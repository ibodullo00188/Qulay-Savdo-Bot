"""Free applications and one-winner assignment. All decisions are transactional."""
from database.db import get_db
from database.repo import _now
from services.transactions import transaction, one


async def get_application(application_id):
    return await one(await get_db(), "SELECT * FROM order_applications WHERE id=?", (application_id,))


async def find_application(order_id, applicant_id):
    return await one(await get_db(), "SELECT * FROM order_applications WHERE order_id=? AND applicant_id=?", (order_id,applicant_id))


async def enqueue(db,user_id,text='',event_type=None,entity_id=None):
    await db.execute("INSERT INTO notifications(user_id,text,event_type,entity_id) VALUES (?,?,?,?)",
                     (user_id,text,event_type,entity_id))


async def create_application(order_id, applicant_id, price, deadline, proposal):
    if not all(isinstance(x,str) and x.strip() for x in (price,deadline,proposal)):
        raise ValueError("Taklifning barcha maydonlarini to'ldiring.")
    if len(price)>40 or len(deadline)>100 or len(proposal)>800:
        raise ValueError("Taklif matni belgilangan chegaradan uzun.")
    async with transaction() as db:
        order = await one(db,"SELECT * FROM orders WHERE id=?",(order_id,))
        if not order or order['deleted_at'] or order['status']!='PUBLISHED' or order['assigned_to']:
            raise ValueError("Bu zakaz uchun so'rovlar yopilgan.")
        if order['user_id']==applicant_id:
            raise ValueError("O'z zakazingizga so'rov yubora olmaysiz.")
        applicant = await one(db,"SELECT * FROM users WHERE telegram_id=?",(applicant_id,))
        owner = await one(db,"SELECT * FROM users WHERE telegram_id=?",(order['user_id'],))
        if not applicant or not applicant['username'] or applicant['is_blocked']:
            raise ValueError("Telegram username o‘rnating, tekshirish tugmasini bosing va taklifni qayta yuboring.")
        if not owner or not owner['username'] or owner['is_blocked']:
            raise ValueError("Buyurtmachining aloqa ma'lumoti hozir mavjud emas.")
        existing = await one(db,"SELECT id FROM order_applications WHERE order_id=? AND applicant_id=?",(order_id,applicant_id))
        if existing:
            raise ValueError("Siz bu zakazga allaqachon so'rov yuborgansiz. Holatini «Mening so'rovlarim»da ko'ring.")
        cur = await db.execute("INSERT INTO order_applications(order_id,applicant_id,username,price,deadline,proposal,created_at) VALUES (?,?,?,?,?,?,?)",
                               (order_id,applicant_id,applicant['username'],price.strip(),deadline.strip(),proposal.strip(),_now()))
        application_id = cur.lastrowid
        from services.experience import event
        await event(db,applicant_id,'APPLICATION',str(order_id),f'application:{application_id}')
        await enqueue(db,order['user_id'],event_type='APPLICATION',entity_id=application_id)
        return application_id


async def decide_application(application_id, owner_id, accept):
    async with transaction() as db:
        app = await one(db,"SELECT * FROM order_applications WHERE id=?",(application_id,))
        order = await one(db,"SELECT * FROM orders WHERE id=?",(app['order_id'],)) if app else None
        if not order or order['user_id']!=owner_id:
            raise ValueError("Bu so'rovni faqat zakaz egasi boshqara oladi.")
        if order['status']!='PUBLISHED' or order['assigned_to'] or order['deleted_at']:
            raise ValueError("Bu zakazda tanlov yakunlangan. Yangi bajaruvchi tanlab bo'lmaydi.")
        if app['status']!='PENDING':
            raise ValueError("Bu so'rov allaqachon ko'rib chiqilgan.")
        if accept:
            user = await one(db,"SELECT * FROM users WHERE telegram_id=?",(app['applicant_id'],))
            if not user or user['is_blocked']:
                raise ValueError("Bu nomzod hozir bloklangan.")
            from services.experience import event
            await event(db,owner_id,'ASSIGNED',str(order['id']),f"assigned:{order['id']}:{order['reopen_count']}")
            now = _now()
            await db.execute("UPDATE orders SET status='ASSIGNED',assigned_to=?,selected_application_id=?,assigned_at=?,channel_sync_pending=1 WHERE id=?",
                             (app['applicant_id'],application_id,now,order['id']))
            await db.execute("UPDATE order_applications SET status='ACCEPTED',decided_at=? WHERE id=?",(now,application_id))
            cur = await db.execute("SELECT id,applicant_id FROM order_applications WHERE order_id=? AND status='PENDING'",(order['id'],))
            others = await cur.fetchall()
            await db.execute("UPDATE order_applications SET status='CLOSED',decided_at=? WHERE order_id=? AND status='PENDING'",(now,order['id']))
            for other in others:
                await enqueue(db,other['applicant_id'],f"Tanlov yakunlandi · {order['public_code']}\n\nBuyurtmachi boshqa bajaruvchini tanladi. Ishtirokingiz uchun rahmat.")
            await enqueue(db,app['applicant_id'],event_type='ACCEPTED',entity_id=application_id)
        else:
            await db.execute("UPDATE order_applications SET status='REJECTED',decided_at=? WHERE id=?",(_now(),application_id))
            await enqueue(db,app['applicant_id'],f"So'rov natijasi · {order['public_code']}\n\nBuyurtmachi taklifingizni rad etdi. Boshqa ochiq zakazlarga so'rov yuborishingiz mumkin.")
        await db.execute("INSERT INTO admin_actions(admin_id,action_type,target_type,target_id) VALUES (?,?, 'application',?)",
                         (owner_id,'SELECT_APPLICANT' if accept else 'REJECT_APPLICANT',str(application_id)))
        return order['id']


