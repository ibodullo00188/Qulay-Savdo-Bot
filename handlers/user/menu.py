"""Asosiy menyu tugmalari uchun ENG YUQORI USTUVORLIKKA ega router.

MUAMMO: AdStates/OrderStates kabi holatlarda F.text bilan ishlaydigan
"catch-all" handlerlar (masalan ad_content_text) o'z routerida (ads.py)
boshqa modullarning (mine.py, referral.py, admin/panel.py) menyu
tugmalaridan OLDINROQ ro'yxatdan o'tgani uchun, foydalanuvchi masalan
"📢 E'lon berish" oqimida turib "📋 Mening e'lonlarim" bossa — bu matn
avval ads.py dagi umumiy F.text ushlagichga tushib, noto'g'ri xabar
("Rasm majburiy" va h.k.) qaytarardi. Faqat "🏠 Bosh menyu" va
"❌ Bekor qilish" ishlardi, chunki ular state'dan mustaqil edi va
routerda birinchi turardi (start.py, eng birinchi ro'yxatdan o'tgan
router).

YECHIM: shu barcha menyu tugmalarini state'dan mustaqil, eng ustuvor
(main.py'da start.py'dan keyin, lekin ads/orders/mine/referral/admin'dan
OLDIN ro'yxatdan o'tadigan) shu routerga chiqaramiz. Har biri avval
`abandon_pending_flow()` orqali yarim qolgan oqimni yakunlaydi (bu bir
yo'la #1 va #4 buglarni ham to'g'irlaydi — chunki turli oqimlar bir xil
FSM state kalitlaridan (kind/item_id) foydalanadi), so'ng haqiqiy
handlerga (asl modulidan import qilingan funksiyaga) topshiradi. Bu orqali
mantiq faqat bitta joyda (asl modulda) qoladi — bu yerda faqat marshrutlash
va state tozalash bor.
"""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import keyboards.keyboards as kb
from services.flow import abandon_pending_flow

router = Router(name="menu_priority")

_MENU_TEXTS = {
    kb.BTN_AD, kb.BTN_ORDER, kb.BTN_MY_ADS, kb.BTN_MY_ORDERS,
    kb.BTN_REFERRAL, kb.BTN_ADMIN, kb.BTN_FIND_ORDER, kb.BTN_MY_APPLICATIONS,
}


@router.message(F.text.in_(_MENU_TEXTS))
@router.message(Command("order"))
@router.message(Command("admin"))
async def menu_button_priority(message: Message, state: FSMContext):
    # Kech import — dumaloq (circular) import'dan qochish uchun: bu
    # modullarning hech biri handlers.user.menu'ni import qilmaydi, lekin
    # xavfsizlik uchun funksiya ichida import qilingan.
    from handlers.user import ads, orders, mine, referral, applications
    from handlers.admin import panel

    text = message.text
    is_command = bool(text) and text.startswith("/")

    current_state = await state.get_state()
    if current_state is not None:
        await abandon_pending_flow(state)

    if is_command:
        cmd = text.split()[0].split("@")[0]
        if cmd == "/order":
            await orders.order_begin(message, state)
        elif cmd == "/admin":
            await panel.admin_cmd(message, state)
        return

    if text == kb.BTN_FIND_ORDER:
        await applications.lookup_begin(message,state)
    elif text == kb.BTN_MY_APPLICATIONS:
        await applications.my_applications(message,message.from_user.id)
    elif text == kb.BTN_AD:
        await ads.ad_begin(message, state)
    elif text == kb.BTN_ORDER:
        await orders.order_begin(message, state)
    elif text == kb.BTN_MY_ADS:
        await mine.my_ads(message)
    elif text == kb.BTN_MY_ORDERS:
        await mine.my_orders(message)
    elif text == kb.BTN_REFERRAL:
        await referral.referral_menu(message)
    elif text == kb.BTN_ADMIN:
        await panel.admin_cmd(message, state)
