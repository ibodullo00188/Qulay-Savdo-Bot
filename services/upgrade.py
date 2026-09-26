"""Persistent channel rewards, agreement lifecycle and moderation reminders."""
import time
import logging
from database.db import get_db, _add_column_if_missing
import database.repo as repo
from services.transactions import transaction, one
from services.applications import enqueue

log = logging.getLogger(__name__)

async def migrate(db):
    for name, ddl in [('reopen_count','INTEGER NOT NULL DEFAULT 0'),('owner_done','INTEGER NOT NULL DEFAULT 0'),('worker_done','INTEGER NOT NULL DEFAULT 0')]:
        await _add_column_if_missing(db,'orders',name,f'{name} {ddl}')
    await _add_column_if_missing(db,'users','bonus_penalty','bonus_penalty INTEGER NOT NULL DEFAULT 0')
    await db.executescript('''
        CREATE TABLE IF NOT EXISTS code_sequences(prefix TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS referral_links(link TEXT PRIMARY KEY,owner_id INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS channel_members(user_id INTEGER PRIMARY KEY, present INTEGER NOT NULL,
            referrer_id INTEGER, reward INTEGER NOT NULL DEFAULT 0, credited INTEGER NOT NULL DEFAULT 0,
            last_event INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS reminder_delivery(admin_id INTEGER, item_key TEXT, sent_at INTEGER NOT NULL,
            PRIMARY KEY(admin_id,item_key));
    ''')

    await _add_column_if_missing(db,'channel_members','reversal_skipped','reversal_skipped INTEGER NOT NULL DEFAULT 0')
    # Legacy penalty data is no longer collectible under the new policy.
    await db.execute('UPDATE users SET bonus_penalty=0 WHERE bonus_penalty!=0')

async def enabled(db):
    money=await one(db,"SELECT value FROM settings WHERE key='paid_enabled'")
    bonus=await one(db,"SELECT value FROM settings WHERE key='referral_enabled'")
    return (not money or money['value']=='1') and (not bonus or bonus['value']=='1')

async def membership(user_id,present,link=None,event_id=0,is_bot=False):
    if is_bot:
        return
    async with transaction() as db:
        old=await one(db,'SELECT * FROM channel_members WHERE user_id=?',(user_id,))
        if old and event_id and old['last_event']>=event_id:
            return
        if old and bool(old['present'])==present:
            await db.execute('UPDATE channel_members SET last_event=? WHERE user_id=?',(event_id,user_id))
            return
        if old is None:
            invitation=await one(db,'SELECT owner_id FROM referral_links WHERE link=?',(link,)) if link and present else None
            ref=invitation['owner_id'] if invitation else None
            if ref==user_id: ref=None
            amount=max(0,await repo.get_int_setting('referral_bonus',5000)) if ref and await enabled(db) else 0
            await db.execute('INSERT INTO channel_members(user_id,present,referrer_id,reward,last_event) VALUES (?,?,?,?,?)',
                             (user_id,int(present),ref,amount,event_id))
            old=await one(db,'SELECT * FROM channel_members WHERE user_id=?',(user_id,))
        else:
            await db.execute('UPDATE channel_members SET present=?,last_event=? WHERE user_id=?',(int(present),event_id,user_id))
        if present:
            await db.execute('UPDATE channel_members SET reversal_skipped=0 WHERE user_id=?',(user_id,))
        await reconcile_reward(db,old['user_id'])
        if present:
            from services.invite_gate import member_arrived
            await member_arrived(db,user_id)

