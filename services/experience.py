"""Product flows: moderation deadlines, profiles, discovery and support."""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from database.db import get_db, _add_column_if_missing
import database.repo as repo
from services.transactions import one, transaction
from services.applications import enqueue

CATEGORIES={'bot':'🤖 Telegram bot','web':'🌐 Sayt','app':'📱 Mobil ilova','other':'💻 Boshqa dastur'}
EDITABLE=('DRAFT','WAITING_PAYMENT','WAITING_RECEIPT')
DEFAULTS={
 'review_minutes':'60',
 'start_text':'👋 Qulay Savdo Bot’ga xush kelibsiz!\n\nTayyor bot, sayt yoki ilovangizni sotuvga qo‘ying. Yangi dastur kerak bo‘lsa, zakaz joylashtirib, kelgan takliflardan bajaruvchi tanlang.\n\n👨‍💻 Dasturchimisiz? Ochiq zakazlarni ko‘ring — narx va muddat taklif qilish bepul.\n\n👇 Nima qilmoqchisiz?',
 'ad_guide':'Tayyor bot, sayt yoki ilovangiz haqida yozing: nimalar qiladi, narxi qancha, demo va aloqa ma’lumoti. Yuborishdan oldin ko‘rib, tahrirlashingiz mumkin.',
 'order_guide':'Qanday dastur kerakligini, asosiy funksiyalarini, taxminiy budjet va muddatni yozing. Shu ma’lumot orqali dasturchilar sizga narx va muddat taklif qiladi.',
}

async def migrate(db):
    for table in ('orders','ads'):
        await _add_column_if_missing(db,table,'category',"category TEXT NOT NULL DEFAULT 'other'")
        await _add_column_if_missing(db,table,'draft_version','draft_version INTEGER NOT NULL DEFAULT 0')
        await _add_column_if_missing(db,table,'quoted_price','quoted_price INTEGER')
    for field,ddl in [('payment_source',"payment_source TEXT NOT NULL DEFAULT 'LEGACY'"),('review_minutes','review_minutes INTEGER'),('review_due_at','review_due_at TEXT'),('overdue_notified','overdue_notified INTEGER NOT NULL DEFAULT 0')]:
        await _add_column_if_missing(db,'payments',field,ddl)
    await db.execute("UPDATE payments SET payment_source=CASE WHEN amount=0 THEN 'FREE' WHEN receipt_file_id IS NOT NULL THEN 'CARD' ELSE 'WALLET' END WHERE payment_source='LEGACY'")
    await db.executescript('''
      CREATE TABLE IF NOT EXISTS profiles(user_id INTEGER PRIMARY KEY,bio TEXT NOT NULL DEFAULT '',portfolio TEXT NOT NULL DEFAULT '',specialty TEXT NOT NULL DEFAULT 'other');
      CREATE TABLE IF NOT EXISTS subscriptions(user_id INTEGER,category TEXT,PRIMARY KEY(user_id,category));
      CREATE TABLE IF NOT EXISTS order_alerts(order_id INTEGER,user_id INTEGER,PRIMARY KEY(order_id,user_id));
      CREATE TABLE IF NOT EXISTS support_tickets(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,order_id INTEGER,body TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'OPEN',created_at TEXT NOT NULL,response TEXT,answered_by INTEGER);
      CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id INTEGER NOT NULL,author_id INTEGER NOT NULL,target_id INTEGER NOT NULL,rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),body TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'PENDING',created_at TEXT NOT NULL,UNIQUE(order_id,author_id));
      CREATE TABLE IF NOT EXISTS showcase_consent(order_id INTEGER,user_id INTEGER,PRIMARY KEY(order_id,user_id));
      CREATE TABLE IF NOT EXISTS analytics_events(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,event TEXT NOT NULL,entity TEXT,event_key TEXT UNIQUE,created_at TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS analytics_type_date ON analytics_events(event,created_at);
      CREATE INDEX IF NOT EXISTS orders_category_status ON orders(category,status);
    ''')
    await db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('review_minutes','60')")
    old=await one(db,"SELECT value FROM settings WHERE key='experience_v4'")
    if not old:
        # Upgrade only shipped copy; retain custom admin text.
        from database.db import DEFAULT_SETTINGS,LEGACY_COPY
        for key,value in DEFAULTS.items():
            if key!='review_minutes':
                current=await one(db,'SELECT value FROM settings WHERE key=?',(key,))
                if current and current['value'] in (DEFAULT_SETTINGS.get(key),LEGACY_COPY.get(key)):
                    await db.execute('UPDATE settings SET value=? WHERE key=?',(value,key))
        await db.execute("INSERT INTO settings(key,value) VALUES ('experience_v4','1')")
    # Snapshot legacy pending deadlines once, without shifting them on restart.
    mins=await one(db,"SELECT value FROM settings WHERE key='review_minutes'")
    minutes=int(mins['value'])
    await db.execute("UPDATE payments SET review_minutes=?,review_due_at=datetime(created_at,?) WHERE status='WAITING_ADMIN' AND review_due_at IS NULL",(minutes,f'+{minutes} minutes'))

