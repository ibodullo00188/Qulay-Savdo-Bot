import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import database.repo as repo
import keyboards.keyboards as kb
from services.payment import offer_payment, card_instructions, validate_callback, notify_receipt
from services.transactions import pay
from services.notifications import flush_notifications
from services.channel import publish_ad, validate_content
from services.flow import abandon_pending_flow
from states import AdStates

logger = logging.getLogger(__name__)
router = Router(name="user_ads")


@router.message(F.text == kb.BTN_AD)
async def ad_begin(message: Message, state: FSMContext):
    # Agar foydalanuvchi boshqa (yoki shu) oqimda to'lovni yakunlamay
    # qaytadan "E'lon berish" bossa, eski to'lanmagan yozuv CANCELLED
    # qilinadi — aks holda u abadiy "faol" bo'lib qolar edi.
    await abandon_pending_flow(state)
    await repo.expire_stale_pending()
    user = await repo.get_or_create_user(message.from_user.id)
    active = await repo.count_user_active_ads(user["telegram_id"])
    max_active = await repo.get_int_setting("max_active_ads", 5)
    if active >= max_active:
        await message.answer(
            f"⛔️ Sizda faol e'lonlar soni limitga yetdi ({max_active} ta).\n"
            "Avval eskilaridan birini yakunlang yoki o'chiring.",
            reply_markup=kb.main_menu_rb(await repo.is_admin(message.from_user.id)))
        return
    await state.set_state(AdStates.awaiting_content)
    guide = await repo.get_setting("ad_guide") or "📢 E'lon berish"
    await message.answer(guide, reply_markup=kb.ad_guarantee_kb())


@router.callback_query(F.data == "ad:guarantee")
async def ad_guarantee(cq: CallbackQuery):
    text = await repo.get_setting("ad_guarantee") or "❓ Kafolat matni yo'q."
    await cq.answer()
    await cq.message.answer(text, reply_markup=kb.guarantee_info_kb())


@router.callback_query(F.data == "ok")
async def ok_dismiss(cq: CallbackQuery):
    await cq.answer()
    try:
        await cq.message.delete()
    except Exception:
        pass


@router.callback_query(F.data == "cancel")
async def cancel_flow(cq: CallbackQuery, state: FSMContext):
    # Yarim qolgan e'lon/zakaz bo'lsa CANCELLED qilib belgilaymiz, aks
    # holda "❌ Bekor qilish" bosilgan yozuv abadiy WAITING_PAYMENT/
    # WAITING_RECEIPT holatida "faol" bo'lib qolar edi.
    await abandon_pending_flow(state)
    await cq.answer("Bekor qilindi")
    is_admin = await repo.is_admin(cq.from_user.id)
    try:
        await cq.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cq.message.answer("Jarayondan chiqdingiz. Yuborilgan post matni qoralamalarda saqlanadi.", reply_markup=kb.main_menu_rb(is_admin))


async def _image_required() -> bool:
    return (await repo.get_setting("image_required", "1")) == "1"


@router.message(AdStates.awaiting_content, F.photo)
async def ad_content_photo(message: Message, state: FSMContext):
    if not await validate_content(message):
        return
    caption = message.caption or ""
    user = await repo.get_or_create_user(message.from_user.id)
    ad_id, code = await repo.create_ad(
        user_id=user["telegram_id"], message_type="PHOTO",
        telegram_file_id=message.photo[-1].file_id,
        caption=caption, text=None, status="WAITING_PAYMENT")
    await state.update_data(kind="ad", item_id=ad_id, public_code=code)
    price = await repo.get_int_setting("ad_price", 34990)
    if await repo.get_setting('paid_enabled','1')=='0':
        await offer_payment(message,0,'ad',state)
        return
    await message.answer(
        f"✅ E'loningiz qabul qilindi!\n\n"
        f"E'lon joylash narxi {price:,} so'm ni tashkil etadi.\n"
        f"Iltimos, to'lovni amalga oshiring.\n\n"
        f"🆔 Kod: {code}")
    await offer_payment(message, price, "ad", state)


