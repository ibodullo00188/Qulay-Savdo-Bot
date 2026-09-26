"""⚠️ Shikoyatlarni admin tomonidan ko'rib chiqish (post o'chirish / userni
bloklash / e'tiborsiz qoldirish)."""
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

import database.repo as repo
import keyboards.keyboards as kb
from services.channel import delete_channel_post

logger = logging.getLogger(__name__)
router = Router(name="admin_receipts")


async def _guard(cq) -> bool:
    return await repo.is_admin(cq.from_user.id)


async def _find_by_code(code):
    ad = await repo.get_ad_by_code(code)
    if ad:
        return "ad", ad
    order = await repo.get_order_by_code(code)
    if order:
        return "order", order
    return None, None


@router.callback_query(F.data.regexp(r"^admin:complaints(:next:(\d+))?$"))
async def admin_complaints(cq: CallbackQuery):
    if not await _guard(cq):
        await cq.answer("⛔️", show_alert=True)
        return
    parts = cq.data.split(":")
    offset = int(parts[-1]) if len(parts) > 2 else 0
    complaints = await repo.list_complaints("OPEN", limit=1, offset=offset)
    total = await repo.count_complaints("OPEN")
    await cq.answer()
    if not complaints:
        text = "✅ Ochiq shikoyatlar yo'q." if offset == 0 else \
            "✅ Boshqa ochiq shikoyat yo'q."
        await cq.message.edit_text(text, reply_markup=kb.back_to_admin_kb())
        return
    c = complaints[0]
    await cq.message.edit_text(
        f"⚠️ Ochiq shikoyatlar: {total} ta ({offset + 1}-{offset + 1})\n\n"
        f"🆔 {c['target_code']}\nSabab: {c['reason']}\n"
        f"Shikoyatchi: {c['user_id']}",
        reply_markup=kb.complaints_pagination_kb(offset, total, c["id"]))


@router.callback_query(F.data.startswith("complaint:delete_post:"))
async def complaint_delete_post(cq: CallbackQuery):
    if not await _guard(cq):
        await cq.answer("⛔️", show_alert=True)
        return
    complaint_id = int(cq.data.split(":")[2])
    complaint = await repo.get_complaint(complaint_id)
    await cq.answer()
    if not complaint:
        await cq.message.edit_text("Topilmadi.")
        return
    kind, item = await _find_by_code(complaint["target_code"])
    if kind=='order' and item:
        from services.applications import close_order
        from services.order_channel import sync_order_post
        from services.notifications import flush_notifications
        try:
            await close_order(item['id'],cq.from_user.id,'REJECTED',admin=True)
        except ValueError as exc:
            await cq.message.answer(str(exc))
            return
        await sync_order_post(cq.bot,item['id'])
        await flush_notifications(cq.bot)
    elif item and item.get('channel_message_id'):
        deleted=await delete_channel_post(cq.bot,item['channel_message_id'])
        if not deleted:
            await cq.message.answer("Kanal postini o‘chirib bo‘lmadi. Shikoyat ochiq qoldi.")
            return
        await repo.cas_update_status('ads',item['id'],'PUBLISHED',status='REJECTED')
    await repo.update_complaint(complaint_id, status="RESOLVED")
    await repo.log_admin_action(cq.from_user.id, "COMPLAINT_DELETE_POST",
                                 "complaint", str(complaint_id))
    await cq.message.edit_text(f"🗑 {complaint['target_code']} bo‘yicha chora ko‘rildi.")


@router.callback_query(F.data.startswith("complaint:block_user:"))
async def complaint_block_user(cq: CallbackQuery):
    if not await _guard(cq):
        await cq.answer("⛔️", show_alert=True)
        return
    complaint_id = int(cq.data.split(":")[2])
    complaint = await repo.get_complaint(complaint_id)
    await cq.answer()
    if not complaint:
        await cq.message.edit_text("Topilmadi.")
        return
    kind, item = await _find_by_code(complaint["target_code"])
    if item:
        await repo.set_blocked(item["user_id"], True)
        await repo.update_complaint(complaint_id, status="RESOLVED")
        await repo.log_admin_action(cq.from_user.id, "COMPLAINT_BLOCK_USER",
                                     "complaint", str(complaint_id))
        await cq.message.edit_text(
            f"🚫 Foydalanuvchi (id: {item['user_id']}) bloklandi.")
    else:
        await cq.message.edit_text("Nishon egasi topilmadi.")


@router.callback_query(F.data.startswith("complaint:dismiss:"))
async def complaint_dismiss(cq: CallbackQuery):
    if not await _guard(cq):
        await cq.answer("⛔️", show_alert=True)
        return
    complaint_id = int(cq.data.split(":")[2])
    await repo.update_complaint(complaint_id, status="DISMISSED")
    await cq.answer("✅")
    await cq.message.edit_text("✅ Shikoyat e'tiborsiz qoldirildi.")