async def review_minutes():
    return await repo.get_int_setting('review_minutes',60)

def duration(minutes):
    return f'{minutes//60} soat' if minutes%60==0 else f'{minutes} daqiqa'

async def stamp_review(db,payment_id,source):
    setting=await one(db,"SELECT value FROM settings WHERE key='review_minutes'")
    minutes=int(setting['value']) if setting else 60
    await db.execute("UPDATE payments SET payment_source=?,review_minutes=?,review_due_at=datetime('now',?),overdue_notified=0 WHERE id=?",(source,minutes,f'+{minutes} minutes',payment_id))

async def event(db,user_id,name,entity='',key=None):
    await db.execute('INSERT OR IGNORE INTO analytics_events(user_id,event,entity,event_key,created_at) VALUES (?,?,?,?,?)',(user_id,name,str(entity),key,repo._now()))

async def track(user_id,name,entity='',key=None):
    await event(await get_db(),user_id,name,entity,key)

async def save_draft(kind,user_id,content,category,item_id=None):
    if kind not in ('ad','order') or category not in CATEGORIES: raise ValueError('Noto‘g‘ri bo‘lim.')
    table='ads' if kind=='ad' else 'orders'
    if item_id is None:
        creator=repo.create_ad if kind=='ad' else repo.create_order
        item_id,_=await creator(user_id,**content,status='DRAFT')
    async with transaction() as db:
        item=await one(db,f'SELECT * FROM {table} WHERE id=?',(item_id,))
        if not item or item['user_id']!=user_id or item['status'] not in EDITABLE or item['deleted_at']:
            raise ValueError('Bu post tahrirlanmaydi. Joriy holatini tekshiring.')
        await db.execute(f"UPDATE {table} SET message_type=?,telegram_file_id=?,caption=?,text=?,category=?,draft_version=draft_version+1,status='DRAFT' WHERE id=?",(content['message_type'],content['telegram_file_id'],content['caption'],content['text'],category,item_id))
        await event(db,user_id,'DRAFT_SAVED',f'{kind}:{item_id}',f'draft:{kind}:{item_id}')
    return await (repo.get_ad(item_id) if kind=='ad' else repo.get_order(item_id))

async def prepare_submission(kind,item_id,user_id,version):
    table='ads' if kind=='ad' else 'orders'
    async with transaction() as db:
        item=await one(db,f'SELECT * FROM {table} WHERE id=?',(item_id,))
        if not item or item['user_id']!=user_id or item['status'] not in EDITABLE or item['draft_version']!=version or item['deleted_at']:
            raise ValueError('Bu eski ko‘rinish. Qoralamani qayta oching.')
        if kind=='order':
            user=await one(db,'SELECT * FROM users WHERE telegram_id=?',(user_id,))
            if not user or not user['username']: raise ValueError('Telegram username o‘rnating va qayta tekshiring.')
        key='max_active_ads' if kind=='ad' else 'max_active_orders'
        limit=await one(db,'SELECT value FROM settings WHERE key=?',(key,))
        count=await one(db,f"SELECT COUNT(*) n FROM {table} WHERE user_id=? AND id!=? AND deleted_at IS NULL AND status NOT IN ('DRAFT','DELETED','REJECTED','COMPLETED','CANCELLED')",(user_id,item_id))
        if count['n']>=int(limit['value']): raise ValueError('Faol postlar limiti tugagan. Qoralama saqlandi.')
        from services.invite_gate import require
        await require(db,user_id)
        quote=item.get('quoted_price')
        if quote is None:
            price=await one(db,'SELECT value FROM settings WHERE key=?',(kind+'_price',))
            quote=int(price['value'])
        await db.execute(f"UPDATE {table} SET status='WAITING_PAYMENT',quoted_price=? WHERE id=?",(quote,item_id))
        item['quoted_price']=quote
        return item