async def reconcile_reward(db,user_id):
    member=await one(db,'SELECT * FROM channel_members WHERE user_id=?',(user_id,))
    if not member or not member['referrer_id'] or not member['reward'] or not await enabled(db):
        return
    ref=await one(db,'SELECT * FROM users WHERE telegram_id=?',(member['referrer_id'],))
    if not ref: return
    amount=member['reward']
    if member['present'] and not member['credited']:
        await db.execute('UPDATE users SET balance=balance+? WHERE telegram_id=?',
                         (amount,ref['telegram_id']))
        await db.execute('UPDATE channel_members SET credited=1 WHERE user_id=?',(user_id,))
        await enqueue(db,ref['telegram_id'],f'🎁 Do‘stingiz kanalga qo‘shildi. Balansingizga {amount:,} so‘m bonus qo‘shildi.')
    elif not member['present'] and member['credited'] and not member['reversal_skipped']:
        if ref['balance'] < amount:
            # Consume this departure without a debit, notification or future claim.
            # Keep credited=1 so rejoining cannot award the same bonus again.
            await db.execute('UPDATE channel_members SET reversal_skipped=1 WHERE user_id=?',(user_id,))
            return
        await db.execute('UPDATE users SET balance=balance-? WHERE telegram_id=?',
                         (amount,ref['telegram_id']))
        await db.execute('UPDATE channel_members SET credited=0 WHERE user_id=?',(user_id,))
        await enqueue(db,ref['telegram_id'],f'ℹ️ Siz taklif qilgan do‘st (Telegram ID: {user_id}) kanaldan chiqdi. Shu sababli unga berilgan {amount:,} so‘m bonus balansingizdan ayirildi. Qolgan balans: {ref["balance"]-amount:,} so‘m.')


