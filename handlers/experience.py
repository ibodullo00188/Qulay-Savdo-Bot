"""User-facing v4 experience. All callbacks re-check identity and persisted state."""
from html import escape
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
import database.repo as repo
import keyboards.keyboards as kb
from database.db import get_db
from services.transactions import one,transaction,pay
from services import experience as xp
from services.channel import validate_content
from services.order_channel import clip_units
from services.notifications import flush_notifications
from states import AdStates,OrderStates

router=Router(name='experience')
router.callback_query.filter(F.message.chat.type=='private')
class Edit(StatesGroup):
    content=State()
    preview=State()
    profile=State()
    help=State()
    answer=State()
    review=State()

MENUS={'🔎 Ochiq zakazlar','📝 Qoralamalar','👤 Profilim','🔔 Mos zakazlar','🏆 Bajarilgan ishlar','🆘 Yordam olish',kb.BTN_AD,kb.BTN_ORDER,kb.BTN_MY_ADS,kb.BTN_MY_ORDERS,kb.BTN_MY_APPLICATIONS,kb.BTN_REFERRAL,kb.BTN_FIND_ORDER,kb.BTN_HOME,kb.BTN_CANCEL,kb.BTN_ADMIN,kb.BTN_RULES}
def content_input(message):
    return not(message.text and (message.text.startswith('/') or message.text in MENUS))

def nav(rows): return kb.ipb(rows+[('xp:help:0','🆘 Yordam olish')])
async def item(kind,iid): return await (repo.get_ad(iid) if kind=='ad' else repo.get_order(iid))
async def admin(cq):
    if not await repo.is_admin(cq.from_user.id):
        await cq.answer('Ruxsat yo‘q.',show_alert=True);return False
    return True
async def error(cq,exc): await cq.answer(str(exc)[:190],show_alert=True)

async def begin(message,state,kind):
    from handlers.user.applications import remember
    await remember(message.from_user)
    await state.clear()
    await state.set_state(Edit.content)
    await state.update_data(editor_kind=kind,editor_category='other')
    await xp.track(message.from_user.id,'FLOW_STARTED',kind)
    price=await repo.get_int_setting(kind+'_price',34990)
    free=await repo.get_setting('paid_enabled','1')=='0'
    guide=clip_units(await repo.get_setting(kind+'_guide') or xp.DEFAULTS[kind+'_guide'],2800)
    text=('📢 Tayyor dasturingizni sotuvga qo‘ying' if kind=='ad' else '🛠 Zakazingizga bajaruvchi toping')+'\n\n'+guide
    text+='\n\n'+('🎁 Hozir joylashtirish bepul.' if free else f'💳 Joylashtirish narxi: {price:,} so‘m. Bu kanalga chiqarish haqi.')
    from services.invite_gate import settings as invite_settings
    invitation=await invite_settings()
    if free and invitation['enabled']:
        text+=f'\n🤝 Joylashtirish sharti: {invitation["count"]} ta do‘stni kanalga taklif qilish — '+('bir marta.' if invitation['mode']=='once' else 'har yangi post uchun.')
    if kind=='order': text+=' Dasturchining ish haqi alohida kelishiladi.'
    text+=f'\n⏱ Tekshirish: {xp.duration(await xp.review_minutes())} ichida.\n\n1/3 · Yo‘nalishni tanlang va matn yoki bitta rasm va izoh yuboring. Yo‘nalish tanlanmasa “Boshqa dastur” bo‘ladi.'
    if kind=='ad' and await repo.get_setting('image_required','1')=='1': text+='\nE’lon uchun rasm va izoh kerak.'
    await message.answer(text,reply_markup=nav([(f'xp:category:{c}',v) for c,v in xp.CATEGORIES.items()]+[('xp:save','📝 Saqlab chiqish')]))

@router.message(F.text=="📢 E'lon berish")
@router.message(F.text==kb.BTN_AD)
async def begin_ad(message: Message,state: FSMContext): await begin(message,state,'ad')
@router.message(F.text=='🔵 Dasturga zakaz berish')
@router.message(F.text==kb.BTN_ORDER)
@router.message(Command('order'))
async def begin_order(message: Message,state: FSMContext): await begin(message,state,'order')

@router.callback_query(F.data.startswith('xp:category:'))
async def choose_category(cq: CallbackQuery,state: FSMContext):
    category=cq.data.split(':')[-1]
    if category not in xp.CATEGORIES or await state.get_state()!=Edit.content.state:
        await error(cq,ValueError('Joriy jarayonni menyudan oching.'));return
    await state.update_data(editor_category=category)
    await cq.answer('Yo‘nalish saqlandi')
    await cq.message.answer(xp.CATEGORIES[category]+'\nEndi matn yoki rasm va izohni yuboring.')

