"""Consistent Telegram cards. User-provided content is always HTML-escaped."""
from html import escape
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import database.repo as repo
from services.applications import get_application

LABELS = {'PENDING':'⏳ Javob kutilmoqda','ACCEPTED':'✅ Bajaruvchi tanlangan',
          'REJECTED':'Taklif rad etildi','CLOSED':'Tanlov yakunlandi'}


def markup(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def action(text, data):
    return InlineKeyboardButton(text=text,callback_data=data)


def chat_button(user, label='💬 Yozish'):
    if user and user.get('username'):
        return InlineKeyboardButton(text=label,url=f"https://t.me/{user['username']}")
    return None


def username(user):
    return '@'+user['username'] if user and user.get('username') else 'Username mavjud emas'


def application_text(app, order, user, owner_view=True):
    heading = 'YANGI SO‘ROV' if owner_view and app['status']=='PENDING' else 'SO‘ROV TAFSILOTLARI'
    name = username(user)
    if owner_view:
        name += f" · yuborilganda: @{app['username']} (ID: {app['applicant_id']})"
    label = 'Nomzod' if owner_view else 'Buyurtmachi'
    return (f"<b>{escape(heading)}</b>\n\n"
            f"👤 {label}: <b>{escape(name)}</b>\n"
            f"🆔 Zakaz: <code>{escape(order['public_code'])}</code>\n"
            f"📌 {escape(LABELS.get(app['status'],app['status']))}\n\n"
            f"<b>Taklif narxi</b>\n{escape(app['price'])}\n\n"
            f"<b>Bajarish muddati</b>\n{escape(app['deadline'])}\n\n"
            f"<b>Izoh</b>\n{escape(app['proposal'])}")


def application_keyboard(app,order,user,owner_view=True):
    rows=[]
    rows.append([action('👤 Profil va fikrlar',f"xp:profile:{app['applicant_id'] if owner_view else order['user_id']}")])
    chat=chat_button(user)
    if not chat and owner_view:
        chat=InlineKeyboardButton(text="💬 Saqlangan username",url=f"https://t.me/{app['username']}")
    if chat:
        rows.append([chat])
    if owner_view:
        if app['status']=='PENDING' and order['status']=='PUBLISHED' and not order['assigned_to']:
            rows.append([action('✅ Shu bajaruvchini tanlash',f"app:accept:{app['id']}")])
            rows.append([action('❌ Rad etish',f"app:reject:{app['id']}")])
        if app['status'] in ('REJECTED','CLOSED') and order['status']=='PUBLISHED':
            rows.append([action('🔄 Qayta ko‘rib chiqish',f"xp:reconsider:{app['id']}")])
        rows.append([action('📊 Takliflarni solishtirish',f"xp:compare:{order['id']}:0")])
        rows.append([action('📨 Barcha so‘rovlar',f"apps:order:{order['id']}:0")])
    else:
        if order['status'] in ('ASSIGNED','COMPLETION_REVIEW') and order['assigned_to']==app['applicant_id']:
            rows.append([action('🏁 Ish tugaganini tasdiqlash',f"finish:confirm:{order['id']}")])
        rows.append([action('📨 Mening so‘rovlarim','apps:mine:0')])
    if order['status']=='COMPLETED':
        rows.append([action('⭐ Fikr va natija',f"xp:result:{order['id']}")])
    rows.append([action('🆘 Yordam olish',f"xp:help:{order['id']}" if order['assigned_to']==app['applicant_id'] or owner_view else 'xp:help:0')])
    return markup(rows)


async def notification_card(event_type, entity_id):
    if event_type=='MATCHED_ORDER':
        return None  # handled in notifications with recipient subscription checks
    app=await get_application(entity_id)
    if not app:
        return None
    order=await repo.get_order(app['order_id'])
    if not order:
        return None
    owner_view=event_type=='APPLICATION'
    user=await repo.get_user(app['applicant_id'] if owner_view else order['user_id'])
    text=application_text(app,order,user,owner_view)
    if event_type=='ACCEPTED':
        text=f"<b>🎉 Siz bajaruvchi sifatida tanlandingiz!</b>\n\n"+text
    return text,application_keyboard(app,order,user,owner_view)
