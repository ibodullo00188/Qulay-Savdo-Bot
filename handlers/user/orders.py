import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import database.repo as repo
import keyboards.keyboards as kb
from services.payment import offer_payment, card_instructions, validate_callback, notify_receipt
from services.transactions import pay
from services.notifications import flush_notifications
from services.channel import publish_order, validate_content
from services.flow import abandon_pending_flow
from states import OrderStates
from handlers.user.applications import require_username

logger = logging.getLogger(__name__)
router = Router(name="user_orders")


# ============================================================
#  ZAKAZ BERISH (foydalanuvchi tomonidan)
# ============================================================
@router.message(Command("order"))
@router.message(F.text == kb.BTN_ORDER)
async def order_begin(message: Message, state: FSMContext):
    if not await require_username(message,message.from_user):
        return
    # Yarim qolgan to'lanmagan zakaz bo'lsa CANCELLED qilinadi — aks holda
    # abadiy "faol" bo'lib qolib kvotani band qilib turadi.
    await abandon_pending_flow(state)
    await repo.expire_stale_pending()
    user = await repo.get_or_create_user(message.from_user.id)
    active = await repo.count_user_active_orders(user["telegram_id"])
    max_active = await repo.get_int_setting("max_active_orders", 5)
    if active >= max_active:
        await message.answer(
            f"⛔️ Faol zakazlar soni limitga yetdi ({max_active} ta). "
            "Avval eskisini yakunlang.",
            reply_markup=kb.main_menu_rb(await repo.is_admin(message.from_user.id)))
        return
    await state.set_state(OrderStates.awaiting_content)
    guide = await repo.get_setting("order_guide") or "🔵 Dasturga zakaz berish"
    if await repo.get_setting('paid_enabled','1')=='0':
        guide='🔵 BEPUL ZAKAZ\n\nTopshiriq matnini yoki bitta rasm va izohni yuboring. Karta va chek talab qilinmaydi. Admin tekshirgach kanalga joylanadi; bajaruvchilar bepul taklif yuboradi.'
    await message.answer(guide, reply_markup=kb.order_guarantee_kb())


@router.callback_query(F.data == "order:guarantee")
async def order_guarantee(cq: CallbackQuery):
    text = await repo.get_setting("order_guarantee") or "❓ Kafolat matni yo'q."
    await cq.answer()
    await cq.message.answer(text, reply_markup=kb.guarantee_info_kb())


async def _create_order_and_offer(message: Message, state: FSMContext,
                                   message_type, file_id, caption, text):
    if not await require_username(message,message.from_user):
        return
    if not await validate_content(message):
        return
    user = await repo.get_or_create_user(message.from_user.id)
    order_id, code = await repo.create_order(
        user_id=user["telegram_id"], message_type=message_type,
        telegram_file_id=file_id, caption=caption, text=text,
        status="WAITING_PAYMENT")
    await state.update_data(kind="order", item_id=order_id, public_code=code)
    price = await repo.get_int_setting("order_price", 34990)
    if await repo.get_setting('paid_enabled','1')=='0':
        await offer_payment(message,0,'order',state)
        return
    await message.answer(
        f"🔵 Zakazingiz tayyor!\n\n"
        f"🆔 Zakaz raqami: {code}\n\n"
        f"Zakazni kanalimizga joylashtirish narxi {price:,} so'm.\n"
        f"Iltimos, to'lovni amalga oshiring.")
    await offer_payment(message, price, "order", state)


@router.message(OrderStates.awaiting_content, F.photo)
async def order_content_photo(message: Message, state: FSMContext):
    await _create_order_and_offer(
        message, state, "PHOTO", message.photo[-1].file_id,
        message.caption or "", None)


@router.message(OrderStates.awaiting_content, F.text)
async def order_content_text(message: Message, state: FSMContext):
    await _create_order_and_offer(
        message, state, "TEXT", None, None, message.text)


@router.callback_query(F.data.startswith("order:pay_balance"))
async def order_pay_balance(cq: CallbackQuery, state: FSMContext):
    await cq.answer("Zakaz uchun karta orqali to‘lang va chek yuboring. Admin tasdiqlaydi.",show_alert=True)


@router.callback_query(F.data.startswith("order:pay_card"))
async def order_pay_card(cq: CallbackQuery, state: FSMContext):
    data = await validate_callback(cq,state,"order")
    if not data:
        return
    item = await repo.get_order(data['item_id'])
    if item['status'] not in ('WAITING_PAYMENT','WAITING_RECEIPT'):
        await cq.answer("Bu yozuv allaqachon qayta ishlangan.",show_alert=True)
        return
    await repo.cas_update_status('orders',data['item_id'],'WAITING_PAYMENT',status='WAITING_RECEIPT')
    await state.set_state(OrderStates.awaiting_receipt)
    await cq.answer()
    await card_instructions(cq.message,data['price'])


@router.message(OrderStates.awaiting_receipt, F.photo)
async def order_receipt(message: Message, state: FSMContext):
    if message.media_group_id:
        await message.answer("Chekni bitta rasm qilib yuboring, albom emas.")
        return
    data = await state.get_data()
    if data.get('kind')!='order' or not data.get('item_id'):
        await state.clear()
        await message.answer("Jarayon topilmadi. Menyudan qaytadan boshlang.")
        return
    try:
        payment_id = await pay(message.from_user.id,'order',data['item_id'],data['price'],
                               message.photo[-1].file_id,message.photo[-1].file_unique_id)
    except (ValueError, KeyError) as exc:
        await message.answer(str(exc) if isinstance(exc,ValueError) else "Jarayon eskirgan. /cancel ni bosing.")
        return
    await state.clear()
    await message.answer("✅ Chek qabul qilindi. Admin tekshirmoqda.",
        reply_markup=kb.main_menu_rb(await repo.is_admin(message.from_user.id)))
    await notify_receipt(message,payment_id,data['public_code'],data['price'])


@router.message(OrderStates.awaiting_receipt)
async def order_receipt_wrong(message: Message):
    await message.answer("📎 Iltimos, to'lov chekini rasm ko'rinishida yuboring.",
                          reply_markup=kb.cancel_kb())