async def preview(message,state,kind,post):
    await state.set_state(Edit.preview)
    await state.update_data(editor_kind=kind,editor_id=post['id'],editor_category=post['category'],editor_version=post['draft_version'])
    rows=[(f"xp:submit:{kind}:{post['id']}:{post['draft_version']}",'✅ Davom etish'),(f"xp:edit:{kind}:{post['id']}",'✏️ Tahrirlash'),('xp:save','📝 Saqlab chiqish'),(f"xp:discard:{kind}:{post['id']}",'🗑 Qoralamani o‘chirish')]
    await message.answer(f"2/3 · Postingizni tekshiring\n🆔 {post['public_code']}\n{xp.CATEGORIES[post['category']]}\n\nQuyidagi matn qoralama sifatida saqlandi.")
    body=post.get('caption') or post.get('text') or ''
    if post['message_type']=='PHOTO': await message.answer_photo(post['telegram_file_id'],caption=clip_units(body,1000),reply_markup=nav(rows))
    else: await message.answer(clip_units(body,4000),reply_markup=nav(rows))

@router.message(Edit.content,content_input)
async def draft_content(message: Message,state: FSMContext):
    data=await state.get_data();kind=data.get('editor_kind')
    if kind not in ('ad','order'): await state.clear();return
    if kind=='ad' and await repo.get_setting('image_required','1')=='1' and not message.photo:
        await message.answer('E’lon uchun bitta rasm va uning izohini yuboring. Matnda mahsulot, narx va aloqa ma’lumotini yozing.',reply_markup=nav([('xp:save','📝 Saqlab chiqish')]))
        return
    if not (message.photo or message.text):
        await message.answer('Matn yoki bitta rasm va izoh yuboring.');return
    if not await validate_content(message): return
    content=dict(message_type='PHOTO' if message.photo else 'TEXT',telegram_file_id=message.photo[-1].file_id if message.photo else None,caption=message.caption if message.photo else None,text=message.text if not message.photo else None)
    try: post=await xp.save_draft(kind,message.from_user.id,content,data.get('editor_category','other'),data.get('editor_id'))
    except ValueError as exc: await message.answer(str(exc));return
    await preview(message,state,kind,post)

@router.callback_query(F.data=='xp:save')
async def save_exit(cq: CallbackQuery,state: FSMContext):
    data=await state.get_data();await state.clear();await cq.answer()
    await cq.message.answer('📝 Yuborgan matningiz qoralamalarda saqlandi.' if data.get('editor_id') or data.get('item_id') else 'Hali post matni yuborilmadi. Keyin menyudan boshlashingiz mumkin.',reply_markup=kb.main_menu_rb(await repo.is_admin(cq.from_user.id)))

@router.callback_query(F.data.regexp(r'^xp:(edit|resume):(ad|order):\d+$'))
async def edit_draft(cq: CallbackQuery,state: FSMContext):
    _,action,kind,raw=cq.data.split(':');post=await item(kind,int(raw))
    if not post or post['user_id']!=cq.from_user.id or post['status'] not in xp.EDITABLE or post['deleted_at']:
        await error(cq,ValueError('Qoralama tahrirlanmaydi. Holatini tekshiring.'));return
    await state.clear();await cq.answer()
    if action=='resume': await preview(cq.message,state,kind,post);return
    await state.set_state(Edit.content);await state.update_data(editor_kind=kind,editor_id=post['id'],editor_category=post['category'])
    await cq.message.answer('Yangi matn yoki rasm va izohni yuboring. Yubormasdan chiqsangiz oldingi matn saqlanadi.',reply_markup=nav([(f'xp:category:{c}',v) for c,v in xp.CATEGORIES.items()]))

@router.callback_query(F.data.regexp(r'^xp:submit:(ad|order):\d+:\d+$'))
async def submit(cq: CallbackQuery,state: FSMContext):
    _,_,kind,raw,version=cq.data.split(':');iid=int(raw)
    from handlers.user.applications import remember,require_username
    await remember(cq.from_user)
    if kind=='order' and not await require_username(cq.message,cq.from_user): await cq.answer();return
    from services.invite_gate import InviteRequired
    try: post=await xp.prepare_submission(kind,iid,cq.from_user.id,int(version))
    except InviteRequired:
        await cq.answer()
        from handlers.invite_gate import prompt
        await prompt(cq.message,cq.from_user.id,kind,iid)
        return
    except ValueError as exc: await error(cq,exc);return
    await cq.answer()
    await state.clear();await state.update_data(kind=kind,item_id=iid,public_code=post['public_code'])
    from services.payment import offer_payment
    await cq.message.answer('3/3 · '+('Admin tekshiruviga yuborish' if await repo.get_setting('paid_enabled','1')=='0' else 'Joylashtirish to‘lovi'))
    await offer_payment(cq.message,post['quoted_price'],kind,state,user_id=cq.from_user.id)

async def ack(message,pid):
    p=await repo.get_payment(pid)
    await message.answer(await xp.pending_text(p),reply_markup=nav([(f'xp:payment:{pid}','📋 Holatni ko‘rish')]))

@router.callback_query(F.data.regexp(r'^ad:pay_balance:\d+$'))
async def wallet(cq: CallbackQuery,state: FSMContext):
    from services.payment import validate_callback
    data=await validate_callback(cq,state,'ad')
    if not data:return
    try: pid=await pay(cq.from_user.id,'ad',data['item_id'],data['price'])
    except ValueError as exc: await error(cq,exc);return
    await state.clear();await cq.answer('To‘lov saqlandi')
    await ack(cq.message,pid)

