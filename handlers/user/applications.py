"""Code lookup, free proposals and private-chat links; no chat relay."""
import re
from html import escape
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
import database.repo as repo
import keyboards.keyboards as kb
from states import ApplicationStates, LookupStates, UnlockStates
from services.flow import abandon_pending_flow
from services.applications import (create_application,decide_application,get_application,
                                   find_application,list_order_applications,list_my_applications)
from services.application_views import (action,markup,chat_button,username,application_text,
                                       application_keyboard,LABELS)
from services.notifications import flush_notifications
from services.order_channel import sync_order_post

router=Router(name='user_applications')
router.message.filter(F.chat.type=='private')
router.callback_query.filter((F.message.chat.type=='private') | F.data.startswith('order:apply:') | F.data.startswith('unlock:start:'))
PAGE=6


async def remember(user):
    return await repo.get_or_create_user(user.id,user.username,user.first_name,user.last_name)


async def require_username(message, user):
    await remember(user)
    if user.username:
        return True
    await message.answer(
        "<b>Telegram username kerak</b>\n\n"
        "Boshqalar «Yozish» tugmasi orqali shaxsiy chattingizga o'tishi uchun "
        "Telegram → Sozlamalar → Username bo'limida username o'rnating.\n\n"
        "So‘ng pastdagi tekshirish tugmasini bosing. Kiritilgan ma’lumotlar saqlanadi.",parse_mode='HTML',reply_markup=kb.ipb([('username:retry','🔄 Username’ni tekshirish')]))
    return False


async def show_order(bot,chat_id,user_id,code):
    order=await repo.get_order_by_code(code.strip().upper())
    if not order or order['status'] not in ('PUBLISHED','ASSIGNED','COMPLETED','DELETED','COMPLETION_REVIEW'):
        await bot.send_message(chat_id,"Zakaz topilmadi yoki hali kanalga joylanmagan. Kodni tekshiring: ZK-4821.")
        return
    label={'COMPLETION_REVIEW':'⏳ Ikki tomon tasdiqladi; admin tekshirmoqda','PUBLISHED':'🟢 Takliflar qabul qilinmoqda','ASSIGNED':'✅ Bajaruvchi tanlangan — so‘rovlar yopilgan',
           'COMPLETED':'🏁 Ish bajarilgan','DELETED':'🔒 Zakaz yopilgan'}[order['status']]
    await bot.send_message(chat_id,f"<b>ZAKAZ TAFSILOTLARI</b>\n\n🆔 <code>{escape(order['public_code'])}</code>\n{label}",parse_mode='HTML')
    body=order.get('text') or order.get('caption') or ''
    # Preserve full user content, even on legacy long posts.
    from services.order_channel import clip_units
    while body:
        part=clip_units(body,3800)
        await bot.send_message(chat_id,part)
        body=body[len(part):]
    if user_id==order['user_id']:
        await bot.send_message(chat_id,"Siz ushbu zakaz egasisiz.",reply_markup=kb.item_detail_kb('order',order['id'],order['status']))
        return
    existing=await find_application(order['id'],user_id)
    if existing:
        await bot.send_message(chat_id,f"Sizning so‘rovingiz: {LABELS[existing['status']]}",
                               reply_markup=markup([[action('📋 So‘rovni ko‘rish',f"app:view:{existing['id']}")]]))
    elif order['status']=='PUBLISHED':
        await bot.send_message(chat_id,
            "<b>Taklifingizni yuboring</b>\n\nIshtirok etish bepul. Narx, muddat va qisqa izoh kiriting. "
            "Buyurtmachi nomzodlar bilan shaxsiy chatda gaplashib, bittasini tanlaydi.",
            parse_mode='HTML',reply_markup=kb.developer_order_kb(order['public_code']))


async def lookup_begin(message,state):
    await abandon_pending_flow(state)
    await state.set_state(LookupStates.awaiting_code)
    await message.answer("<b>Zakazni topish</b>\n\nKanaldagi kodni yuboring. Masalan: <code>ZK-4821</code>",parse_mode='HTML',reply_markup=kb.cancel_kb())


