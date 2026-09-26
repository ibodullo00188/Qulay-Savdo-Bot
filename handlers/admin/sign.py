"""✍️ Imzo matni — admin tahrirlaydi; kanal postlari ostiga qo'shiladi."""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import database.repo as repo
import keyboards.keyboards as kb
from states import SettingsStates

logger = logging.getLogger(__name__)
router = Router(name="admin_sign")


@router.callback_query(F.data == "admin:sign")
async def cb_sign_start(cq: CallbackQuery, state: FSMContext):
    if not await repo.is_admin(cq.from_user.id):
        await cq.answer("⛔️", show_alert=True)
        return
    current = await repo.get_setting("sign_text") or repo.SIGN_DEFAULT
    await state.set_state(SettingsStates.awaiting_sign_text)
    await cq.answer()
    await cq.message.edit_text(
        f"✍️ Hozirgi imzo:\n\n{current}\n\nYangi imzo matnini yuboring:",
        reply_markup=kb.cancel_kb())


@router.message(SettingsStates.awaiting_sign_text, F.text)
async def msg_sign_text(message: Message, state: FSMContext):
    if not await repo.is_admin(message.from_user.id):
        return
    if not message.text.strip() or len(message.text.encode("utf-16-le"))//2 > 400:
        await message.answer("Imzo 1–400 belgidan iborat bo‘lsin.")
        return
    await repo.set_sign_text(message.text)
    await state.clear()
    await message.answer(
        "✅ Imzo yangilandi.\n\nEndi u barcha yangi e'lon va zakazlarga "
        "pastdan qo'shilib kanalga chiqadi.",
        reply_markup=kb.admin_sections_kb())