async def toggle_money(admin_id):
    if not await repo.is_admin(admin_id): raise ValueError('Ruxsat yo‘q.')
    async with transaction() as db:
        old=await one(db,"SELECT value FROM settings WHERE key='paid_enabled'")
        value='0' if not old or old['value']=='1' else '1'
        await db.execute("INSERT INTO settings(key,value) VALUES ('paid_enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(value,))
        if value=='1':
            await db.execute("UPDATE settings SET value='1' WHERE key='referral_enabled'")
            cur=await db.execute('SELECT user_id FROM channel_members WHERE present!=credited AND reward>0')
            for row in await cur.fetchall(): await reconcile_reward(db,row['user_id'])
        await db.execute("INSERT INTO admin_actions(admin_id,action_type,description) VALUES (?,'TOGGLE_PAID',?)",(admin_id,value))
        return value=='1'

async def submit_free(kind,item_id,user_id,admin=False):
    if kind not in ('ad','order'): raise ValueError('Noto‘g‘ri tur.')
    if admin and not await repo.is_admin(user_id): raise ValueError('Ruxsat yo‘q.')
    table='ads' if kind=='ad' else 'orders'
    async with transaction() as db:
        setting=await one(db,"SELECT value FROM settings WHERE key='paid_enabled'")
        if not admin and (not setting or setting['value']!='0'): raise ValueError('Pullik tizim yoqilgan. Qayta boshlang.')
        item=await one(db,f'SELECT * FROM {table} WHERE id=?',(item_id,))
        if not item or item['user_id']!=user_id or item['status'] not in ('DRAFT','WAITING_PAYMENT','WAITING_RECEIPT'):
            raise ValueError('Yozuv allaqachon yuborilgan yoki sizga tegishli emas.')
        if not admin:
            from services.invite_gate import require
            await require(db,user_id,kind,item_id,consume=True)
        await db.execute(f"UPDATE {table} SET status='WAITING_ADMIN' WHERE id=?",(item_id,))
        cur=await db.execute('INSERT INTO payments(user_id,type,ad_id,order_id,amount,status) VALUES (?,?,?,?,0,?)',
             (user_id,'AD_PUBLICATION' if kind=='ad' else 'ORDER_PUBLICATION',item_id if kind=='ad' else None,item_id if kind=='order' else None,'WAITING_ADMIN'))
        pid=cur.lastrowid
        from services.experience import stamp_review,event
        await stamp_review(db,pid,'FREE')
        await event(db,user_id,'SUBMITTED',f'{kind}:{item_id}',f'submitted:{kind}:{item_id}')
        return pid

async def reopen(order_id,owner_id,expected_round):
    async with transaction() as db:
        order=await one(db,'SELECT * FROM orders WHERE id=?',(order_id,))
        if not order or order['user_id']!=owner_id: raise ValueError('Faqat zakaz egasi qayta ochadi.')
        if order['status'] not in ('ASSIGNED','COMPLETION_REVIEW'): raise ValueError('Faqat kelishuvi bor zakaz qayta ochiladi.')
        if order['reopen_count']!=expected_round: raise ValueError('Bu eski tugma. Zakazni qayta oching.')
        if order['reopen_count']>=2: raise ValueError('Ikki marta yangilash limiti tugagan.')
        await db.execute("UPDATE orders SET status='PUBLISHED',assigned_to=NULL,selected_application_id=NULL,assigned_at=NULL,owner_done=0,worker_done=0,reopen_count=reopen_count+1,channel_sync_pending=1 WHERE id=?",(order_id,))
        # Existing proposals and contact history are retained. They become selectable again.
        await db.execute("UPDATE order_applications SET status='CLOSED',decided_at=NULL WHERE order_id=? AND status='ACCEPTED'",(order_id,))
        cur=await db.execute('SELECT applicant_id FROM order_applications WHERE order_id=?',(order_id,))
        for row in await cur.fetchall(): await enqueue(db,row['applicant_id'],f'🔄 {order["public_code"]} qayta ochildi. Oldingi taklifingiz saqlandi. Buyurtmachi uni qayta ko‘rib chiqishi mumkin.')
        return order

async def confirm_finish(order_id,actor_id,expected_round):
    admins=await repo.list_admin_ids()
    async with transaction() as db:
        order=await one(db,'SELECT * FROM orders WHERE id=?',(order_id,))
        if not order or actor_id not in (order['user_id'],order['assigned_to']): raise ValueError('Faqat buyurtmachi va tanlangan bajaruvchi tasdiqlaydi.')
        if order['reopen_count']!=expected_round: raise ValueError('Eski kelishuv tugmasi. Zakazni qayta oching.')
        if order['status']!='ASSIGNED': raise ValueError('Yakunlash allaqachon yuborilgan yoki zakaz faol emas.')
        field='owner_done' if actor_id==order['user_id'] else 'worker_done'
        if order[field]: raise ValueError('Siz allaqachon tasdiqlagansiz. Ikkinchi tomon kutilmoqda.')
        await db.execute(f'UPDATE orders SET {field}=1 WHERE id=?',(order_id,))
        other=order['worker_done'] if field=='owner_done' else order['owner_done']
        if other:
            await db.execute("UPDATE orders SET status='COMPLETION_REVIEW',channel_sync_pending=1 WHERE id=?",(order_id,))
            for uid in admins: await enqueue(db,uid,f'🏁 {order["public_code"]}: ikki tomon ish tugaganini tasdiqladi. /admin → Barcha kutilayotganlar.')
        else:
            uid=order['assigned_to'] if field=='owner_done' else order['user_id']
            await enqueue(db,uid,f'🏁 {order["public_code"]}: ikkinchi tomon ish tugaganini tasdiqladi. Zakaz yoki so‘rov tafsilotida siz ham tasdiqlang.')
        return bool(other)

async def moderate_finish(order_id,admin_id,expected_round,approve):
    if not await repo.is_admin(admin_id): raise ValueError('Ruxsat yo‘q.')
    async with transaction() as db:
        order=await one(db,'SELECT * FROM orders WHERE id=?',(order_id,))
        if not order or order['status']!='COMPLETION_REVIEW' or order['reopen_count']!=expected_round or not(order['owner_done'] and order['worker_done']):
            raise ValueError('Bu yakunlash so‘rovi eskirgan.')
        await db.execute("UPDATE orders SET status=?,completed_at=?,owner_done=?,worker_done=?,channel_sync_pending=1 WHERE id=?",
            ('COMPLETED' if approve else 'ASSIGNED',repo._now() if approve else None,int(approve),int(approve),order_id))
        for uid in (order['user_id'],order['assigned_to']):
            await enqueue(db,uid,f'{order["public_code"]}: '+('🏁 Admin tasdiqladi. Zakaz to‘liq tugatildi. Zakaz yoki so‘rov tafsilotidan “Fikr va natija”ni ochib, hamkorlik haqida fikr qoldirishingiz mumkin.' if approve else 'Admin yakunlashni rad etdi. Kelishuv davom etadi; ish tugagach qayta tasdiqlang.'))
        if approve:
            from services.experience import event
            await event(db,order['user_id'],'COMPLETED',str(order_id),f'completed:{order_id}')
        await db.execute("INSERT INTO admin_actions(admin_id,action_type,target_type,target_id) VALUES (?,?,'order',?)",(admin_id,'COMPLETE_ORDER' if approve else 'REJECT_COMPLETION',str(order_id)))

async def pending_entries():
    db=await get_db()
    entries=[]
    cur=await db.execute("SELECT p.id,p.ad_id,p.order_id,p.amount,p.receipt_file_id FROM payments p WHERE p.status='WAITING_ADMIN' ORDER BY p.id")
    for row in await cur.fetchall():
        p=dict(row); kind='ad' if p['ad_id'] else 'order'; item=await (repo.get_ad(p['ad_id']) if kind=='ad' else repo.get_order(p['order_id']))
        if item and not item['channel_message_id'] and item['status']=='WAITING_ADMIN': entries.append(('payment',p,item))
    cur=await db.execute("SELECT * FROM orders WHERE status='COMPLETION_REVIEW' ORDER BY id")
    entries.extend(('finish',{},dict(r)) for r in await cur.fetchall())
    for table in ('ads','orders'):
        cur=await db.execute(f"SELECT * FROM {table} WHERE status IN ('READY_TO_PUBLISH','PUBLISHING','PUBLICATION_FAILED','PUBLICATION_REVIEW') ORDER BY id")
        entries.extend(('recovery',{'kind':'ad' if table=='ads' else 'order'},dict(r)) for r in await cur.fetchall())
    return entries

async def send_pending(bot,admin,entry):
    import keyboards.keyboards as kb
    typ,p,item=entry
    title=f"⏳ {item['public_code']} · {kb.STATUS_LABELS.get(item['status'],item['status'])}"
    if typ=='payment':
        title+=f"\n💰 {p['amount']:,} so‘m" if p['amount'] else '\nBepul joylashtirish'
        rows=[(f"pay:preview:{p['id']}",'👁 To‘liq ko‘rish'),(f"upgrade:edit:{p['id']}",'✏️ Tahrirlash'),(f"pay:approve:{p['id']}",'✅ Tasdiqlash'),(f"pay:reject:{p['id']}",'❌ Rad etish')]
    elif typ=='finish':
        rows=[(f"finish:admin:{item['id']}:{item['reopen_count']}:yes",'🏁 Yakunlashni tasdiqlash'),(f"finish:admin:{item['id']}:{item['reopen_count']}:no",'❌ Yakunlashni rad etish')]
        title+='\nBuyurtmachi va bajaruvchi tasdiqladi.'
    else: rows=[(f"admin:republish:{p['kind']}:{item['id']}",'🔄 Kanalga joylashni tekshirish')]
    from services.order_channel import clip_units
    body=clip_units(item.get('caption') or item.get('text') or '',700)
    await bot.send_message(admin,title+'\n\n'+body,reply_markup=kb.ipb(rows))
    if typ=='payment' and p.get('receipt_file_id'):
        await bot.send_photo(admin,p['receipt_file_id'],caption=f"Chek · {item['public_code']}")

async def hourly_reminders(bot):
    entries=await pending_entries()
    db=await get_db(); now=int(time.time())
    for admin in await repo.list_admin_ids():
        for entry in entries:
            typ,p,item=entry
            key=f"{typ}:{item['public_code']}:{p.get('id',item.get('reopen_count',0))}"
            old=await one(db,'SELECT sent_at FROM reminder_delivery WHERE admin_id=? AND item_key=?',(admin,key))
            if old and now-old['sent_at']<3600: continue
            try: await send_pending(bot,admin,entry)
            except Exception:
                log.warning('Eslatma yetkazilmadi: %s',admin)
                continue
            await db.execute('INSERT INTO reminder_delivery VALUES (?,?,?) ON CONFLICT(admin_id,item_key) DO UPDATE SET sent_at=excluded.sent_at',(admin,key,now))