@router.message(AdStates.awaiting_receipt,content_input)
@router.message(OrderStates.awaiting_receipt,content_input)
async def receipt(message: Message,state: FSMContext):
    if not message.photo or message.media_group_id:
        await message.answer('Chekni bitta rasm qilib yuboring.',reply_markup=nav([('xp:save','📝 Keyin davom ettirish')]))
        return
    data=await state.get_data()
    if data.get('kind') not in ('ad','order') or not data.get('item_id'):
        await message.answer('Jarayonni qoralamalardan oching.');return
    try: pid=await pay(message.from_user.id,data['kind'],data['item_id'],data['price'],message.photo[-1].file_id,message.photo[-1].file_unique_id)
    except ValueError as exc: await message.answer(str(exc),reply_markup=nav([]));return
    await state.clear();await ack(message,pid)
    from services.payment import notify_receipt
    await notify_receipt(message,pid,data['public_code'],data['price'])

@router.callback_query(F.data.regexp(r'^xp:payment:\d+$'))
async def payment_status(cq: CallbackQuery):
    p=await repo.get_payment(int(cq.data.split(':')[-1]))
    if not p or (p['user_id']!=cq.from_user.id and not await repo.is_admin(cq.from_user.id)):
        await error(cq,ValueError('Sizga tegishli emas.'));return
    await cq.answer()
    if p['status']=='WAITING_ADMIN': await ack(cq.message,p['id'])
    else:
        post=await item('ad' if p['ad_id'] else 'order',p['ad_id'] or p['order_id'])
        await cq.message.answer(f"To‘lov: {kb.STATUS_LABELS.get(p['status'],p['status'])}\nPost: {kb.STATUS_LABELS.get(post['status'],post['status']) if post else 'Topilmadi'}\n"+(p.get('reject_reason') or ''),reply_markup=nav([]))

async def mine(message,uid,kind,group='all',offset=0):
    db=await get_db();table='ads' if kind=='ad' else 'orders';args=[uid]
    condition='user_id=? AND deleted_at IS NULL'
    conditions={'draft':"status IN ('DRAFT','WAITING_PAYMENT','WAITING_RECEIPT')",'active':"status IN ('PUBLISHED','ASSIGNED')",'review':"status IN ('WAITING_ADMIN','COMPLETION_REVIEW','READY_TO_PUBLISH','PUBLISHING','PUBLICATION_FAILED','PUBLICATION_REVIEW')",'done':"status IN ('COMPLETED','REJECTED','CANCELLED')"}
    if group in conditions:condition+=' AND '+conditions[group]
    total=await one(db,f'SELECT COUNT(*) n FROM {table} WHERE {condition}',tuple(args))
    cur=await db.execute(f'SELECT * FROM {table} WHERE {condition} ORDER BY id DESC LIMIT 6 OFFSET ?',(*args,offset))
    rows=[]
    for post in await cur.fetchall():
        callback=f"xp:resume:{kind}:{post['id']}" if post['status'] in xp.EDITABLE else f"{kind}:view:{post['id']}"
        rows.append((callback,f"{post['public_code']} · {kb.STATUS_LABELS.get(post['status'],post['status'])}"))
    if offset:rows.append((f'xp:mine:{kind}:{group}:{max(0,offset-6)}','← Oldingi'))
    if offset+6<total['n']:rows.append((f'xp:mine:{kind}:{group}:{offset+6}','Keyingi →'))
    rows += [(f'xp:mine:{kind}:{g}:0',label) for g,label in [('all','Barchasi'),('draft','📝 Qoralamalar'),('active','🟢 Faol'),('review','⏳ Tekshiruvda'),('done','🏁 Yakunlangan')]]
    await message.answer(f"{'E’lonlarim' if kind=='ad' else 'Zakazlarim'} · {total['n']} ta",reply_markup=nav(rows))

@router.message(F.text==kb.BTN_MY_ADS)
async def ads_mine(message: Message,state: FSMContext): await state.clear();await mine(message,message.from_user.id,'ad')
@router.message(F.text==kb.BTN_MY_ORDERS)
async def orders_mine(message: Message,state: FSMContext): await state.clear();await mine(message,message.from_user.id,'order')
@router.message(F.text=='📝 Qoralamalar')
async def drafts(message: Message,state: FSMContext):
    await state.clear();await message.answer('Saqlangan qoralamalar:',reply_markup=kb.ipb([('xp:mine:ad:draft:0','📢 E’lonlar'),('xp:mine:order:draft:0','🛠 Zakazlar')]))
@router.callback_query(F.data.regexp(r'^xp:mine:(ad|order):(all|draft|active|review|done):\d+$'))
async def mine_page(cq: CallbackQuery):
    _,_,kind,group,offset=cq.data.split(':');await cq.answer();await mine(cq.message,cq.from_user.id,kind,group,int(offset))

