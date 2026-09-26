"""E'lon / Zakaz / Kontakt-unlock uchun umumiy to'lov oqimi yordamchisi."""

import logging

import database.repo as repo
import keyboards.keyboards as kb

logger = logging.getLogger(__name__)


async def card_instructions(message, price):
    card = await repo.get_setting("card_number") or "—"
    holder = await repo.get_setting("card_holder") or "—"
    await message.answer(
        f"💳 Karta orqali to'lov\n\n👤 {holder}\n💳 {card}\n"
        f"💰 Summa: {price:,} so'm\n\nTo'lovdan keyin chek rasmini yuboring.",
        reply_markup=kb.ipb([('xp:save','📝 Keyin davom ettirish'),('xp:help:0','🆘 Yordam olish')]))


async def offer_payment(message, price: int, prefix: str, state, user_id=None):
    from states import AdStates, OrderStates, UnlockStates
    states = {'ad':AdStates,'order':OrderStates,'unlock':UnlockStates}[prefix]
    uid = user_id if user_id is not None else message.from_user.id
    data = await state.get_data()
    item_id = data.get('order_id') if prefix=='unlock' else data.get('item_id')
    if prefix in ('ad','order') and await repo.get_setting('paid_enabled','1')=='0':
        from services.upgrade import submit_free
        from services.invite_gate import InviteRequired
        try:
            pid=await submit_free(prefix,item_id,uid)
        except InviteRequired:
            from handlers.invite_gate import prompt
            await prompt(message,uid,prefix,item_id)
            return
        await state.clear()
        from handlers.experience import ack
        await ack(message,pid)
        return
    if price < 0:
        await message.answer("Narx sozlamasida xato. Admin bilan bog'laning.")
        return
    await state.update_data(price=price)
    if prefix == 'order':
        await repo.cas_update_status('orders',item_id,'WAITING_PAYMENT',status='WAITING_RECEIPT')
        await state.set_state(states.awaiting_receipt)
        await card_instructions(message,price)
        return
    balance = await repo.get_balance(uid)
    if balance >= price:
        await state.set_state(states.awaiting_payment)
        await message.answer(
            f"💳 To'lov\n\nBalansingiz: {balance:,} so'm.\nXizmat narxi: {price:,} so'm.",
            reply_markup=kb.payment_choice_kb(prefix,item_id))
    else:
        if prefix != 'unlock':
            await repo.cas_update_status('ads' if prefix=='ad' else 'orders', item_id,
                                         'WAITING_PAYMENT',status='WAITING_RECEIPT')
        await state.set_state(states.awaiting_receipt)
        await card_instructions(message, price)


async def validate_callback(cq, state, kind):
    data = await state.get_data()
    parts = cq.data.split(':')
    item_id = data.get('order_id') if kind=='unlock' else data.get('item_id')
    from states import AdStates, OrderStates, UnlockStates
    group = {'ad':AdStates,'order':OrderStates,'unlock':UnlockStates}[kind]
    active_state = await state.get_state()
    if (active_state not in (group.awaiting_payment.state,group.awaiting_receipt.state)
            or len(parts)!=3 or not parts[2].isdigit() or int(parts[2])!=item_id
            or data.get('kind')!=kind or not isinstance(data.get('price'),int)):
        await cq.answer("Bu eski tugma. Joriy menyudan foydalaning.",show_alert=True)
        return None
    item = await (repo.get_ad(item_id) if kind=='ad' else repo.get_order(item_id))
    if not item or item.get('deleted_at') or (kind!='unlock' and item['user_id']!=cq.from_user.id):
        await cq.answer("Yozuv topilmadi.",show_alert=True)
        return None
    if kind=='unlock' and (item['status']!='PUBLISHED' or item['user_id']==cq.from_user.id):
        await cq.answer("Bu zakaz endi mavjud emas.",show_alert=True)
        return None
    return data


async def notify_receipt(message, payment_id, code, price):
    for admin_id in await repo.list_admin_ids():
        try:
            await message.bot.send_photo(admin_id, message.photo[-1].file_id,
                caption=f"💳 YANGI TO'LOV #{payment_id}\n🆔 {code}\n👤 {message.from_user.id}\n💰 {price:,} so'm",
                reply_markup=kb.receipt_admin_kb(payment_id))
        except Exception:
            logger.warning("Admin xabari yuborilmadi: %s",admin_id)
