import logging
from aiogram import F, Router
from aiogram.types import Message
import database.repo as repo
import keyboards.keyboards as kb
import config
from database.db import get_db
from services.transactions import one
from services.locks import get_lock
router=Router(name='user_referral')
logger=logging.getLogger(__name__)

@router.message(F.text==kb.BTN_REFERRAL)
async def referral_menu(message: Message):
    user=await repo.get_or_create_user(message.from_user.id)
    db=await get_db()
    async with get_lock(f'referral-link:{user["telegram_id"]}'):
        stored=await one(db,'SELECT link FROM referral_links WHERE owner_id=?',(user['telegram_id'],))
        if not stored:
            try:
                invite=await message.bot.create_chat_invite_link(config.CHANNEL_ID,name=f'ref-{user["telegram_id"]}')
            except Exception:
                logger.warning('Kanal havolasi yaratilmadi')
                await message.answer('Kanal taklif havolasini yaratib bo‘lmadi. Admin botning kanaldagi taklif qilish huquqini tekshirishi kerak.'); return
            await db.execute('INSERT INTO referral_links VALUES (?,?)',(invite.invite_link,user['telegram_id']))
            link=invite.invite_link
        else: link=stored['link']
    count=await one(db,'SELECT COUNT(*) AS n FROM channel_members WHERE referrer_id=? AND present=1',(user['telegram_id'],))
    bonus=await repo.get_int_setting('referral_bonus',5000)
    enabled=await repo.get_setting('paid_enabled','1')=='1' and await repo.get_setting('referral_enabled','1')=='1'
    await message.answer(f"🤝 Do‘stlarni KANALGA taklif qilish\n\n🔗 {link}\n\nKanalda turgan do‘stlar: {count['n']}\n💰 Balans: {user['balance']:,} so‘m\n🎁 Har yangi a’zo uchun: {bonus:,} so‘m\n\nDo‘st shu havola orqali kanalga kirganda darhol bonus beriladi. Chiqsa balans yetarli bo‘lgandagina o‘sha bonus to‘liq ayriladi va sababi darhol xabar qilinadi. Balans yetmasa hech narsa ayrilmaydi, jarima yoki keyingi bonusdan ushlash bo‘lmaydi. Qayta kirish qo‘shimcha bonus yig‘maydi, avval qaytarilgan bonusni tiklaydi."+('' if enabled else '\n\n⏸ Bonus tizimi vaqtincha o‘chirilgan.'))