async def browse(message,category='all',offset=0):
    if category!='all' and category not in xp.CATEGORIES:return
    db=await get_db();condition="o.status='PUBLISHED' AND o.assigned_to IS NULL AND o.deleted_at IS NULL AND u.is_blocked=0";args=[]
    if category!='all':condition+=' AND o.category=?';args=[category]
    total=await one(db,f'SELECT COUNT(*) n FROM orders o JOIN users u ON u.telegram_id=o.user_id WHERE {condition}',tuple(args))
    cur=await db.execute(f'SELECT o.* FROM orders o JOIN users u ON u.telegram_id=o.user_id WHERE {condition} ORDER BY o.id DESC LIMIT 6 OFFSET ?',(*args,offset))
    rows=[]
    for post in await cur.fetchall(): rows.append((f"order:apply:{post['public_code']}",f"{post['public_code']} · {(post['text'] or post['caption'] or '')[:35]}"))
    if offset:rows.append((f'xp:browse:{category}:{max(0,offset-6)}','← Oldingi'))
    if offset+6<total['n']:rows.append((f'xp:browse:{category}:{offset+6}','Keyingi →'))
    rows += [(f'xp:browse:{c}:0',v) for c,v in {'all':'Barcha yo‘nalishlar',**xp.CATEGORIES}.items()]
    await message.answer(f"🔎 Ochiq zakazlar: {total['n']} ta\nTaklif yuborish bepul.",reply_markup=nav(rows))
@router.message(F.text=='🔎 Ochiq zakazlar')
async def browse_menu(message: Message,state: FSMContext):await state.clear();await browse(message)
@router.callback_query(F.data.regexp(r'^xp:browse:(all|bot|web|app|other):\d+$'))
async def browse_page(cq: CallbackQuery):
    _,_,cat,offset=cq.data.split(':');await cq.answer();await browse(cq.message,cat,int(offset))

async def subscriptions(message,uid):
    db=await get_db();cur=await db.execute('SELECT category FROM subscriptions WHERE user_id=?',(uid,));chosen={r[0] for r in await cur.fetchall()}
    await message.answer('🔔 Mos zakazlar\nFaqat o‘zingiz yoqqan yo‘nalishlardan yangi zakaz xabari keladi. Qayta bosib o‘chiring.',reply_markup=kb.ipb([(f'xp:sub:{c}',('✅ ' if c in chosen else '⬜ ')+v) for c,v in xp.CATEGORIES.items()]+[('xp:sub:off','Barchasini o‘chirish')]))
@router.message(F.text=='🔔 Mos zakazlar')
async def sub_menu(message: Message,state: FSMContext):await state.clear();await subscriptions(message,message.from_user.id)
@router.callback_query(F.data=='xp:subscriptions')
async def sub_settings(cq: CallbackQuery):await cq.answer();await subscriptions(cq.message,cq.from_user.id)
@router.callback_query(F.data.startswith('xp:sub:'))
async def sub_toggle(cq: CallbackQuery):
    cat=cq.data.split(':')[-1];uid=cq.from_user.id
    if cat not in (*xp.CATEGORIES,'off'):return
    await repo.get_or_create_user(uid,cq.from_user.username,cq.from_user.first_name)
    async with transaction() as db:
        if cat=='off':await db.execute('DELETE FROM subscriptions WHERE user_id=?',(uid,))
        elif await one(db,'SELECT 1 FROM subscriptions WHERE user_id=? AND category=?',(uid,cat)):await db.execute('DELETE FROM subscriptions WHERE user_id=? AND category=?',(uid,cat))
        else:await db.execute('INSERT INTO subscriptions VALUES (?,?)',(uid,cat))
    await cq.answer();await subscriptions(cq.message,uid)

async def profile(message,uid,viewer):
    db=await get_db();user=await repo.get_user(uid)
    if not user:await message.answer('Profil topilmadi.');return
    p=await one(db,'SELECT * FROM profiles WHERE user_id=?',(uid,)) or {'bio':'','portfolio':'','specialty':'other'}
    completed=await one(db,"SELECT COUNT(*) n FROM orders WHERE status='COMPLETED' AND assigned_to=?",(uid,))
    ratings=await one(db,"SELECT COUNT(*) n,AVG(rating) avg FROM reviews WHERE target_id=? AND status='APPROVED'",(uid,))
    title='@'+user['username'] if user['username'] else (user['first_name'] or 'Foydalanuvchi')
    text=f"👤 {title}\n{xp.CATEGORIES[p['specialty']]}\n\n{p['bio'] or 'Haqida ma’lumot kiritilmagan.'}\n\nPortfolio: {p['portfolio'] or 'Kiritilmagan'}\n🏁 Tasdiqlangan bajarilgan ishlar: {completed['n']}\n⭐ "+(f"{ratings['avg']:.1f}/5 · {ratings['n']} fikr" if ratings['n'] else 'Hali fikr yo‘q')
    rows=[(f'xp:reviews:{uid}:0','⭐ Fikrlarni ko‘rish')]
    if uid==viewer:rows += [('xp:pedit:bio','✏️ O‘zim haqimda'),('xp:pedit:portfolio','🔗 Portfolio'),('xp:pedit:specialty','Mutaxassislik')]
    await message.answer(text,reply_markup=nav(rows))
@router.message(F.text=='👤 Profilim')
async def my_profile(message: Message,state: FSMContext):
    await state.clear();await repo.get_or_create_user(message.from_user.id,message.from_user.username,message.from_user.first_name);await profile(message,message.from_user.id,message.from_user.id)
