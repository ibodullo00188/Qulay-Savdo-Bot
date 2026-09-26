"""Channel status is a retriable projection of the authoritative order state."""
import asyncio
import logging
from aiogram.exceptions import TelegramBadRequest
import config
import database.repo as repo
import keyboards.keyboards as kb
from services.locks import get_lock

logger=logging.getLogger(__name__)


def clip_units(text, limit):
    raw=text.encode('utf-16-le')
    return raw[:max(0,limit)*2].decode('utf-16-le',errors='ignore')


def code_entities(text,code):
    from aiogram.types import MessageEntity
    position=text.index(code)
    return [MessageEntity(type='code',offset=len(text[:position].encode('utf-16-le'))//2,length=len(code))]


async def render_order_post(order):
    status = {
        'PUBLISHED':'🟢 OCHIQ — takliflar qabul qilinmoqda',
        'COMPLETION_REVIEW':'⏳ ISH YAKUNI — admin tasdig‘i kutilmoqda',
        'ASSIGNED':'✅ ZAKAZ QABUL QILINGAN — bajaruvchi tanlandi',
        'COMPLETED':'🏁 ISH BAJARILDI — zakaz yakunlangan',
        'DELETED':'🔒 ZAKAZ YOPILGAN',
        'REJECTED':'🔒 ZAKAZ YOPILGAN',
    }.get(order['status'],'🟢 OCHIQ — takliflar qabul qilinmoqda')
    header=f"{status}\n🆔 {order['public_code']}\n\n"
    body=order.get('channel_body')
    if body is None:
        body=order.get('caption') or order.get('text') or ''
        sign=await repo.get_setting('sign_text') or repo.SIGN_DEFAULT
        body=f"{body.strip()}\n\n{sign}"
    limit=1024 if order['message_type']=='PHOTO' else 4096
    room=limit-len(header.encode('utf-16-le'))//2
    if len(body.encode('utf-16-le'))//2>room:
        body=clip_units(body,room-36)+'\n… To‘liq matn botda.'
    return header+body


async def sync_order_post(bot, order_id):
    # Serializing network edits prevents a delayed ASSIGNED edit replacing COMPLETED.
    async with get_lock(f'order-channel:{order_id}'):
        order=await repo.get_order(order_id)
        if not order or not order['channel_sync_pending']:
            return True
        if not order['channel_message_id'] or not config.CHANNEL_ID:
            return False
        status=order['status']
        text=await render_order_post(order)
        markup=kb.order_channel_kb(order['public_code']) if status=='PUBLISHED' else kb.closed_order_channel_kb(order['public_code'],status)
        try:
            if order['message_type']=='PHOTO':
                await bot.edit_message_caption(chat_id=config.CHANNEL_ID,message_id=order['channel_message_id'],caption=text,caption_entities=code_entities(text,order['public_code']),reply_markup=markup)
            else:
                await bot.edit_message_text(chat_id=config.CHANNEL_ID,message_id=order['channel_message_id'],text=text,entities=code_entities(text,order['public_code']),reply_markup=markup)
        except TelegramBadRequest as exc:
            if 'message is not modified' not in str(exc).lower():
                logger.warning('Kanal holatini yangilab bo‘lmadi: %s',order['public_code'])
                return False
        except Exception:
            logger.warning('Kanal bilan aloqa xatosi: %s',order['public_code'])
            return False
        await repo.cas_update_status('orders',order_id,status,channel_sync_pending=0)
        return True


async def flush_order_posts(bot):
    from database.db import get_db
    db=await get_db()
    cur=await db.execute('SELECT id FROM orders WHERE channel_sync_pending=1 ORDER BY id LIMIT 100')
    for row in await cur.fetchall():
        await sync_order_post(bot,row['id'])


async def delivery_worker(bot):
    from services.notifications import flush_notifications
    while True:
        try:
            from services.maintenance import guarded_work
            async with guarded_work():
                from services.upgrade import hourly_reminders
                from services.experience import alerts
                await alerts(bot)
                await hourly_reminders(bot)
                await flush_order_posts(bot)
                await flush_notifications(bot)
        except Exception:
            logger.exception('Bildirishnomalarni yetkazishda xato')
        await asyncio.sleep(30)