async def pending_text(payment):
    code=''
    item=await (repo.get_ad(payment['ad_id']) if payment['ad_id'] else repo.get_order(payment['order_id']))
    if item: code=item['public_code']
    label='Chekingiz qabul qilindi' if payment.get('receipt_file_id') else 'Postingiz tekshiruvga yuborildi'
    due=payment.get('review_due_at')
    local=''
    if due:
        local=(datetime.strptime(due,'%Y-%m-%d %H:%M:%S')+timedelta(hours=5)).strftime('%d.%m %H:%M')
    late='\n⏳ Belgilangan muddat o‘tgan. Yordam orqali murojaat qilishingiz mumkin.' if due and due<repo._now() else ''
    return f'✅ {label}\n\nKod: {code}\nHolat: admin tekshiruvida.\n\nTekshirish muddati: {duration(payment.get("review_minutes") or 60)} ichida.'+(f'\nBelgilangan vaqt: {local} (Toshkent).' if local else '')+late+'\n\nQayta to‘lov qilmang. Tasdiqlansa kanalga joylaymiz va xabar yuboramiz. Rad etilsa sababini bildiramiz.'

async def support_ticket(uid,body,order_id=None):
    if not 5<=len(body.strip())<=1500: raise ValueError('Murojaat 5–1500 belgidan iborat bo‘lsin.')
    admins=await repo.list_admin_ids()
    async with transaction() as db:
        if order_id:
            order=await one(db,'SELECT * FROM orders WHERE id=?',(order_id,))
            if not order or uid not in (order['user_id'],order['assigned_to']): raise ValueError('Bu kelishuv sizga tegishli emas.')
        cur=await db.execute("INSERT INTO support_tickets(user_id,order_id,body,created_at) VALUES (?,?,?,?)",(uid,order_id,body.strip(),repo._now()))
        tid=cur.lastrowid
        for admin in admins: await enqueue(db,admin,f'🆘 Yangi yordam so‘rovi #{tid}\n\n{body.strip()}\n\n/admin → Yordam so‘rovlari')
        return tid

async def answer_ticket(tid,admin_id,body):
    if not await repo.is_admin(admin_id): raise ValueError('Ruxsat yo‘q.')
    if not 1<=len(body.strip())<=2500: raise ValueError('Javob 1–2500 belgi bo‘lsin.')
    async with transaction() as db:
        ticket=await one(db,'SELECT * FROM support_tickets WHERE id=?',(tid,))
        if not ticket or ticket['status']!='OPEN': raise ValueError('Murojaat allaqachon javoblangan.')
        await db.execute("UPDATE support_tickets SET status='ANSWERED',response=?,answered_by=? WHERE id=?",(body,admin_id,tid))
        await enqueue(db,ticket['user_id'],f'💬 Yordam so‘rovi #{tid} bo‘yicha javob:\n\n{body}')

async def profile_save(uid,field,value):
    limits={'bio':500,'portfolio':300,'specialty':20}
    if field not in limits or len(value)>limits[field]: raise ValueError('Qiymat juda uzun yoki maydon noto‘g‘ri.')
    if field=='portfolio' and value:
        url=urlparse(value)
        if url.scheme!='https' or not url.hostname or url.username: raise ValueError('Portfolio uchun https:// bilan boshlanuvchi havola yuboring.')
    if field=='specialty' and value not in CATEGORIES: raise ValueError('Mutaxassislikni tugmadan tanlang.')
    db=await get_db()
    await db.execute('INSERT OR IGNORE INTO profiles(user_id) VALUES (?)',(uid,))
    await db.execute(f'UPDATE profiles SET {field}=? WHERE user_id=?',(value,uid))

