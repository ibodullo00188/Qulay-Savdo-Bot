import config

from aiogram.types import (
    CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove)
from aiogram.utils.keyboard import InlineKeyboardBuilder


def button_style(text):
    """Restrained accents: primary actions, positive confirmation and danger."""
    if text == BTN_AD:
        return 'success'
    if text in (BTN_ORDER, BTN_ADMIN):
        return 'primary'
    if text.startswith(('🗑', '❌', '🚫', '➖ Admin')):
        return 'danger'
    if text.startswith(('✅ Tasdiq', '✅ Davom', '✅ Yubor', '✅ Saqla')):
        return 'success'
    return None


def reply_button(text):
    style = button_style(text)
    return KeyboardButton(text=text, **({'style': style} if style else {}))


def reply_row(*labels):
    return ReplyKeyboardMarkup(
        keyboard=[[reply_button(l) for l in labels]],
        resize_keyboard=True)


def multi_reply(rows):
    return ReplyKeyboardMarkup(
        keyboard=[[reply_button(l) for l in r] for r in rows],
        resize_keyboard=True)


def ipb(items, widths=(1,)):
    """items: har biri (callback_data, matn) juftligi bo'lgan ro'yxat."""
    b = InlineKeyboardBuilder()
    for row in items:
        cb, text = row
        style = button_style(text)
        b.button(text=text, callback_data=cb, **({"style": style} if style else {}))
    return b.adjust(*widths).as_markup()


# ================= ASOSIY MENYU (barcha foydalanuvchilar) =================
BTN_AD = "📢 Tayyor dasturimni sotaman"
BTN_ORDER = "🛠 Dasturchi topaman"
BTN_MY_ADS = "📋 Mening e'lonlarim"
BTN_MY_ORDERS = "📋 Mening zakazlarim"
BTN_REFERRAL = "🎁 E’lon uchun bonus olish"
BTN_RULES = "ℹ️ Qoidalar"
BTN_MORE = "➕ Qo‘shimcha"
BTN_ADMIN = "🛠 Admin panel"
BTN_CANCEL = "❌ Bekor qilish"
BTN_HOME = "🏠 Bosh menyu"
BTN_FIND_ORDER = "🔎 Kod orqali topish"
BTN_MY_APPLICATIONS = "📨 Yuborgan takliflarim"


def main_menu_rb(is_admin: bool = False):
    rows = [[BTN_AD, BTN_ORDER], [BTN_MORE]]
    if is_admin:
        rows.append([BTN_ADMIN])
    return multi_reply(rows)


def additional_menu_rb():
    rows = [
        [BTN_MY_ADS, BTN_MY_ORDERS],
        ["🔎 Ochiq zakazlar", BTN_MY_APPLICATIONS],
        ["📝 Qoralamalar", "👤 Profilim"],
        ["🔔 Mos zakazlar", "🏆 Bajarilgan ishlar"],
        [BTN_FIND_ORDER, "🆘 Yordam olish"],
        [BTN_REFERRAL, BTN_RULES],
    ]
    rows.append([BTN_HOME])
    return multi_reply(rows)


def cancel_reply_kb():
    return reply_row(BTN_CANCEL)


def admin_menu_rb():
    return multi_reply([["🛠 Admin panel"], [BTN_HOME]])


def cancel_kb():
    return ipb([("cancel", "❌ Bekor qilish")])


# -================= AD (e'lon) oqimi =================
def ad_guarantee_kb():
    return ipb([("ad:guarantee", "ℹ️ Joylashtirish qanday ishlaydi?"),
                ("cancel", "❌ Bekor qilish")])


def order_guarantee_kb():
    return ipb([("order:guarantee", "ℹ️ Joylashtirish qanday ishlaydi?"),
                ("cancel", "❌ Bekor qilish")])


def guarantee_info_kb():
    return ipb([("ok", "👌 Tushunarli")])


def payment_choice_kb(prefix: str, item_id: int):
    """prefix: 'ad' | 'order' | 'unlock' — balans yetarli bo'lganda."""
    return ipb([
        (f"{prefix}:pay_balance:{item_id}", "💰 Balansdan to'lash"),
        (f"{prefix}:pay_card:{item_id}", "💳 Karta orqali"),
        ("cancel", "❌ Bekor qilish"),
    ], widths=(1, 1, 1))


def pay_card_only_kb():
    return ipb([("cancel", "❌ Bekor qilish")])


# ================= ADMIN: to'lov moderatsiyasi =================
def receipt_admin_kb(payment_id):
    return ipb([
        (f"pay:approve:{payment_id}", "✅ Tasdiqlash"),
        (f"pay:reject:{payment_id}", "❌ Rad etish"),
        (f"pay:preview:{payment_id}", "📋 E’lon / zakazni ko‘rish"),
        (f"upgrade:edit:{payment_id}", "✏️ Postni tahrirlash"),
    ], widths=(2,))


# ================= Mening e'lonlarim / zakazlarim =================
def items_list_kb(items, kind: str):
    """kind: 'ad' | 'order' — ro'yxatdagi har bir element uchun tugma."""
    rows = []
    for it in items:
        title = f"{it['public_code']} — {STATUS_LABELS.get(it['status'], it['status'])}"
        rows.append((f"{kind}:view:{it['id']}", title))
    if not rows:
        return None
    return ipb(rows, widths=(1,))


