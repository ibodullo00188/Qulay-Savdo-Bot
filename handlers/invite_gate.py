from urllib.parse import quote
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton
from database.db import get_db
import database.repo as repo
import keyboards.keyboards as kb
from services import invite_gate as gate

router=Router(name='invite_gate')
router.callback_query.filter(F.message.chat.type=='private')

class InviteSettings(StatesGroup):
    count=State()


async def admin_view(message):
    o=await gate.settings()
    mode={'once':'Bir marta','per_post':'Har bir yangi post uchun'}.get(o['mode'],'Tanlanmagan')
    text=f'🤝 Do‘st taklif qilib bepul joylashtirish\n\nHolat: {"Yoqilgan" if o["enabled"] else "O‘chirilgan"}\nTalab: {o["count"] or "Belgilanmagan"} ta do‘st\nRejim: {mode}\n\n'
    text+='Pullik tizim o‘chirilganda ishlaydi. E’lon va zakazlarga bir xil qo‘llanadi. '
    text+='Bir martalik shartni bajarganlar huquqini saqlaydi. Har post rejimida faqat avval ishlatilmagan takliflar hisoblanadi.'
    if o['paid']:text+='\n\n⏸ Hozir pullik tizim yoqilgan; taklif sharti qo‘llanmaydi.'
    await message.answer(text,reply_markup=kb.ipb([
        (f'ig:enabled:{int(not o["enabled"])}','⏹ O‘chirish' if o['enabled'] else '▶️ Yoqish'),
        ('ig:count','🔢 Do‘stlar soni'),('ig:mode:once','1️⃣ Bir marta'),
        ('ig:mode:per_post','🔁 Har bir post uchun'),('admin:sections','⬅️ Admin panel')]))


@router.callback_query(F.data=='ig:admin')
async def open_admin(cq: CallbackQuery,state: FSMContext):
    if not await repo.is_admin(cq.from_user.id):await cq.answer('Ruxsat yo‘q.',show_alert=True);return
    await state.clear();await cq.answer();await admin_view(cq.message)


@router.callback_query(F.data.regexp(r'^ig:(enabled:[01]|mode:(once|per_post))$'))
async def option(cq: CallbackQuery):
    if not await repo.is_admin(cq.from_user.id):await cq.answer('Ruxsat yo‘q.',show_alert=True);return
    _,key,value=cq.data.split(':')
    try:await gate.change(cq.from_user.id,key,value)
    except ValueError as exc:await cq.answer(str(exc),show_alert=True);return
    await cq.answer('Saqlandi');await admin_view(cq.message)


@router.callback_query(F.data=='ig:count')
async def count_begin(cq: CallbackQuery,state: FSMContext):
    if not await repo.is_admin(cq.from_user.id):await cq.answer('Ruxsat yo‘q.',show_alert=True);return
    await state.clear();await state.set_state(InviteSettings.count);await cq.answer()
    await cq.message.answer('Nechta do‘st taklif qilish kerak? 1–10000 oralig‘ida son yuboring.\nBekor qilish: /cancel')


@router.message(InviteSettings.count,Command('cancel','admin','menu','start'))
async def cancel_count(message: Message,state: FSMContext):
    if not await repo.is_admin(message.from_user.id):return
    await state.clear();await admin_view(message)


@router.message(InviteSettings.count)
async def count_value(message: Message,state: FSMContext):
    if not await repo.is_admin(message.from_user.id):return
    try:await gate.change(message.from_user.id,'count',(message.text or '').strip())
    except ValueError as exc:await message.answer(str(exc));return
    await state.clear();await admin_view(message)


async def prompt(message,uid,kind,item_id):
    post=await (repo.get_ad(item_id) if kind=='ad' else repo.get_order(item_id))
    if not post or post['user_id']!=uid or post.get('deleted_at'):
        await message.answer('Qoralama topilmadi.');return
    o=await gate.settings()
    count,granted=await gate.progress(await get_db(),uid,o)
    try:link=await gate.invite_link(message.bot,uid)
    except Exception:
        await message.answer('Taklif havolasini yaratib bo‘lmadi. Qoralamangiz saqlandi. Admin botning kanaldagi taklif havolasi yaratish huquqini tekshirsin.');return
    text=f'📝 Postingiz tayyor va qoralamada saqlangan!\n\nKanalga shaxsiy havolangiz orqali {o["count"]} ta do‘st taklif qiling.\n✅ Hisoblangan: {min(count,o["count"])}/{o["count"]}\n👥 Yana kerak: {max(0,o["count"]-count)} ta\n\n'
    text+=('Bu shart bir marta bajariladi. Keyingi postlarda qayta talab qilinmaydi.' if o['mode']=='once' else 'Har yangi e’lon yoki zakaz uchun yangi do‘stlar kerak. Ushbu postga ishlatilgan taklif keyingi postda qayta hisoblanmaydi.')
    text+='\n\n'+link+'\n\nHavolani do‘stlaringizga yuboring. Ular kanalga qo‘shilgach pastdagi tugmani bosing. Kanalga kirishi tekshiriladi; botni ochish shart emas.'
    markup=kb.ipb([(f'xp:submit:{kind}:{item_id}:{post["draft_version"]}','✅ Tekshirish va davom etish'),('xp:save','📝 Keyin davom ettirish')])
    markup.inline_keyboard.insert(0,[InlineKeyboardButton(text='📲 Taklif havolasini ulashish',url='https://t.me/share/url?url='+quote(link,safe=''))])
    await message.answer(text,reply_markup=markup)
