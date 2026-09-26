"""Private admin backup export and upload/preview/confirm restore flow."""
import asyncio
import io
from zoneinfo import ZoneInfo
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
import database.repo as repo
import keyboards.keyboards as kb
from services import backup

router = Router()


class BackupRestore(StatesGroup):
    waiting_file = State()
    confirmation = State()


async def permitted(user_id, chat):
    return chat.type == 'private' and await repo.is_admin(user_id)


async def send_backup(message, bot_id, caption='✅ Backup tayyor. Faylni saqlab qo‘ying; tiklashda aynan shu ZIP fayl kerak bo‘ladi.'):
    payload, name = await asyncio.to_thread(backup.create_archive, bot_id)
    await message.answer_document(BufferedInputFile(payload, filename=name), caption=caption)


@router.callback_query(F.data == 'backup:create')
async def export_button(cq: CallbackQuery):
    if not cq.message or not await permitted(cq.from_user.id, cq.message.chat):
        await cq.answer('Ruxsat yo‘q.', show_alert=True)
        return
    await cq.answer('Backup tayyorlanmoqda…')
    try:
        await send_backup(cq.message, cq.bot.id)
    except backup.BackupError as exc:
        await cq.message.answer(str(exc))


@router.message(Command('backup'))
async def export_command(message: Message):
    if await permitted(message.from_user.id, message.chat):
        try:
            await send_backup(message, message.bot.id)
        except backup.BackupError as exc:
            await message.answer(str(exc))


async def begin(message, user_id, state):
    backup.discard_user(user_id)
    await state.clear()
    await state.set_state(BackupRestore.waiting_file)
    await message.answer('♻️ Tiklamoqchi bo‘lgan backup ZIP faylini hujjat sifatida yuboring.\n\n'
        'Fayl tekshirilgach, sana va yozuvlar sonini ko‘rasiz. Tasdiqlamaguningizcha baza o‘zgarmaydi.\n'
        'Bekor qilish: /cancel', reply_markup=kb.ipb([('backup:cancel','❌ Bekor qilish')]))


@router.callback_query(F.data == 'backup:restore')
async def restore_button(cq: CallbackQuery, state: FSMContext):
    if not cq.message or not await permitted(cq.from_user.id, cq.message.chat):
        await cq.answer('Ruxsat yo‘q.', show_alert=True)
        return
    await cq.answer()
    await begin(cq.message, cq.from_user.id, state)


@router.message(Command('restore_backup'))
async def restore_command(message: Message, state: FSMContext):
    if await permitted(message.from_user.id, message.chat):
        await begin(message, message.from_user.id, state)


@router.callback_query(F.data == 'backup:cancel')
async def cancel_button(cq: CallbackQuery, state: FSMContext):
    if not cq.message or not await permitted(cq.from_user.id, cq.message.chat):
        await cq.answer('Ruxsat yo‘q.', show_alert=True)
        return
    backup.discard_user(cq.from_user.id)
    await state.clear()
    await cq.answer('Bekor qilindi.')
    await cq.message.answer('Tiklash bekor qilindi.', reply_markup=kb.admin_sections_kb())


@router.message(StateFilter(BackupRestore.waiting_file, BackupRestore.confirmation), Command('cancel','admin','start','menu'))
async def cancel_command(message: Message, state: FSMContext):
    if await permitted(message.from_user.id, message.chat):
        backup.discard_user(message.from_user.id)
        await state.clear()
        await message.answer('Tiklash bekor qilindi.', reply_markup=kb.admin_sections_kb())


@router.message(BackupRestore.waiting_file, F.document)
async def receive_file(message: Message, state: FSMContext):
    if not await permitted(message.from_user.id, message.chat):
        return
    document = message.document
    if not (document.file_name or '').lower().endswith('.zip'):
        await message.answer('Bot yaratgan .zip backup faylini yuboring.')
        return
    if not document.file_size or document.file_size > backup.MAX_ARCHIVE:
        await message.answer('Backup hajmi 19 MiB dan oshmasin.')
        return
    try:
        # Bound the actual bytes too, not just Telegram's declared size.
        class LimitedBuffer(io.BytesIO):
            def write(self, data):
                if self.tell() + len(data) > backup.MAX_ARCHIVE:
                    raise backup.BackupError('Backup hajmi 19 MiB dan oshdi.')
                return super().write(data)
        buffer = LimitedBuffer()
        await message.bot.download(document, destination=buffer)
        token, details = await asyncio.to_thread(backup.stage_archive, buffer.getvalue(), message.bot.id, message.from_user.id)
    except backup.BackupError as exc:
        await message.answer(str(exc))
        return
    except Exception:
        await message.answer('Fayl yuklanmadi. Qayta yuboring.')
        return
    await state.set_state(BackupRestore.confirmation)
    c = details['counts']
    date = details['created_at'].astimezone(ZoneInfo('Asia/Tashkent')).strftime('%d.%m.%Y %H:%M')
    await message.answer(f'📦 Backup tekshirildi.\nSana: {date} (Toshkent)\n'
        f'Foydalanuvchilar: {c["users"]}\nZakazlar: {c["orders"]}\nE’lonlar: {c["ads"]}\nTo‘lovlar: {c["payments"]}\n\n'
        'Joriy baza shu nusxa bilan almashtiriladi. Backupdan keyingi o‘zgarishlar qaytariladi. '
        'Kanaldagi postlar orqaga qaytmaydi. Avval joriy bazaning nusxasi sizga yuboriladi.\n\n'
        '10 daqiqa ichida tasdiqlang.', reply_markup=kb.ipb([
            (f'backup:confirm:{token}','✅ Backupni tiklash'),('backup:cancel','❌ Bekor qilish')]))


@router.message(StateFilter(BackupRestore.waiting_file, BackupRestore.confirmation))
async def expected_file(message: Message):
    if await permitted(message.from_user.id, message.chat):
        await message.answer('Backup ZIP faylini yuboring yoki tasdiqlash tugmasini bosing. Bekor qilish: /cancel')


@router.callback_query(F.data.startswith('backup:confirm:'))
async def confirm_restore(cq: CallbackQuery, state: FSMContext):
    if not cq.message or not await permitted(cq.from_user.id, cq.message.chat):
        await cq.answer('Ruxsat yo‘q.', show_alert=True)
        return
    token = cq.data.split(':',2)[-1]
    try:
        backup.get_staged(token, cq.from_user.id)
    except backup.BackupError as exc:
        await cq.answer(str(exc), show_alert=True)
        return
    await cq.answer('Tiklanmoqda…')
    try:
        await send_backup(cq.message, cq.bot.id, '📦 Tiklashdan OLDINGI baza. Zarur bo‘lsa shu fayl bilan ortga qaytishingiz mumkin.')
    except Exception:
        await cq.message.answer('Joriy backup yuborilmadi. Baza o‘zgartirilmadi; qayta urinib ko‘ring.')
        return
    try:
        await backup.restore_staged(token, cq.from_user.id)
    except Exception:
        await cq.message.answer('Tiklash bajarilmadi. Xatolik tafsilotlarini server logidan tekshiring.')
        import logging
        logging.getLogger(__name__).exception('Backup restore failed')
        return
    await state.clear()
    await cq.message.answer('✅ Backup tiklandi. Bot tiklangan baza bilan ishlayapti.\n'
        'Balanslar va zakazlarni tekshiring. Eski navbatdagi xabarlar qayta yuborilmadi; '
        'noaniq kanal nashrlari admin tekshiruviga qoldirildi.', reply_markup=kb.admin_sections_kb())