@router.callback_query(F.data.regexp(r'^xp:profile:\d+$'))
async def profile_view(cq: CallbackQuery):await cq.answer();await profile(cq.message,int(cq.data.split(':')[-1]),cq.from_user.id)
@router.callback_query(F.data.startswith('xp:pedit:'))
async def profile_edit(cq: CallbackQuery,state: FSMContext):
    field=cq.data.split(':')[-1]
    if field not in ('bio','portfolio','specialty'):return
    await cq.answer();await state.clear()
    if field=='specialty':
        await cq.message.answer('Mutaxassisligingiz:',reply_markup=kb.ipb([(f'xp:pspecial:{c}',v) for c,v in xp.CATEGORIES.items()]));return
    await state.set_state(Edit.profile);await state.update_data(profile_field=field)
    await cq.message.answer(('O‘zingiz haqingizda 500 belgigacha yozing.' if field=='bio' else 'Portfolio uchun https:// havola yuboring.')+' Bu ma’lumot boshqalarga ochiq ko‘rinadi. O‘chirish uchun “-” yuboring.',reply_markup=kb.cancel_kb())
@router.callback_query(F.data.startswith('xp:pspecial:'))
async def profile_special(cq: CallbackQuery):
    try:await xp.profile_save(cq.from_user.id,'specialty',cq.data.split(':')[-1])
    except ValueError as exc:await error(cq,exc);return
    await cq.answer('Saqlandi');await profile(cq.message,cq.from_user.id,cq.from_user.id)
@router.message(Edit.profile,F.text,content_input)
async def profile_content(message: Message,state: FSMContext):
    data=await state.get_data()
    try:await xp.profile_save(message.from_user.id,data['profile_field'],'' if message.text.strip()=='-' else message.text.strip())
    except ValueError as exc:await message.answer(str(exc));return
    await state.clear();await profile(message,message.from_user.id,message.from_user.id)

@router.callback_query(F.data.regexp(r'^xp:reviews:\d+:\d+$'))
async def reviews(cq: CallbackQuery):
    _,_,uid,offset=cq.data.split(':');uid=int(uid);offset=int(offset);db=await get_db()
    cur=await db.execute("SELECT r.*,o.public_code FROM reviews r JOIN orders o ON o.id=r.order_id WHERE r.target_id=? AND r.status='APPROVED' ORDER BY r.id DESC LIMIT 6 OFFSET ?",(uid,offset))
    rows=await cur.fetchall();await cq.answer()
    text='⭐ Tasdiqlangan ishlarga bildirilgan fikrlar\n\n'+'\n\n'.join(f"{r['public_code']} · {r['rating']}/5\n{r['body']}" for r in rows)
    buttons=[]
    if offset:buttons.append((f'xp:reviews:{uid}:{max(0,offset-6)}','← Oldingi'))
    if len(rows)==6:buttons.append((f'xp:reviews:{uid}:{offset+6}','Keyingi →'))
    await cq.message.answer(text if rows else 'Hali tasdiqlangan fikr yo‘q.',reply_markup=kb.ipb(buttons) if buttons else None)

@router.callback_query(F.data.regexp(r'^xp:compare:\d+:\d+$'))
async def compare(cq: CallbackQuery):
    _,_,oid,offset=cq.data.split(':');oid=int(oid);offset=int(offset)
    from services.applications import list_order_applications
    try:order,apps,total=await list_order_applications(oid,cq.from_user.id,offset,4)
    except ValueError as exc:await error(cq,exc);return
    await cq.answer();text=f"📊 {order['public_code']} · Takliflarni solishtirish\n";rows=[]
    from services.application_views import LABELS
    db=await get_db()
    for app in apps:
        p=await one(db,'SELECT portfolio FROM profiles WHERE user_id=?',(app['applicant_id'],))
        text+=f"\n👤 @{app['username']}\n💰 {app['price']} · ⏱ {app['deadline']}\n{LABELS[app['status']]}\nPortfolio: {p['portfolio'] if p and p['portfolio'] else 'Kiritilmagan'}\n"
        rows.append((f"app:view:{app['id']}",f"@{app['username']} — tafsilotlar"))
    if offset:rows.append((f'xp:compare:{oid}:{max(0,offset-4)}','← Oldingi'))
    if offset+4<total:rows.append((f'xp:compare:{oid}:{offset+4}','Keyingi →'))
    await cq.message.answer(text,reply_markup=nav(rows))

@router.callback_query(F.data.regexp(r'^xp:reconsider:\d+$'))
async def reconsider(cq: CallbackQuery):
    aid=int(cq.data.split(':')[-1])
    async with transaction() as db:
        app=await one(db,'SELECT * FROM order_applications WHERE id=?',(aid,));order=await one(db,'SELECT * FROM orders WHERE id=?',(app['order_id'],)) if app else None
        if not order or order['user_id']!=cq.from_user.id or order['status']!='PUBLISHED' or app['status'] not in ('CLOSED','REJECTED'):
            await error(cq,ValueError('Taklifni qayta ochib bo‘lmaydi.'));return
        await db.execute("UPDATE order_applications SET status='PENDING',decided_at=NULL WHERE id=?",(aid,))
        from services.applications import enqueue
        await enqueue(db,app['applicant_id'],f"{order['public_code']}: buyurtmachi taklifingizni qayta ko‘rib chiqmoqda.")
    await cq.answer('Taklif tanlovga qaytarildi');await flush_notifications(cq.bot)