async def close_order(order_id, actor_id, status, admin=False):
    if status=='COMPLETED':
        raise ValueError('Yakunlash uchun ikki tomon va admin tasdig‘i kerak.')
    if status not in ('COMPLETED','DELETED','REJECTED'):
        raise ValueError("Noto'g'ri holat.")
    async with transaction() as db:
        order = await one(db,"SELECT * FROM orders WHERE id=?",(order_id,))
        if not order or (not admin and order['user_id']!=actor_id):
            raise ValueError("Zakaz topilmadi.")
        if status=='COMPLETED' and order['status']!='ASSIGNED':
            raise ValueError("Avval bajaruvchini tanlang. Keyin ish tugaganini belgilashingiz mumkin.")
        if order['status'] in ('WAITING_ADMIN','READY_TO_PUBLISH','PUBLISHING','PUBLICATION_FAILED','PUBLICATION_REVIEW','PROCESSING'):
            raise ValueError("Avval to'lov va joylash jarayonini yakunlang.")
        if order['status']=='DELETED':
            raise ValueError("Zakaz allaqachon o'chirilgan.")
        # Assignment represents an agreement; normal users finish it, not silently delete it.
        if status=='DELETED' and order['status'] in ('ASSIGNED','COMPLETION_REVIEW') and not admin:
            raise ValueError("Bajaruvchi tanlangan. Ish tugaganda «Ish bajarildi» tugmasini bosing.")
        now = _now()
        await db.execute("UPDATE orders SET status=?,completed_at=CASE WHEN ?='COMPLETED' THEN ? ELSE completed_at END,deleted_at=CASE WHEN ?='DELETED' THEN ? ELSE deleted_at END,channel_sync_pending=1 WHERE id=?",
                         (status,status,now,status,now,order_id))
        cur = await db.execute("SELECT applicant_id FROM order_applications WHERE order_id=? AND status='PENDING'",(order_id,))
        for row in await cur.fetchall():
            await enqueue(db,row['applicant_id'],f"Zakaz yopildi · {order['public_code']}\n\nUshbu zakaz uchun tanlov tugadi.")
        await db.execute("UPDATE order_applications SET status='CLOSED',decided_at=? WHERE order_id=? AND status='PENDING'",(now,order_id))
        if order['assigned_to']:
            await enqueue(db,order['assigned_to'],f"{order['public_code']} · " + ("Buyurtmachi ish bajarilganini tasdiqladi. Rahmat!" if status=='COMPLETED' else "Zakaz yopildi."))
        return order


async def list_order_applications(order_id, owner_id, offset=0, limit=6):
    db = await get_db()
    order = await one(db,"SELECT * FROM orders WHERE id=? AND user_id=?",(order_id,owner_id))
    if not order:
        raise ValueError("Bu zakaz sizga tegishli emas.")
    cur = await db.execute("SELECT * FROM order_applications WHERE order_id=? ORDER BY CASE status WHEN 'ACCEPTED' THEN 0 WHEN 'PENDING' THEN 1 ELSE 2 END,id DESC LIMIT ? OFFSET ?",(order_id,limit,offset))
    items = [dict(x) for x in await cur.fetchall()]
    count = await one(db,"SELECT COUNT(*) AS n FROM order_applications WHERE order_id=?",(order_id,))
    return order,items,count['n']


async def list_my_applications(user_id, offset=0, limit=6):
    db = await get_db()
    cur = await db.execute("SELECT a.*,o.public_code,o.status AS order_status FROM order_applications a JOIN orders o ON o.id=a.order_id WHERE a.applicant_id=? ORDER BY a.id DESC LIMIT ? OFFSET ?",(user_id,limit,offset))
    items = [dict(x) for x in await cur.fetchall()]
    count = await one(db,"SELECT COUNT(*) AS n FROM order_applications WHERE applicant_id=?",(user_id,))
    return items,count['n']