@router.message(StateFilter(None,LookupStates.awaiting_code),F.text.regexp(r'(?i)^\s*ZK-\d{4,48}\s*$'))
async def lookup_code(message: Message,state: FSMContext):
    await remember(message.from_user)
    await state.clear()
    await show_order(message.bot,message.chat.id,message.from_user.id,message.text)


@router.message(LookupStates.awaiting_code)
async def lookup_wrong(message: Message):
    await message.answer("Kod shakli: ZK-4821. Kanaldagi kodni nusxalab yuboring.",reply_markup=kb.cancel_kb())


@router.callback_query(F.data.startswith('order:apply:'))
@router.callback_query(F.data.startswith('unlock:start:'))
async def legacy_order_link(cq: CallbackQuery):
    await remember(cq.from_user)
    await cq.answer()
    try:
        await show_order(cq.bot,cq.from_user.id,cq.from_user.id,cq.data.split(':',2)[2])
    except Exception:
        # Old channel buttons cannot open a DM until the user starts the bot.
        # Answering again with an alert gives a useful instruction instead of silence.
        await cq.answer("Botning shaxsiy chatida /start ni bosing va zakaz kodini yuboring.",show_alert=True)


@router.callback_query(F.data.startswith('unlock:pay_'))
async def legacy_payment_disabled(cq: CallbackQuery,state: FSMContext):
    if (await state.get_data()).get('kind')=='unlock':
        await state.clear()
    await cq.answer("So‘rov yuborish bepul. Zakaz kodini botga yuboring.",show_alert=True)


@router.message(UnlockStates.awaiting_receipt)
async def legacy_receipt_disabled(message: Message,state: FSMContext):
    await state.clear()
    await message.answer("Kontakt uchun to‘lov bekor qilingan. Zakaz kodini yuborib, bepul so‘rov qoldiring. "
                         "Oldin pul o‘tkazgan bo‘lsangiz, admin bilan bog‘laning.")


@router.callback_query(F.data.startswith('application:start:'))
async def application_start(cq: CallbackQuery,state: FSMContext):
    await cq.answer()
    if not await require_username(cq.message,cq.from_user):
        return
    order=await repo.get_order_by_code(cq.data.split(':',2)[2])
    if not order or order['status']!='PUBLISHED' or order['assigned_to'] or order['deleted_at']:
        await cq.message.answer("Bu zakaz uchun so‘rovlar yopilgan.")
        return
    if order['user_id']==cq.from_user.id:
        await cq.message.answer("O‘z zakazingizga so‘rov yubora olmaysiz.")
        return
    if await find_application(order['id'],cq.from_user.id):
        await cq.message.answer("Siz bu zakazga allaqachon so‘rov yuborgansiz.",reply_markup=markup([[action('📨 Mening so‘rovlarim','apps:mine:0')]]))
        return
    await abandon_pending_flow(state)
    await state.update_data(application_order_id=order['id'],application_code=order['public_code'])
    await state.set_state(ApplicationStates.price)
    await cq.message.answer(
        f"<b>TAKLIF YUBORISH · 1/3</b>\n🆔 <code>{escape(order['public_code'])}</code>\n\n"
        "<b>Qancha narx taklif qilasiz?</b>\nSummani so‘mda yozing: 500000.\nAniq bo‘lmasa «Kelishiladi»ni tanlang.",
        parse_mode='HTML',reply_markup=markup([[action('Kelishiladi',f"application:negotiable:{order['id']}")],[action('Bekor qilish','cancel')]]))


async def price_done(message,state,price):
    await state.update_data(application_price=price)
    await state.set_state(ApplicationStates.deadline)
    await message.answer("<b>TAKLIF YUBORISH · 2/3</b>\n\n<b>Bajarish muddati qancha?</b>\nMasalan: 5 kun yoki 2 hafta.",parse_mode='HTML',reply_markup=kb.cancel_kb())


@router.callback_query(F.data.startswith('application:negotiable:'))
async def negotiable(cq: CallbackQuery,state: FSMContext):
    data=await state.get_data()
    if await state.get_state()!=ApplicationStates.price.state or str(data.get('application_order_id'))!=cq.data.split(':')[-1]:
        await cq.answer("Bu eski tugma.",show_alert=True)
        return
    await cq.answer()
    await price_done(cq.message,state,'Kelishiladi')