async def help_begin(message,state,uid,oid=0):
    if oid:
        order=await repo.get_order(oid)
        if not order or uid not in (order['user_id'],order['assigned_to']):await message.answer('Bu kelishuv sizga tegishli emas.');return
    await state.clear();await state.set_state(Edit.help);await state.update_data(help_order=oid or None)
    await message.answer('🆘 Yordam olish\n\nMuammoni yozing. Zakaz yoki to‘lov kodi bo‘lsa qo‘shing. Xabar adminlarga yuboriladi, javob shu botga keladi. Bank karta maxfiy kodi va parolingizni yubormang.',reply_markup=kb.cancel_kb())
@router.message(F.text=='🆘 Yordam olish')
async def help_menu(message: Message,state: FSMContext):await help_begin(message,state,message.from_user.id)
@router.callback_query(F.data.regexp(r'^xp:help:\d+$'))
async def help_callback(cq: CallbackQuery,state: FSMContext):await cq.answer();await help_begin(cq.message,state,cq.from_user.id,int(cq.data.split(':')[-1]))
@router.message(Edit.help,F.text,content_input)
async def help_content(message: Message,state: FSMContext):
    data=await state.get_data()
    try:tid=await xp.support_ticket(message.from_user.id,message.text,data.get('help_order'))
    except ValueError as exc:await message.answer(str(exc));return
    await state.clear();await message.answer(f'✅ Murojaat #{tid} yuborildi. Admin javobi shu chatga keladi.',reply_markup=kb.main_menu_rb(await repo.is_admin(message.from_user.id)));await flush_notifications(message.bot)

@router.callback_query(F.data.regexp(r'^xp:admin:tickets:\d+$'))
async def tickets(cq: CallbackQuery):
    if not await admin(cq):return
    offset=int(cq.data.split(':')[-1]);db=await get_db();cur=await db.execute("SELECT * FROM support_tickets WHERE status='OPEN' ORDER BY id LIMIT 5 OFFSET ?",(offset,));items=await cur.fetchall();await cq.answer()
    if not items:await cq.message.answer('Ochiq yordam so‘rovi yo‘q.');return
    for t in items:await cq.message.answer(f"🆘 #{t['id']} · user {t['user_id']}\nZakaz ID: {t['order_id'] or '—'}\n\n{t['body']}",reply_markup=kb.ipb([(f"xp:answer:{t['id']}",'💬 Javob yozish')]))
    await cq.message.answer('Sahifalar:',reply_markup=kb.ipb([(f'xp:admin:tickets:{max(0,offset-5)}','← Oldingi'),(f'xp:admin:tickets:{offset+5}','Keyingi →')]))
@router.callback_query(F.data.regexp(r'^xp:answer:\d+$'))
async def answer_begin(cq: CallbackQuery,state: FSMContext):
    if not await admin(cq):return
    await state.clear();await state.set_state(Edit.answer);await state.update_data(ticket_id=int(cq.data.split(':')[-1]));await cq.answer();await cq.message.answer('Foydalanuvchiga javob yozing:',reply_markup=kb.cancel_kb())
@router.message(Edit.answer,F.text,content_input)
async def answer_content(message: Message,state: FSMContext):
    data=await state.get_data()
    try:await xp.answer_ticket(data['ticket_id'],message.from_user.id,message.text)
    except ValueError as exc:await message.answer(str(exc));return
    await state.clear();await message.answer('✅ Javob yuborish navbatiga saqlandi.');await flush_notifications(message.bot)

@router.callback_query(F.data.regexp(r'^xp:result:\d+$'))
async def result_options(cq: CallbackQuery):
    oid=int(cq.data.split(':')[-1]);order=await repo.get_order(oid)
    if not order or order['status']!='COMPLETED' or cq.from_user.id not in (order['user_id'],order['assigned_to']):await error(cq,ValueError('Faqat yakunlangan ish ishtirokchisi.'));return
    await cq.answer();await cq.message.answer(f"🏁 {order['public_code']}\n\nFikrni yuborishdan oldin uning ochiq profilga chiqarilishiga rozilik so‘raladi.\n\nNatijani ko‘rsatishga ikkalangiz rozi bo‘lsangiz, zakaz kodi, yo‘nalishi va topshiriq matni ‘Bajarilgan ishlar’da ochiq ko‘rinadi. Rozilikni qaytarib olish mumkin.",reply_markup=kb.ipb([(f'xp:rate:{oid}:{n}',f'{n} ⭐') for n in range(1,6)]+[(f'xp:consent:{oid}:yes','Natijani ochiq ko‘rsatishga roziman'),(f'xp:consent:{oid}:no','Natijani ko‘rsatish roziligini bekor qilish')]))
@router.callback_query(F.data.regexp(r'^xp:rate:\d+:[1-5]$'))
async def review_begin(cq: CallbackQuery,state: FSMContext):
    _,_,oid,n=cq.data.split(':');await state.clear();await state.set_state(Edit.review);await state.update_data(review_order=int(oid),rating=int(n));await cq.answer()
    await cq.message.answer('3–500 belgi bilan hamkorlik haqidagi fikringizni yozing. Keyin ko‘rib, ommaga chiqarishga rozilik bilan yuborasiz.',reply_markup=kb.cancel_kb())
