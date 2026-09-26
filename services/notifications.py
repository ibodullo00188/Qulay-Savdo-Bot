import asyncio
import logging
from database.db import get_db
from database.repo import _now

logger = logging.getLogger(__name__)
_delivery_lock = asyncio.Lock()


async def flush_notifications(bot):
    async with _delivery_lock:
        db = await get_db()
        cur = await db.execute("SELECT * FROM notifications WHERE sent_at IS NULL ORDER BY id LIMIT 100")
        for row in await cur.fetchall():
            try:
                if row['event_type']=='MATCHED_ORDER':
                    from services.transactions import one
                    import keyboards.keyboards as kb
                    order=await one(db,"SELECT * FROM orders WHERE id=? AND status='PUBLISHED'",(row['entity_id'],))
                    sub=await one(db,'SELECT 1 FROM subscriptions WHERE user_id=? AND category=?',(row['user_id'],order['category'])) if order else None
                    if order and sub:
                        await bot.send_message(row['user_id'],f"🔔 Siz tanlagan yo‘nalishda yangi zakaz: {order['public_code']}\nTaklif yuborish bepul.",reply_markup=kb.ipb([(f"order:apply:{order['public_code']}",'Zakazni ko‘rish'),('xp:subscriptions','Bildirishnomalarni sozlash')]))
                elif row['event_type']:
                    from services.application_views import notification_card
                    card = await notification_card(row['event_type'],row['entity_id'])
                    if card:
                        text,markup = card
                        await bot.send_message(row['user_id'],text,parse_mode='HTML',reply_markup=markup)
                else:
                    await bot.send_message(row['user_id'],row['text'])
            except Exception:
                logger.warning("Bildirishnoma yuborilmadi: %s",row['user_id'])
                continue
            await db.execute("UPDATE notifications SET sent_at=? WHERE id=?",(_now(),row['id']))