@router.message(ApplicationStates.price,F.text)
async def application_price(message: Message,state: FSMContext):
    raw=message.text.strip().replace(' ','').replace(',','')
    if raw.lower()=='kelishiladi':
        value='Kelishiladi'
    elif raw.isascii() and raw.isdigit() and 0<int(raw)<=1_000_000_000:
        value=f"{int(raw):,} so‘m"
    else:
        await message.answer("Musbat butun son kiriting (masalan 500000) yoki «Kelishiladi» deb yozing.")
        return
    await price_done(message,state,value)


@router.message(ApplicationStates.deadline,F.text)
async def application_deadline(message: Message,state: FSMContext):
    value=message.text.strip()
    if not 1<=len(value)<=100:
        await message.answer("Muddatni 1–100 belgi bilan yozing.")
        return
    await state.update_data(application_deadline=value)
    await state.set_state(ApplicationStates.proposal)
    await message.answer("<b>TAKLIF YUBORISH · 3/3</b>\n\n<b>Qisqa izoh yozing</b>\nTajribangiz, ishga yondashuvingiz yoki portfolio havolangizni kiriting. Maksimal 800 belgi.",parse_mode='HTML',reply_markup=kb.cancel_kb())


@router.message(ApplicationStates.proposal,F.text)
async def application_proposal(message: Message,state: FSMContext):
    value=message.text.strip()
    if not 1<=len(value)<=800:
        await message.answer("Izoh 1–800 belgidan iborat bo‘lsin.")
        return
    await state.update_data(application_proposal=value)
    data=await state.get_data()
    await state.set_state(ApplicationStates.preview)
    await message.answer(
        f"<b>TAKLIFNI TEKSHIRING</b>\n\n👤 {escape('@'+message.from_user.username if message.from_user.username else 'Username kerak')}\n"
        f"🆔 <code>{escape(data['application_code'])}</code>\n\n"
        f"<b>Narx:</b> {escape(data['application_price'])}\n"
        f"<b>Muddat:</b> {escape(data['application_deadline'])}\n\n{escape(value)}\n\n"
        "Yuborilgach, buyurtmachi username’ingiz va taklifingizni ko‘radi. Suhbat Telegramdagi shaxsiy chatda bo‘ladi.",
        parse_mode='HTML',reply_markup=markup([
            [action('📩 Bepul yuborish',f"application:send:{data['application_order_id']}")],
            [action('✏️ Qayta yozish',f"application:start:{data['application_code']}")],
            [action('Bekor qilish','cancel')]]))


@router.callback_query(F.data.startswith('application:send:'))
async def application_send(cq: CallbackQuery,state: FSMContext):
    data=await state.get_data()
    if await state.get_state()!=ApplicationStates.preview.state or str(data.get('application_order_id'))!=cq.data.split(':')[-1]:
        await cq.answer("Bu taklif yuborilgan yoki eski tugma bosildi.",show_alert=True)
        return
    if not await require_username(cq.message,cq.from_user):
        await cq.answer()
        return
    try:
        aid=await create_application(data['application_order_id'],cq.from_user.id,data['application_price'],data['application_deadline'],data['application_proposal'])
    except ValueError as exc:
        await cq.answer(str(exc),show_alert=True)
        return
    await state.clear()
    await cq.answer("So‘rov saqlandi")
    app=await get_application(aid)
    order=await repo.get_order(app['order_id'])
    owner=await repo.get_user(order['user_id'])
    await cq.message.edit_text(
        "<b>✅ So‘rovingiz yuborildi</b>\n\n"+application_text(app,order,owner,False),
        parse_mode='HTML',reply_markup=application_keyboard(app,order,owner,False))
    await flush_notifications(cq.bot)


@router.message(StateFilter(ApplicationStates.price,ApplicationStates.deadline,ApplicationStates.proposal,ApplicationStates.preview))
async def application_wrong(message: Message):
    await message.answer("Joriy bosqich uchun matn yuboring yoki ko‘rsatilgan tugmadan foydalaning.",reply_markup=kb.cancel_kb())