async def review_create(oid,uid,rating,body):
    if rating not in range(1,6) or not 3<=len(body.strip())<=500: raise ValueError('1–5 baho va 3–500 belgili fikr kerak.')
    admins=await repo.list_admin_ids()
    async with transaction() as db:
        order=await one(db,'SELECT * FROM orders WHERE id=?',(oid,))
        if not order or order['status']!='COMPLETED' or uid not in (order['user_id'],order['assigned_to']): raise ValueError('Faqat yakunlangan ish ishtirokchisi fikr yozadi.')
        target=order['assigned_to'] if uid==order['user_id'] else order['user_id']
        if await one(db,'SELECT id FROM reviews WHERE order_id=? AND author_id=?',(oid,uid)): raise ValueError('Bu ishga fikr qoldirgansiz.')
        cur=await db.execute('INSERT INTO reviews(order_id,author_id,target_id,rating,body,created_at) VALUES (?,?,?,?,?,?)',(oid,uid,target,rating,body.strip(),repo._now()))
        for admin in admins: await enqueue(db,admin,f'⭐ Yangi fikr #{cur.lastrowid}. /admin → Fikrlar')
        return cur.lastrowid

async def review_moderate(rid,admin_id,approve):
    if not await repo.is_admin(admin_id): raise ValueError('Ruxsat yo‘q.')
    async with transaction() as db:
        row=await one(db,'SELECT * FROM reviews WHERE id=?',(rid,))
        if not row or row['status']!='PENDING': raise ValueError('Fikr allaqachon tekshirilgan.')
        await db.execute('UPDATE reviews SET status=? WHERE id=?',('APPROVED' if approve else 'REJECTED',rid))
        await enqueue(db,row['author_id'],'⭐ Fikringiz '+('profilga joylandi.' if approve else 'moderatsiyadan o‘tmadi. Yordam orqali sababini aniqlashtirishingiz mumkin.'))

async def consent(oid,uid,allow):
    async with transaction() as db:
        order=await one(db,'SELECT * FROM orders WHERE id=?',(oid,))
        if not order or order['status']!='COMPLETED' or uid not in (order['user_id'],order['assigned_to']): raise ValueError('Bu natija sizga tegishli emas.')
        if allow: await db.execute('INSERT OR IGNORE INTO showcase_consent VALUES (?,?)',(oid,uid))
        else: await db.execute('DELETE FROM showcase_consent WHERE order_id=? AND user_id=?',(oid,uid))

async def alerts(bot):
    # Outbox enrollment retries independently from network publication.
    async with transaction() as db:
        cur=await db.execute("SELECT o.id,o.public_code,o.user_id,o.category,s.user_id recipient FROM orders o JOIN subscriptions s ON s.category=o.category JOIN users u ON u.telegram_id=s.user_id WHERE o.status='PUBLISHED' AND o.published_at>=datetime('now','-1 day') AND u.is_blocked=0 AND s.user_id!=o.user_id AND NOT EXISTS(SELECT 1 FROM order_alerts a WHERE a.order_id=o.id AND a.user_id=s.user_id) LIMIT 100")
        for row in await cur.fetchall():
            await db.execute('INSERT INTO order_alerts VALUES (?,?)',(row['id'],row['recipient']))
            await enqueue(db,row['recipient'],event_type='MATCHED_ORDER',entity_id=row['id'])
    async with transaction() as db:
        cur=await db.execute("SELECT * FROM payments WHERE status='WAITING_ADMIN' AND review_due_at<datetime('now') AND overdue_notified=0 LIMIT 100")
        for row in await cur.fetchall():
            await db.execute('UPDATE payments SET overdue_notified=1 WHERE id=?',(row['id'],))
            await enqueue(db,row['user_id'],f'⏳ #{row["id"]}: tekshirish belgilangan vaqtdan kechikdi. Qayta to‘lov qilmang. Adminlarga eslatildi. Holat va yordam menyusidan murojaat qilishingiz mumkin.')
            for admin in await repo.list_admin_ids(): await enqueue(db,admin,f'⏰ #{row["id"]}: tekshirish muddati o‘tdi. /admin → Barcha kutilayotganlar')