def item_detail_kb(kind: str, item_id: int, status: str):
    """Har qanday holatdagi (faqat allaqachon o'chirilgan bo'lmasa) e'lon/
    zakaz uchun "O'chirish" tugmasini ko'rsatadi — avval faqat PUBLISHED
    holatida ko'rinar edi, shu sabab bekor qilingan/rad etilgan/to'lov
    kutayotgan yozuvlarni foydalanuvchi o'zi hech qachon o'chira olmas,
    kvotasi abadiy band bo'lib qolar edi. "Bajarildi" tugmasi esa faqat
    kanalda joylashtirilgan (PUBLISHED) elementlar uchun mantiqan to'g'ri."""
    rows = []
    if kind=='order':
        rows.append((f"apps:order:{item_id}:0","📨 Kelgan so‘rovlar"))
        if status in ('ASSIGNED','COMPLETION_REVIEW'):
            rows.append((f"finish:confirm:{item_id}","🏁 Ish tugaganini tasdiqlash"))
            rows.append((f"reopen:ask:{item_id}","🔄 Zakazni qayta ochish (maks. 2)"))
        if status not in ('DELETED','ASSIGNED','COMPLETED','COMPLETION_REVIEW'):
            rows.append((f"order:delete:{item_id}","🗑 Zakazni yopish"))
    else:
        if status=='PUBLISHED':
            rows.append((f"ad:complete:{item_id}","✅ Bajarildi deb belgilash"))
        if status!='DELETED':
            rows.append((f"ad:delete:{item_id}","🗑 O'chirish"))
    if kind=='order':
        rows.append((f"xp:compare:{item_id}:0","📊 Takliflarni solishtirish"))
        if status=='COMPLETED':rows.append((f"xp:result:{item_id}","⭐ Fikr va natija"))
    if status=='WAITING_ADMIN':rows.append((f"xp:itemstatus:{kind}:{item_id}","⏱ Tekshirish muddati"))
    rows.append((f"xp:help:{item_id}" if kind=='order' else 'xp:help:0','🆘 Yordam olish'))
    rows.append((f"xp:mine:{kind}:all:0","⬅️ Ro‘yxatga"))
    return ipb(rows)


STATUS_LABELS = {
    "COMPLETION_REVIEW": "⏳ yakunlash admin tasdig‘ida",
    "ASSIGNED": "✅ bajaruvchi tanlangan",
    "READY_TO_PUBLISH": "kanalga joylash kutilmoqda",
    "PUBLISHING": "kanalga joylanmoqda",
    "PUBLICATION_FAILED": "kanalga joylanmadi",
    "PUBLICATION_REVIEW": "kanaldagi natijani admin tekshiradi",
    "DRAFT": "qoralama",
    "WAITING_PAYMENT": "to'lov kutilmoqda",
    "WAITING_RECEIPT": "chek kutilmoqda",
    "WAITING_ADMIN": "admin tasdig'i kutilmoqda",
    "PROCESSING": "to'lov qayta ishlanmoqda",
    "APPROVED": "tasdiqlangan",
    "PUBLISHED": "kanalda e'lon qilingan",
    "REJECTED": "rad etilgan",
    "COMPLETED": "bajarilgan",
    "CANCELLED": "bekor qilingan",
    "DELETED": "o'chirilgan",
}


# ================= Kanal posti tugmalari =================
def copy_code_button(public_code):
    return InlineKeyboardButton(text=f"📋 {public_code} — nusxalash", copy_text=CopyTextButton(text=public_code))


def order_channel_kb(public_code):
    if config.BOT_USERNAME:
        return InlineKeyboardMarkup(inline_keyboard=[
            [copy_code_button(public_code)],
            [InlineKeyboardButton(text="📩 Bepul so‘rov yuborish",url=f"https://t.me/{config.BOT_USERNAME}?start=order_{public_code}")],
            [InlineKeyboardButton(text="⚠️ Shikoyat qilish",url=f"https://t.me/{config.BOT_USERNAME}?start=complaint_{public_code}")]])
    return ipb([(f"order:apply:{public_code}","📩 Bepul so‘rov yuborish"),
                (f"complaint:start:{public_code}","⚠️ Shikoyat qilish")])


def ad_channel_kb(public_code):
    if config.BOT_USERNAME:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⚠️ Shikoyat qilish",url=f"https://t.me/{config.BOT_USERNAME}?start=complaint_{public_code}")]])
    return ipb([(f"complaint:start:{public_code}","⚠️ Shikoyat qilish")])


def developer_order_kb(public_code, already_unlocked=False):
    return ipb([(f"application:start:{public_code}","📩 Bepul so‘rov yuborish")])