async def my_applications(message,user_id,offset=0):
    items,total=await list_my_applications(user_id,offset,PAGE)
    rows=[[action(f"{a['public_code']} · {LABELS[a['status']]}",f"app:view:{a['id']}")] for a in items]
    nav=[]
    if offset>0: nav.append(action('← Oldingi',f'apps:mine:{max(0,offset-PAGE)}'))
    if offset+PAGE<total: nav.append(action('Keyingi →',f'apps:mine:{offset+PAGE}'))
    if nav: rows.append(nav)
    await message.answer(f"<b>MENING SO‘ROVLARIM</b>\n\nJami: {total}"+("\nHozircha so‘rov yubormagansiz. «Zakazni topish» orqali boshlang." if not total else "\nTafsilotlarni ko‘rish uchun tanlang."),parse_mode='HTML',reply_markup=markup(rows) if rows else None)


@router.callback_query(F.data.regexp(r'^apps:mine:\d+$'))
async def my_applications_page(cq: CallbackQuery):
    await cq.answer()
    await my_applications(cq.message,cq.from_user.id,int(cq.data.split(':')[-1]))


@router.callback_query(F.data.regexp(r'^apps:order:\d+:\d+$'))
async def order_applications(cq: CallbackQuery):
    _,_,oid,offset=cq.data.split(':')
    offset=int(offset)
    try:
        order,items,total=await list_order_applications(int(oid),cq.from_user.id,offset,PAGE)
    except ValueError as exc:
        await cq.answer(str(exc),show_alert=True)
        return
    await cq.answer()
    rows=[]
    for app in items:
        user=await repo.get_user(app['applicant_id'])
        rows.append([action(f"{username(user)} · {LABELS[app['status']]}",f"app:view:{app['id']}")])
    nav=[]
    if offset>0: nav.append(action('← Oldingi',f'apps:order:{oid}:{max(0,offset-PAGE)}'))
    if offset+PAGE<total: nav.append(action('Keyingi →',f'apps:order:{oid}:{offset+PAGE}'))
    if nav: rows.append(nav)
    rows.append([action('← Zakazga qaytish',f'order:view:{oid}')])
    await cq.message.answer(f"<b>KELGAN SO‘ROVLAR</b>\n\n🆔 <code>{escape(order['public_code'])}</code>\nJami: {total}\n\n"+
        ("Nomzodni tanlab, taklifini ko‘ring. «Yozish» shaxsiy chatni ochadi." if total else "Hozircha so‘rov yo‘q. Yangi so‘rov kelganda shu yerda ko‘rinadi."),parse_mode='HTML',reply_markup=markup(rows))


@router.callback_query(F.data.regexp(r'^app:view:\d+$'))
async def application_view(cq: CallbackQuery):
    app=await get_application(int(cq.data.split(':')[-1]))
    order=await repo.get_order(app['order_id']) if app else None
    if not order or cq.from_user.id not in (order['user_id'],app['applicant_id']):
        await cq.answer("Bu so‘rov sizga tegishli emas.",show_alert=True)
        return
    await cq.answer()
    owner_view=cq.from_user.id==order['user_id']
    user=await repo.get_user(app['applicant_id'] if owner_view else order['user_id'])
    await cq.message.answer(application_text(app,order,user,owner_view),parse_mode='HTML',reply_markup=application_keyboard(app,order,user,owner_view))


@router.callback_query(F.data.regexp(r'^app:(accept|reject):\d+$'))
async def decide(cq: CallbackQuery):
    _,decision,raw_id=cq.data.split(':')
    try:
        oid=await decide_application(int(raw_id),cq.from_user.id,decision=='accept')
    except ValueError as exc:
        await cq.answer(str(exc),show_alert=True)
        return
    await cq.answer("Bajaruvchi tanlandi" if decision=='accept' else "So‘rov rad etildi")
    app=await get_application(int(raw_id))
    order=await repo.get_order(oid)
    user=await repo.get_user(app['applicant_id'])
    text=("<b>✅ Bajaruvchi tanlandi. Yangi so‘rovlar yopildi.</b>\n\n" if decision=='accept' else "<b>So‘rov rad etildi. Boshqa nomzodlarni ko‘rishingiz mumkin.</b>\n\n")
    await cq.message.edit_text(text+application_text(app,order,user),parse_mode='HTML',reply_markup=application_keyboard(app,order,user))
    if decision=='accept':
        await sync_order_post(cq.bot,oid)
    await flush_notifications(cq.bot)