@router.message(Edit.review,F.text,content_input)
async def review_body(message: Message,state: FSMContext):
    if not 3<=len(message.text)<=500:await message.answer('Fikr 3–500 belgi bo‘lsin.');return
    await state.update_data(review_body=message.text)
    await message.answer(message.text+'\n\nBu fikr admin tekshiruvidan so‘ng hamkoringiz profilida ochiq ko‘rinishiga rozimisiz?',reply_markup=kb.ipb([('xp:review_send','Roziman — fikrni yuborish'),('cancel','Bekor qilish')]))
@router.callback_query(F.data=='xp:review_send')
async def review_send(cq: CallbackQuery,state: FSMContext):
    data=await state.get_data()
    if await state.get_state()!=Edit.review.state or not data.get('review_body'):await error(cq,ValueError('Eski tugma.'));return
    try:await xp.review_create(data['review_order'],cq.from_user.id,data['rating'],data['review_body'])
    except ValueError as exc:await error(cq,exc);return
    await state.clear();await cq.answer('Fikr tekshiruvga yuborildi');await flush_notifications(cq.bot)
@router.callback_query(F.data.regexp(r'^xp:consent:\d+:(yes|no)$'))
async def consent(cq: CallbackQuery):
    _,_,oid,answer=cq.data.split(':')
    try:await xp.consent(int(oid),cq.from_user.id,answer=='yes')
    except ValueError as exc:await error(cq,exc);return
    await cq.answer('Rozilik saqlandi' if answer=='yes' else 'Rozilik bekor qilindi')

async def showcase(message,offset=0):
    db=await get_db();cur=await db.execute("SELECT o.* FROM orders o WHERE o.status='COMPLETED' AND EXISTS(SELECT 1 FROM showcase_consent s WHERE s.order_id=o.id AND s.user_id=o.user_id) AND EXISTS(SELECT 1 FROM showcase_consent s WHERE s.order_id=o.id AND s.user_id=o.assigned_to) ORDER BY o.completed_at DESC LIMIT 5 OFFSET ?",(offset,));items=await cur.fetchall()
    if not items:await message.answer('Hozircha ikki tomon rozilik bergan natijalar yo‘q.');return
    for o in items:await message.answer(f"🏆 {o['public_code']} · {xp.CATEGORIES[o['category']]}\nTopshiriq: {clip_units(o['text'] or o['caption'] or '',1500)}\n\nYakunlashni ikki tomon va admin tasdiqlagan.",reply_markup=kb.ipb([(f"xp:profile:{o['assigned_to']}",'Bajaruvchi profili')]))
    await message.answer('Natijalar:',reply_markup=kb.ipb([(f'xp:showcase:{max(0,offset-5)}','← Oldingi'),(f'xp:showcase:{offset+5}','Keyingi →')]))
@router.message(F.text=='🏆 Bajarilgan ishlar')
async def showcase_menu(message: Message,state: FSMContext):await state.clear();await showcase(message)
@router.callback_query(F.data.regexp(r'^xp:showcase:\d+$'))
async def showcase_page(cq: CallbackQuery):await cq.answer();await showcase(cq.message,int(cq.data.split(':')[-1]))
@router.callback_query(F.data.regexp(r'^xp:admin:reviews:\d+$'))
async def review_queue(cq: CallbackQuery):
    if not await admin(cq):return
    db=await get_db();offset=int(cq.data.split(':')[-1]);cur=await db.execute("SELECT * FROM reviews WHERE status='PENDING' ORDER BY id LIMIT 5 OFFSET ?",(offset,));rows=await cur.fetchall();await cq.answer()
    if not rows:await cq.message.answer('Tekshiruvdagi fikr yo‘q.');return
    for r in rows:await cq.message.answer(f"⭐ #{r['id']} · {r['rating']}/5\nMuallif: {r['author_id']} · Profil: {r['target_id']}\n{r['body']}",reply_markup=kb.ipb([(f"xp:rmoderate:{r['id']}:yes",'Tasdiqlash'),(f"xp:rmoderate:{r['id']}:no",'Rad etish')]))
    await cq.message.answer('Sahifalar:',reply_markup=kb.ipb([(f'xp:admin:reviews:{max(0,offset-5)}','← Oldingi'),(f'xp:admin:reviews:{offset+5}','Keyingi →')]))
@router.callback_query(F.data.regexp(r'^xp:rmoderate:\d+:(yes|no)$'))
async def review_moderate(cq: CallbackQuery):
    if not await admin(cq):return
    _,_,rid,answer=cq.data.split(':')
    try:await xp.review_moderate(int(rid),cq.from_user.id,answer=='yes')
    except ValueError as exc:await error(cq,exc);return
    await cq.answer('Saqlandi');await cq.message.edit_reply_markup(reply_markup=None);await flush_notifications(cq.bot)

