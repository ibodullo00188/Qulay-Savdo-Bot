import logging

from aiogram import F, Router, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import database.repo as repo
import keyboards.keyboards as kb
from states import ComplaintStates

logger = logging.getLogger(__name__)
router = Router(name="user_complaint")


@router.callback_query(F.data.startswith("complaint:start:"))
async def complaint_start(cq: CallbackQuery, state: FSMContext, dispatcher: Dispatcher):
    from services.flow import abandon_pending_flow
    code = cq.data.split(":", 2)[2]
    item = await repo.get_ad_by_code(code) if code.startswith('AD-') else await repo.get_order_by_code(code)
    if not item or item['status']!='PUBLISHED':
        await cq.answer("Bu post endi faol emas.",show_alert=True)
        return
    state = dispatcher.fsm.get_context(bot=cq.bot,chat_id=cq.from_user.id,user_id=cq.from_user.id)
    await abandon_pending_flow(state)
    await state.update_data(target_code=code)
    await state.set_state(ComplaintStates.awaiting_reason)
    await cq.answer()
    try:
        await cq.bot.send_message(
            cq.from_user.id,
            f"⚠️ Shikoyat — {code}\n\nQoidabuzarlik yoki firibgarlik sababini "
            f"yozib yuboring:", reply_markup=kb.cancel_kb())
    except Exception:
        pass


@router.message(ComplaintStates.awaiting_reason, F.text)
async def complaint_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    code = data.get("target_code", "—")
    await state.clear()
    user = await repo.get_or_create_user(message.from_user.id)
    complaint_id = await repo.create_complaint(user["telegram_id"], code, message.text)
    await message.answer("✅ Shikoyatingiz qabul qilindi. Admin ko'rib chiqadi.",
                          reply_markup=kb.main_menu_rb(await repo.is_admin(message.from_user.id)))
    for admin_id in await repo.list_admin_ids():
        try:
            await message.bot.send_message(
                admin_id,
                f"⚠️ SHIKOYAT | {code}\n\nSabab: {message.text}\n\n"
                f"👤 Shikoyatchi: @{message.from_user.username or '—'} "
                f"(id: {message.from_user.id})",
                reply_markup=kb.complaint_admin_kb(complaint_id))
        except Exception:
            logger.warning("Admin (%s) ga shikoyat yuborilmadi", admin_id)