def closed_order_channel_kb(public_code, status=None):
    if config.BOT_USERNAME:
        return InlineKeyboardMarkup(inline_keyboard=[[copy_code_button(public_code)], [
            InlineKeyboardButton(text="🏁 Zakaz to‘liq tugatildi" if status=="COMPLETED" else "📋 Zakaz holatini ko‘rish",url=f"https://t.me/{config.BOT_USERNAME}?start=order_{public_code}")]])
    return ipb([(f"order:apply:{public_code}","📋 Zakaz holatini ko‘rish")])


# ================= Shikoyat (admin) =================
def complaint_admin_kb(complaint_id):
    return ipb([
        (f"complaint:delete_post:{complaint_id}", "🗑 Postni o'chirish"),
        (f"complaint:block_user:{complaint_id}", "🚫 Userni bloklash"),
        (f"complaint:dismiss:{complaint_id}", "✅ E'tiborsiz qoldirish"),
    ], widths=(1, 1, 1))


# ================= Broadcast =================
def broadcast_kb():
    return ipb([
        ("bcast:confirm", "✅ Yuborish"),
        ("bcast:cancel", "❌ Bekor"),
    ], widths=(2,))


# ================= Admin panel bosh menyusi =================
ADMIN_SECTIONS = [
        ("ig:admin", "🤝 Taklif orqali joylashtirish"),
        ("backup:create", "📦 Backup yaratish"),
        ("backup:restore", "♻️ Backupni tiklash"),
        ("xp:admin:tickets:0", "🆘 Yordam so‘rovlari"),
        ("xp:admin:reviews:0", "⭐ Fikrlar"),
        ("xp:analytics", "📈 Natijalar va tushum"),
        ("upgrade:queue", "⏳ Barcha kutilayotganlar"),
        ("upgrade:import", "➕ Zakaz / e’lon qo‘shish"),
        ("upgrade:money", "💰 Pullik tizimni yoqish / o‘chirish"),
        ("admin:pending", "💳 Cheklar"),
        ("admin:ads", "📢 E'lonlar"),
        ("admin:orders", "🔵 Zakazlar"),
        ("admin:users", "👥 Foydalanuvchilar"),
        ("admin:complaints", "⚠️ Shikoyatlar"),
        ("admin:stats", "📊 Statistika"),
        ("admin:settings", "⚙️ Sozlamalar"),
        ("admin:sign", "✍️ Kanal imzosi"),
        ("admin:broadcast", "📣 Reklama yuborish"),
        ("admin:admins", "👤 Adminlar"),
    ]
ADMIN_SECTION_ACTIONS = {label: action for action, label in ADMIN_SECTIONS}


def admin_sections_kb():
    labels = list(ADMIN_SECTION_ACTIONS)
    rows = [labels[i:i + 2] for i in range(0, len(labels), 2)]
    rows.append([BTN_HOME])
    return ReplyKeyboardMarkup(
        keyboard=[[reply_button(label) for label in row] for row in rows],
        resize_keyboard=True, is_persistent=True)



def admins_menu_kb():
    return ipb([
        ("admin:add_admin", "➕ Admin qo'shish"),
        ("admin:remove_admin", "➖ Admin olib tashlash"),
        ("admin:sections", "⬅️ Orqaga"),
    ], widths=(1, 1, 1))


def pending_pagination_kb(offset, total, per_page=1):
    """Kutilayotgan to'lovlar/shikoyatlar ro'yxatida "Keyingisi" tugmasi."""
    rows = []
    if offset + per_page < total:
        rows.append((f"admin:pending:next:{offset + per_page}", "➡️ Keyingisi"))
    rows.append(("admin:sections", "⬅️ Admin menyu"))
    return ipb(rows, widths=(1,))


def complaints_pagination_kb(offset, total, complaint_id, per_page=1):
    rows = [
        (f"complaint:delete_post:{complaint_id}", "🗑 Postni o'chirish"),
        (f"complaint:block_user:{complaint_id}", "🚫 Userni bloklash"),
        (f"complaint:dismiss:{complaint_id}", "✅ E'tiborsiz qoldirish"),
    ]
    if offset + per_page < total:
        rows.append((f"admin:complaints:next:{offset + per_page}", "➡️ Keyingisi"))
    rows.append(("admin:sections", "⬅️ Admin menyu"))
    return ipb(rows, widths=(1, 1, 1, 1))


def admin_item_detail_kb(kind: str, item_id: int, page_offset: int = 0):
    """Admin panelidagi ro'yxatdagi bitta e'lon/zakazni o'chirish imkoni
    (avval admin panelida individual item'ni o'chirish imkoniyati umuman
    yo'q edi)."""
    section = "admin:ads" if kind == "ad" else "admin:orders"
    return ipb([
        (f"admin:republish:{kind}:{item_id}", "🔄 Kanalga qayta joylash"),
        (f"admin:item_delete:{kind}:{item_id}:{page_offset}", "🗑 O'chirish"),
        (f"{section}:{page_offset}", "⬅️ Ro'yxatga"),
    ], widths=(1, 1))


def settings_list_kb(keys_labels):
    rows = [(f"admin:setkey:{k}", label) for k, label in keys_labels]
    rows.append(("admin:sections", "⬅️ Orqaga"))
    return ipb(rows, widths=(1,))


def back_to_admin_kb():
    return ipb([("admin:sections", "⬅️ Admin menyu")])
