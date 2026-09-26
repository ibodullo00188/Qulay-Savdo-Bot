"""Durable publication status; ambiguous Telegram failures require review."""
import logging
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
import config
import database.repo as repo
import keyboards.keyboards as kb
from services.transactions import claim_publication

logger = logging.getLogger(__name__)


async def _post_text(item):
    body = (item.get('caption') or item.get('text') or '').strip()
    sign = await repo.get_setting('sign_text') or repo.SIGN_DEFAULT
    return f"{body}\n\n{sign}\n\n🆔 {item['public_code']}"


async def validate_content(message):
    if message.media_group_id:
        await message.answer("Albom emas, bitta rasm va uning izohini yuboring.")
        return False
    body = message.caption if message.photo else message.text
    if not body or not body.strip():
        await message.answer("Iltimos, mahsulot yoki zakaz haqida matn ham yozing.")
        return False
    sign = await repo.get_setting('sign_text') or repo.SIGN_DEFAULT
    limit = 1024 if message.photo else 4096
    # Telegram counts UTF-16 code units; leave room for code and separators.
    size = len((body + sign).encode('utf-16-le')) // 2 + 24
    if size > limit:
        await message.answer(f"Matn juda uzun. Imzo va kod bilan birga {limit} belgidan oshmasligi kerak. Matnni qisqartiring.")
        return False
    return True


async def _publish(bot, kind, item):
    if not item:
        return None
    table = 'ads' if kind=='ad' else 'orders'
    locked = await claim_publication(kind,item['id'])
    if not locked:
        return None
    status = 'PUBLICATION_FAILED'
    try:
        if not config.CHANNEL_ID:
            raise ValueError('CHANNEL_ID sozlanmagan')
        if kind=='order':
            from services.order_channel import render_order_post
            if item.get('channel_body') is None:
                sign=await repo.get_setting('sign_text') or repo.SIGN_DEFAULT
                item['channel_body']=f"{(item.get('caption') or item.get('text') or '').strip()}\n\n{sign}"
                await repo.update_order(item['id'],channel_body=item['channel_body'])
            text=await render_order_post(item)
        else:
            text = await _post_text(item)
        markup = kb.ad_channel_kb(item['public_code']) if kind=='ad' else kb.order_channel_kb(item['public_code'])
        from services.order_channel import code_entities
        entities=code_entities(text,item['public_code']) if kind=='order' else None
        if item['message_type']=='PHOTO' and item['telegram_file_id']:
            msg = await bot.send_photo(config.CHANNEL_ID,item['telegram_file_id'],caption=text,caption_entities=entities,reply_markup=markup)
        else:
            msg = await bot.send_message(config.CHANNEL_ID,text,entities=entities,reply_markup=markup)
    except (ValueError,TelegramBadRequest,TelegramForbiddenError,TelegramRetryAfter) as exc:
        logger.warning('Kanalga joylash rad etildi: %s (%s)',item['public_code'],type(exc).__name__)
    except Exception as exc:
        status = 'PUBLICATION_REVIEW'  # timeout may mean Telegram already sent the post
        logger.warning("Kanalga joylash natijasi noma'lum: %s (%s)",item['public_code'],type(exc).__name__)
    else:
        await repo.cas_update_status(table,item['id'],'PUBLISHING',status='PUBLISHED',
                                     channel_message_id=msg.message_id,published_at=repo._now())
        from services.experience import track
        await track(item["user_id"],"PUBLISHED",f"{kind}:{item['id']}",f"published:{kind}:{item['id']}")
        return msg.message_id
    await repo.cas_update_status(table,item['id'],'PUBLISHING',status=status)
    for admin in await repo.list_admin_ids():
        try:
            await bot.send_message(admin,f"⚠️ {item['public_code']}: kanalga joylashni tekshiring. Qayta to'lov kerak emas.",
                reply_markup=kb.ipb([(f"admin:republish:{kind}:{item['id']}","🔄 Tekshirish / qayta joylash")]))
        except Exception:
            logger.warning('Admin xabari yuborilmadi: %s',admin)
    return None


async def publish_ad(bot, ad):
    return await _publish(bot,'ad',ad)


async def publish_order(bot, order):
    return await _publish(bot,'order',order)


async def delete_channel_post(bot, message_id):
    if not (config.CHANNEL_ID and message_id):
        return False
    try:
        await bot.delete_message(config.CHANNEL_ID,message_id)
        return True
    except Exception:
        logger.warning("Kanal postini o'chirib bo'lmadi: %s",message_id)
        return False