@router.callback_query(F.data=='xp:analytics')
async def analytics(cq: CallbackQuery):
    if not await admin(cq):return
    db=await get_db();await cq.answer()
    cur=await db.execute("SELECT payment_source,COUNT(*) n,SUM(amount) amount FROM payments WHERE status='APPROVED' GROUP BY payment_source")
    values={r['payment_source']:dict(r) for r in await cur.fetchall()}
    text=f"📈 Natijalar\n\n💳 Karta tushumi: {values.get('CARD',{}).get('amount',0):,} so‘m\n🎁 Balansdan sarflangan: {values.get('WALLET',{}).get('amount',0):,} so‘m\nBepul tasdiqlangan postlar: {values.get('FREE',{}).get('n',0)}\n\nOxirgi 30 kun — bosqichga yetgan noyob foydalanuvchilar:\n"
    cur=await db.execute("SELECT event,COUNT(DISTINCT user_id) n FROM analytics_events WHERE created_at>=datetime('now','-30 days') GROUP BY event")
    counts={r['event']:r['n'] for r in await cur.fetchall()}
    for key,label in [('VISIT','Botdan foydalangan'),('FLOW_STARTED','Post boshlagan'),('DRAFT_SAVED','Matn tayyorlagan'),('SUBMITTED','Tekshiruvga yuborgan'),('PUBLISHED','Posti joylangan'),('APPLICATION','Taklif yuborgan'),('ASSIGNED','Bajaruvchi tanlagan'),('COMPLETED','Ishi yakunlangan')]:text+=f'{label}: {counts.get(key,0)}\n'
    returned=await one(db,"SELECT COUNT(*) n FROM (SELECT user_id FROM analytics_events WHERE event='VISIT' AND created_at>=datetime('now','-30 days') GROUP BY user_id HAVING COUNT(*)>1)")
    speed=await one(db,"SELECT AVG((julianday(first_at)-julianday(published_at))*1440) minutes,COUNT(*) n FROM (SELECT o.published_at,MIN(a.created_at) first_at FROM orders o JOIN order_applications a ON a.order_id=o.id WHERE o.published_at IS NOT NULL GROUP BY o.id) WHERE first_at>=published_at")
    text+=f"\nKamida ikki kunda qaytganlar: {returned['n']}\nBirinchi taklifgacha o‘rtacha vaqt: "+(f"{speed['minutes']:.0f} daqiqa ({speed['n']} zakaz)" if speed['n'] else 'Hali ma’lumot yo‘q')+'\n\nBosqichlar statistikasi v4 o‘rnatilgandan boshlab yig‘iladi. Bular alohida bosqich hisoblari; bir xil foydalanuvchi guruhining konversiyasi deb talqin qilmang.'
    await cq.message.answer(text)

@router.callback_query(F.data.regexp(r'^xp:itemstatus:(ad|order):\d+$'))
async def item_status(cq: CallbackQuery):
    _,_,kind,raw=cq.data.split(':');post=await item(kind,int(raw))
    if not post or post['user_id']!=cq.from_user.id:await error(cq,ValueError('Sizga tegishli emas.'));return
    db=await get_db();column='ad_id' if kind=='ad' else 'order_id'
    p=await one(db,f'SELECT * FROM payments WHERE {column}=? ORDER BY id DESC LIMIT 1',(int(raw),))
    await cq.answer()
    if p and p['status']=='WAITING_ADMIN':await ack(cq.message,p['id'])
    elif p:await cq.message.answer('Tekshiruv tugagan. Post holati: '+kb.STATUS_LABELS.get(post['status'],post['status']),reply_markup=nav([]))
    else:await cq.message.answer('Tekshiruvdagi to‘lov topilmadi.',reply_markup=nav([]))

@router.callback_query(F.data.regexp(r'^xp:discard:(ad|order):\d+$'))
async def discard_ask(cq: CallbackQuery):
    _,_,kind,raw=cq.data.split(':');post=await item(kind,int(raw))
    if not post or post['user_id']!=cq.from_user.id or post['status'] not in xp.EDITABLE or post['deleted_at']:
        await error(cq,ValueError('Qoralama topilmadi yoki allaqachon yuborilgan.'));return
    await cq.answer();await cq.message.answer(f"{post['public_code']} qoralamasi o‘chirilsinmi?",reply_markup=kb.ipb([(f"xp:discardyes:{kind}:{post['id']}:{post['draft_version']}",'Ha, o‘chirish'),(f"xp:resume:{kind}:{post['id']}",'Yo‘q, qaytish')]))

@router.callback_query(F.data.regexp(r'^xp:discardyes:(ad|order):\d+:\d+$'))
async def discard_yes(cq: CallbackQuery,state: FSMContext):
    _,_,kind,raw,version=cq.data.split(':');table='ads' if kind=='ad' else 'orders'
    async with transaction() as db:
        post=await one(db,f'SELECT * FROM {table} WHERE id=?',(int(raw),))
        if not post or post['user_id']!=cq.from_user.id or post['status'] not in xp.EDITABLE or post['draft_version']!=int(version) or post['deleted_at']:
            await error(cq,ValueError('Post o‘zgargan yoki yuborilgan. Qayta oching.'));return
        await db.execute(f"UPDATE {table} SET status='DELETED',deleted_at=? WHERE id=?",(repo._now(),int(raw)))
    data=await state.get_data()
    if data.get('editor_id')==int(raw) and data.get('editor_kind')==kind:await state.clear()
    await cq.answer('Qoralama o‘chirildi');await cq.message.edit_text('🗑 Qoralama o‘chirildi.')