@router.message(AdStates.awaiting_content, F.text)
async def ad_content_text(message: Message, state: FSMContext):
    if await _image_required():
        await message.answer(
            "⚠️ Rasm majburiy. Iltimos, rasm va matnni bitta xabarda yuboring.",
            reply_markup=kb.cancel_kb())
        return
    if not await validate_content(message):
        return
    user = await repo.get_or_create_user(message.from_user.id)
    ad_id, code = await repo.create_ad(
        user_id=user["telegram_id"], message_type="TEXT",
        telegram_file_id=None, caption=None, text=message.text,
        status="WAITING_PAYMENT")
    await state.update_data(kind="ad", item_id=ad_id, public_code=code)
    price = await repo.get_int_setting("ad_price", 34990)
    if await repo.get_setting('paid_enabled','1')=='0':
        await offer_payment(message,0,'ad',state)
        return
    await message.answer(
        f"✅ E'loningiz qabul qilindi!\n\n"
        f"E'lon joylash narxi {price:,} so'm ni tashkil etadi.\n"
        f"Iltimos, to'lovni amalga oshiring.\n\n"
        f"🆔 Kod: {code}")
    await offer_payment(message, price, "ad", state)


@router.callback_query(F.data.startswith("ad:pay_balance"))
async def ad_pay_balance(cq: CallbackQuery, state: FSMContext):
    data = await validate_callback(cq,state,"ad")
    if not data:
        return
    try:
        await pay(cq.from_user.id,"ad",data['item_id'],data['price'])
    except ValueError as exc:
        await cq.answer(str(exc),show_alert=True)
        return
    await state.clear()
    await cq.answer("✅ To'landi!")
    msg_id = await publish_ad(cq.bot,await repo.get_ad(data['item_id']))
    await cq.message.edit_text("✅ Kanalga joylashtirildi." if msg_id else
        "✅ To'lov qabul qilindi. Kanalga joylashni admin tekshiradi; qayta to'lamang.")
    await flush_notifications(cq.bot)


@router.callback_query(F.data.startswith("ad:pay_card"))
async def ad_pay_card(cq: CallbackQuery, state: FSMContext):
    data = await validate_callback(cq,state,"ad")
    if not data:
        return
    item = await repo.get_ad(data['item_id'])
    if item['status'] not in ('WAITING_PAYMENT','WAITING_RECEIPT'):
        await cq.answer("Bu yozuv allaqachon qayta ishlangan.",show_alert=True)
        return
    await repo.cas_update_status('ads',data['item_id'],'WAITING_PAYMENT',status='WAITING_RECEIPT')
    await state.set_state(AdStates.awaiting_receipt)
    await cq.answer()
    await card_instructions(cq.message,data['price'])


@router.message(AdStates.awaiting_receipt, F.photo)
async def ad_receipt(message: Message, state: FSMContext):
    if message.media_group_id:
        await message.answer("Chekni bitta rasm qilib yuboring, albom emas.")
        return
    data = await state.get_data()
    if data.get('kind')!='ad' or not data.get('item_id'):
        await state.clear()
        await message.answer("Jarayon topilmadi. Menyudan qaytadan boshlang.")
        return
    try:
        payment_id = await pay(message.from_user.id,'ad',data['item_id'],data['price'],
                               message.photo[-1].file_id,message.photo[-1].file_unique_id)
    except (ValueError, KeyError) as exc:
        await message.answer(str(exc) if isinstance(exc,ValueError) else "Jarayon eskirgan. /cancel ni bosing.")
        return
    await state.clear()
    await message.answer("✅ Chek qabul qilindi. Admin tekshirmoqda.",
        reply_markup=kb.main_menu_rb(await repo.is_admin(message.from_user.id)))
    await notify_receipt(message,payment_id,data['public_code'],data['price'])


@router.message(AdStates.awaiting_receipt)
async def ad_receipt_wrong(message: Message):
    await message.answer("📎 Iltimos, to'lov chekini rasm ko'rinishida yuboring.",
                          reply_markup=kb.cancel_kb())
