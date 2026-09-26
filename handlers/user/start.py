import logging

from aiogram import F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import database.repo as repo
import keyboards.keyboards as kb
from services.codes import decode_referral
from services.flow import abandon_pending_flow

logger = logging.getLogger(__name__)
router = Router(name="user_start")


async def _greet(message: Message, user_id_for_admin_check: int):
    is_admin = await repo.is_admin(user_id_for_admin_check)
    text = await repo.get_setting("start_text") or "🚀 Qulay Savdo Bot"
    await message.answer(text, reply_markup=kb.main_menu_rb(is_admin=is_admin))


@router.message(CommandStart(deep_link=True))
async def on_start_deep(message: Message, state: FSMContext):
    await abandon_pending_flow(state)
    payload = message.text.split(maxsplit=1)[-1]
    ref_id = decode_referral(payload)

    existing = await repo.get_user(message.from_user.id)
    is_new = existing is None

    if ref_id == message.from_user.id:
        ref_id = None  # o'zini o'zi taklif qilolmaydi

    user = await repo.get_or_create_user(
        message.from_user.id, message.from_user.username,
        message.from_user.first_name, message.from_user.last_name,
        referred_by=ref_id if is_new else None)

    await repo.update_user(message.from_user.id,
                            username=message.from_user.username,
                            first_name=message.from_user.first_name,
                            last_name=message.from_user.last_name)

    if payload.startswith('order_'):
        from handlers.user.applications import show_order
        await show_order(message.bot,message.chat.id,message.from_user.id,payload.removeprefix('order_'))
        return
    if payload.startswith('complaint_'):
        from states import ComplaintStates
        code = payload.removeprefix('complaint_')
        item = await repo.get_ad_by_code(code) if code.startswith('AD-') else await repo.get_order_by_code(code)
        if not item or item['status']!='PUBLISHED':
            await message.answer("Bu post endi faol emas.")
            return
        await state.update_data(target_code=code)
        await state.set_state(ComplaintStates.awaiting_reason)
        await message.answer(f"⚠️ {code}: shikoyat sababini yozing.",reply_markup=kb.cancel_kb())
        return

    await _greet(message, message.from_user.id)



@router.message(CommandStart())
async def on_start(message: Message, state: FSMContext):
    await abandon_pending_flow(state)
    if message.from_user is None:
        return
    await repo.get_or_create_user(
        message.from_user.id, message.from_user.username,
        message.from_user.first_name, message.from_user.last_name)
    await _greet(message, message.from_user.id)


@router.message(Command("menu"))
@router.message(F.text == kb.BTN_HOME)
async def on_menu(message: Message, state: FSMContext):
    # Foydalanuvchi to'lov/chek bosqichida turib "🏠 Bosh menyu" bossa ham,
    # yarim qolgan yozuv CANCELLED qilinishi va FSM state tozalanishi kerak.
    await abandon_pending_flow(state)
    is_admin = await repo.is_admin(message.from_user.id)
    await message.answer("🏠 Bosh menyu", reply_markup=kb.main_menu_rb(is_admin=is_admin))


@router.message(F.text == kb.BTN_RULES)
async def on_rules(message: Message):
    text = await repo.get_setting("rules_text") or "ℹ️ Qoidalar hozircha kiritilmagan."
    await message.answer(text)


@router.message(Command("cancel"))
@router.message(F.text == kb.BTN_CANCEL)
async def on_cancel(message: Message, state: FSMContext):
    await abandon_pending_flow(state)
    is_admin = await repo.is_admin(message.from_user.id)
    await message.answer("Jarayondan chiqdingiz. Yuborilgan post matni qoralamalarda saqlanadi.", reply_markup=kb.main_menu_rb(is_admin=is_admin))
