"""Financial mutations use a private connection and one durable transaction.
No Telegram/network calls run while SQLite's write lock is held.
"""
from contextlib import asynccontextmanager
import aiosqlite
from config import DATABASE_PATH
from database.repo import _now


@asynccontextmanager
async def transaction():
    async with aiosqlite.connect(DATABASE_PATH, isolation_level=None, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            yield db
        except BaseException:
            await db.rollback()
            raise
        else:
            await db.commit()


async def one(db, sql, args=()):
    cur = await db.execute(sql, args)
    row = await cur.fetchone()
    return dict(row) if row else None


async def grant_bonus(db, user_id):
    # Bonuses are exclusively earned by channel membership now.
    return


async def pay(user_id, kind, item_id, amount, receipt=None, receipt_unique_id=None):
    if kind not in ('ad','order','unlock') or not isinstance(amount, int) or amount < 0:
        raise ValueError("To'lov ma'lumotlari noto'g'ri.")
    if kind == 'unlock':
        raise ValueError("So'rov yuborish bepul. Kontakt uchun to'lov olinmaydi.")
    if kind == 'order' and not receipt:
        raise ValueError("Zakaz uchun karta orqali to'lang va chek yuboring. Admin tasdiqlaydi.")
    table = 'ads' if kind == 'ad' else 'orders'
    ptype = {'ad':'AD_PUBLICATION','order':'ORDER_PUBLICATION','unlock':'CONTACT_UNLOCK'}[kind]
    async with transaction() as db:
        setting=await one(db,"SELECT value FROM settings WHERE key='paid_enabled'")
        if setting and setting['value']=='0':
            raise ValueError('Hozir joylashtirish bepul. Menyudan qayta boshlang. Oldin pul o‘tkazgan bo‘lsangiz admin bilan bog‘laning.')
        item = await one(db, f"SELECT * FROM {table} WHERE id=?", (item_id,))
        if not item or item['deleted_at']:
            raise ValueError("Yozuv topilmadi yoki o'chirilgan.")
        if kind == 'unlock':
            if item['status'] != 'PUBLISHED' or item['user_id'] == user_id:
                raise ValueError("Bu zakaz uchun kontakt olib bo'lmaydi.")
            existing = await one(db, "SELECT u.id FROM contact_unlocks u JOIN payments p ON p.id=u.payment_id WHERE u.order_id=? AND u.developer_user_id=? AND (u.approved_at IS NOT NULL OR p.status IN ('WAITING_ADMIN','APPROVED'))", (item_id,user_id))
            if existing:
                raise ValueError("Kontakt allaqachon olingan yoki to'lov tekshirilmoqda.")
        elif item['user_id'] != user_id or item['status'] not in ('WAITING_PAYMENT','WAITING_RECEIPT'):
            raise ValueError("Bu yozuv sizga tegishli emas yoki allaqachon qayta ishlangan.")
        if receipt:
            duplicate = await one(db, "SELECT id FROM payments WHERE (receipt_unique_id=? OR receipt_file_id=?) AND status IN ('WAITING_ADMIN','APPROVED')", (receipt_unique_id,receipt))
            if duplicate:
                raise ValueError("Bu chek allaqachon yuborilgan. Admin bilan bog'laning.")
        if not receipt:
            cur = await db.execute("UPDATE users SET balance=balance-? WHERE telegram_id=? AND balance>=?", (amount,user_id,amount))
            if cur.rowcount != 1:
                raise ValueError("Balansingiz yetarli emas.")
        status = 'WAITING_ADMIN'
        now = _now()
        cur = await db.execute("INSERT INTO payments(user_id,type,ad_id,order_id,amount,status,receipt_file_id,receipt_unique_id,created_at,approved_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (user_id,ptype,item_id if kind=='ad' else None,item_id if kind!='ad' else None,amount,status,receipt,receipt_unique_id,now,None))
        payment_id = cur.lastrowid
        from services.experience import stamp_review,event
        await stamp_review(db,payment_id,"CARD" if receipt else "WALLET")
        await event(db,user_id,"SUBMITTED",f"{kind}:{item_id}",f"submitted:{kind}:{item_id}")
        if kind == 'unlock':
            await db.execute("INSERT INTO contact_unlocks(order_id,developer_user_id,payment_id,created_at,approved_at) VALUES (?,?,?,?,?)", (item_id,user_id,payment_id,now,None))
        else:
            await db.execute(f"UPDATE {table} SET status=? WHERE id=?", ('WAITING_ADMIN',item_id))
        if not receipt and amount > 0:
            await grant_bonus(db,user_id)
        return payment_id


async def moderate(payment_id, admin_id, approve, reason=None):
    async with transaction() as db:
        payment = await one(db,"SELECT * FROM payments WHERE id=?",(payment_id,))
        if not payment or payment['status'] != 'WAITING_ADMIN':
            raise ValueError("Bu to'lov allaqachon qayta ishlangan.")
        if approve and payment['type']=='CONTACT_UNLOCK':
            raise ValueError("Kontakt olish bepul bo'ldi. Eski to'lovni tekshirib, pulni qaytarish masalasini hal qiling va rad eting.")
        table = 'ads' if payment['type']=='AD_PUBLICATION' else 'orders'
        item_id = payment['ad_id'] if table=='ads' else payment['order_id']
        item = await one(db,f"SELECT * FROM {table} WHERE id=?",(item_id,))
        unlock = payment['type']=='CONTACT_UNLOCK'
        if approve and (not item or item['deleted_at'] or item['status'] != ('PUBLISHED' if unlock else 'WAITING_ADMIN')):
            raise ValueError("Yozuv faol emas. To'lovni rad eting va foydalanuvchi bilan bog'laning.")
        now = _now()
        await db.execute("UPDATE payments SET status=?,approved_at=?,approved_by=?,reject_reason=? WHERE id=?",
                         ('APPROVED' if approve else 'REJECTED',now if approve else None,admin_id,reason,payment_id))
        if not approve and payment.get('payment_source')=='WALLET':
            await db.execute('UPDATE users SET balance=balance+? WHERE telegram_id=?',(payment['amount'],payment['user_id']))
            await db.execute('INSERT INTO notifications(user_id,text) VALUES (?,?)',(payment['user_id'],f"E’lon tasdiqlanmadi. {payment['amount']:,} so‘m balansingizga qaytarildi."))
        if unlock:
            if approve:
                cur = await db.execute("UPDATE contact_unlocks SET approved_at=? WHERE payment_id=?",(now,payment_id))
                if cur.rowcount != 1:
                    raise ValueError("Kontakt yozuvi topilmadi yoki takrorlangan.")
        elif item and not item['deleted_at']:
            if approve:
                await db.execute(f"UPDATE {table} SET status='READY_TO_PUBLISH' WHERE id=?",(item_id,))
            elif item['status']=='WAITING_ADMIN':
                column = 'ad_id' if table=='ads' else 'order_id'
                other = await one(db,f"SELECT id FROM payments WHERE {column}=? AND type=? AND status IN ('WAITING_ADMIN','APPROVED')",(item_id,payment['type']))
                if not other:
                    await db.execute(f"UPDATE {table} SET status='REJECTED' WHERE id=?",(item_id,))
        if approve and payment['amount'] > 0:
            await grant_bonus(db,payment['user_id'])
        await db.execute("INSERT INTO admin_actions(admin_id,action_type,target_type,target_id,description) VALUES (?,?, 'payment',?,?)",(admin_id,'APPROVE_PAYMENT' if approve else 'REJECT_PAYMENT',str(payment_id),reason))
        return payment


async def finish_item(kind, item_id, user_id, status, admin=False):
    if kind == 'order':
        from services.applications import close_order
        return await close_order(item_id,user_id,status,admin=admin)
    table = 'ads' if kind=='ad' else 'orders'
    async with transaction() as db:
        item = await one(db,f"SELECT * FROM {table} WHERE id=?",(item_id,))
        if not item or (not admin and item['user_id']!=user_id):
            raise ValueError("Topilmadi.")
        if item['status'] in ('WAITING_ADMIN','READY_TO_PUBLISH','PUBLISHING','PUBLICATION_FAILED','PUBLICATION_REVIEW','PROCESSING'):
            raise ValueError("Avval to'lov yoki kanalga joylash jarayonini yakunlang.")
        if status=='COMPLETED' and item['status']!='PUBLISHED':
            raise ValueError("Faqat kanaldagi yozuvni yakunlash mumkin.")
        pending = await one(db,"SELECT id FROM payments WHERE order_id=? AND type='CONTACT_UNLOCK' AND status='WAITING_ADMIN'",(item_id,)) if kind=='order' else None
        if pending:
            raise ValueError("Bu zakazning kontakt to'lovi tekshirilmoqda.")
        field = 'deleted_at' if status=='DELETED' else 'completed_at'
        await db.execute(f"UPDATE {table} SET status=?,{field}=? WHERE id=?",(status,_now(),item_id))
        return item


async def claim_publication(kind, item_id):
    table = 'ads' if kind=='ad' else 'orders'
    column = 'ad_id' if kind=='ad' else 'order_id'
    ptype = 'AD_PUBLICATION' if kind=='ad' else 'ORDER_PUBLICATION'
    async with transaction() as db:
        paid = await one(db,f"SELECT id FROM payments WHERE {column}=? AND type=? AND status='APPROVED'",(item_id,ptype))
        if not paid:
            await db.execute(f"UPDATE {table} SET status='PUBLICATION_REVIEW' WHERE id=? AND status='READY_TO_PUBLISH'",(item_id,))
            return False
        cur = await db.execute(f"UPDATE {table} SET status='PUBLISHING' WHERE id=? AND status='READY_TO_PUBLISH' AND deleted_at IS NULL",(item_id,))
        return cur.rowcount == 1
