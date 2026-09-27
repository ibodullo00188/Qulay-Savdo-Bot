"""📣 Reklama — admin barcha foydalanuvchilarga matn yoki rasm jo'natadi."""
import asyncio
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import database.repo as repo
import keyboards.keyboards as kb
from states import BroadcastStates

logger = logging.getLogger(__name__)
router = Router(name="admin_broadcast")

# Bir vaqtning o'zida faqat bitta broadcast yuborilishi uchun oddiy flag.
# "bcast:confirm" ikki marta tez bosilsa (yoki ikkita admin bir vaqtda
# bossa), hammaga ikki marta xabar ketib qolishining oldini oladi.
_broadcast_in_progress = False


@router.callback_query(F.data == "admin:broadcast")
async def cb_broadcast_start(cq: CallbackQuery, state: FSMContext):
    if not await repo.is_admin(cq.from_user.id):
        await cq.answer("⛔️", show_alert=True)
        return
    await state.set_state(BroadcastStates.awaiting_content)
    await cq.answer()
    await cq.message.edit_text(
        "📣 Reklama matnini (yoki rasm + izoh) yuboring.",
        reply_markup=kb.cancel_kb())


@router.message(StateFilter(BroadcastStates.awaiting_content))
async def msg_broadcast_content(message: Message, state: FSMContext):
    if not await repo.is_admin(message.from_user.id):
        return
    if message.photo:
        await state.update_data(photo=message.photo[-1].file_id,
                                 text=message.caption or "")
    else:
        await state.update_data(photo=None, text=message.text or "")
    await message.answer(
        "📤 Ushbu xabarni BARCHA foydalanuvchilarga yuborishni tasdiqlaysizmi?",
        reply_markup=kb.broadcast_kb())


@router.callback_query(F.data == "bcast:cancel")
async def bcast_cancel(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await cq.answer("Bekor qilindi")
    await cq.message.answer("❌ Reklama yuborish bekor qilindi.",
                                reply_markup=kb.admin_sections_kb())


@router.callback_query(F.data == "bcast:confirm")
async def bcast_confirm(cq: CallbackQuery, state: FSMContext):
    global _broadcast_in_progress
    if not await repo.is_admin(cq.from_user.id):
        await cq.answer("⛔️", show_alert=True)
        return

    # Tugmani ikki marta tez bosish (yoki bir vaqtda ikkita admin bosishi)
    # orqali xabar hammaga ikki marta ketib qolishining oldini olish.
    if _broadcast_in_progress:
        await cq.answer("⏳ Reklama allaqachon yuborilmoqda, kuting.", show_alert=True)
        return
    _broadcast_in_progress = True
    try:
        data = await state.get_data()
        await state.clear()
        photo = data.get("photo")
        text = data.get("text") or ""
        if not photo and not text:
            await cq.answer("Yuboriladigan matn topilmadi.", show_alert=True)
            return
        await cq.answer("📤 Yuborilmoqda...")
        await cq.message.edit_text("📤 Yuborish boshlandi, biroz kuting...")

        users = await repo.list_users(limit=100000)
        sent, failed, blocked = 0, 0, 0
        for u in users:
            if u.get("is_blocked"):
                continue
            try:
                if photo:
                    await cq.bot.send_photo(u["telegram_id"], photo, caption=text)
                else:
                    await cq.bot.send_message(u["telegram_id"], text)
                sent += 1
            except TelegramForbiddenError:
                # Botni to'xtatish admin blokiga aylantirilmaydi: foydalanuvchi
                # keyinroq /start bilan qaytishi mumkin.
                blocked += 1
                failed += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)  # Telegram flood-limitiga tushmaslik uchun

        await repo.log_admin_action(
            cq.from_user.id, "BROADCAST", None, None,
            f"yuborildi: {sent}, xato: {failed}, yangi bloklangan: {blocked}")
        await cq.message.answer(
            f"✅ Reklama yuborildi!\n\n📨 Muvaffaqiyatli: {sent}\n❌ Xato: {failed}"
            f"\n🚫 Botga xabar yuborish yopilgan: {blocked}",
            reply_markup=kb.admin_sections_kb())
    finally:
        _broadcast_in_progress = False
