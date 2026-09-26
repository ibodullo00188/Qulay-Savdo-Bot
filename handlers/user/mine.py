import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

import database.repo as repo
import keyboards.keyboards as kb
from services.channel import delete_channel_post
from services.transactions import finish_item
from services.order_channel import sync_order_post
from services.notifications import flush_notifications

logger = logging.getLogger(__name__)
router = Router(name="user_mine")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def _send_list(message: Message, kind: str, user_id=None):
    user = await repo.get_or_create_user(user_id if user_id is not None else message.from_user.id)
    items = (await repo.list_ads_by_user(user["telegram_id"]) if kind == "ad"
             else await repo.list_orders_by_user(user["telegram_id"]))
    title = "🛍 Mening e'lonlarim" if kind == "ad" else "📦 Mening zakazlarim"
    if not items:
        await message.answer(f"📭 {title}: hozircha bo'sh.")
        return
    markup = kb.items_list_kb(items, kind)
    await message.answer(f"{title} ({len(items)} ta):", reply_markup=markup)


@router.message(F.text == kb.BTN_MY_ADS)
async def my_ads(message: Message):
    await _send_list(message, "ad")


@router.message(F.text == kb.BTN_MY_ORDERS)
async def my_orders(message: Message):
    await _send_list(message, "order")


async def _get_item(kind, item_id):
    return await repo.get_ad(item_id) if kind == "ad" else await repo.get_order(item_id)


async def _update_item(kind, item_id, **fields):
    if kind == "ad":
        await repo.update_ad(item_id, **fields)
    else:
        await repo.update_order(item_id, **fields)


@router.callback_query(F.data.regexp(r"^(ad|order):view:(\d+)$"))
async def item_view(cq: CallbackQuery):
    kind, item_id = cq.data.split(":")[0], int(cq.data.split(":")[2])
    item = await _get_item(kind, item_id)
    await cq.answer()
    if not item or item["user_id"] != cq.from_user.id:
        await cq.message.answer("❌ Topilmadi.")
        return
    label = kb.STATUS_LABELS.get(item["status"], item["status"])
    body = item.get("caption") or item.get("text") or ""
    text = (f"🆔 {item['public_code']}\n📌 Holat: {label}\n\n{body}")
    await cq.message.answer(
        text, reply_markup=kb.item_detail_kb(kind, item_id, item["status"]))


@router.callback_query(F.data.regexp(r"^(ad|order):back_list$"))
async def item_back(cq: CallbackQuery):
    kind = cq.data.split(":")[0]
    await cq.answer()
    await _send_list(cq.message, kind, cq.from_user.id)


@router.callback_query(F.data.regexp(r"^(ad|order):delete:(\d+)$"))
async def item_delete(cq: CallbackQuery):
    kind, item_id = cq.data.split(":")[0], int(cq.data.split(":")[2])
    item = await _get_item(kind, item_id)
    if not item or item["user_id"] != cq.from_user.id:
        await cq.answer("❌ Topilmadi.", show_alert=True)
        return
    try:
        item = await finish_item(kind,item_id,cq.from_user.id,'DELETED')
    except ValueError as exc:
        await cq.answer(str(exc),show_alert=True)
        return
    if kind=='order':
        await sync_order_post(cq.bot,item_id)
        await flush_notifications(cq.bot)
    elif item.get('channel_message_id'):
        deleted = await delete_channel_post(cq.bot,item['channel_message_id'])
        if not deleted:
            await cq.message.answer("⚠️ Kanal postini o'chirib bo'lmadi. Admin bilan bog'laning.")
    await cq.answer("🗑 O'chirildi")
    await cq.message.edit_text("🔒 Zakaz yopildi." if kind=="order" else "🗑 O‘chirildi.")


@router.callback_query(F.data.regexp(r"^(ad|order):complete:(\d+)$"))
async def item_complete(cq: CallbackQuery):
    kind, item_id = cq.data.split(":")[0], int(cq.data.split(":")[2])
    item = await _get_item(kind, item_id)
    if not item or item["user_id"] != cq.from_user.id:
        await cq.answer("❌ Topilmadi.", show_alert=True)
        return
    try:
        item = await finish_item(kind,item_id,cq.from_user.id,'COMPLETED')
    except ValueError as exc:
        await cq.answer(str(exc),show_alert=True)
        return
    if kind=='order':
        await sync_order_post(cq.bot,item_id)
        await flush_notifications(cq.bot)
    elif item.get('channel_message_id'):
        deleted = await delete_channel_post(cq.bot,item['channel_message_id'])
        if not deleted:
            await cq.message.answer("⚠️ Kanal postini o'chirib bo'lmadi. Admin bilan bog'laning.")
    await cq.answer("✅ Bajarildi deb belgilandi")
    if kind=='order':
        await cq.message.edit_text("🏁 Ish bajarilgan deb belgilandi. Zakaz yakunlandi.",reply_markup=kb.item_detail_kb(kind,item_id,'COMPLETED'))
        return
    label = "E'lon" if kind == "ad" else "Zakaz"
    await cq.message.edit_text(f"✅ {label} bajarildi deb belgilandi va kanaldan olib "
                                f"tashlash so‘raldi.")
