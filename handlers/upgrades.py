"""Additional private admin flows and channel membership events."""
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated
from aiogram.fsm.context import FSMContext
import config
import database.repo as repo
import keyboards.keyboards as kb
from database.db import get_db
from services.transactions import transaction, one
from services.upgrade import (membership,toggle_money,submit_free,reopen,confirm_finish,
    moderate_finish,pending_entries,send_pending)
from services.order_channel import sync_order_post
from services.notifications import flush_notifications

router=Router(name='upgrades')
router.callback_query.filter(F.message.chat.type=='private')

def is_import_content(message):
    menu={kb.BTN_CANCEL,kb.BTN_HOME,kb.BTN_ADMIN,kb.BTN_AD,kb.BTN_ORDER,
          kb.BTN_MY_ADS,kb.BTN_MY_ORDERS,kb.BTN_REFERRAL,kb.BTN_RULES,
          kb.BTN_FIND_ORDER,kb.BTN_MY_APPLICATIONS}
    return not (message.text and (message.text.startswith('/') or message.text in menu))

router.message.filter(is_import_content)

class ImportStates(StatesGroup):
    content=State()
    preview=State()
    edit=State()

async def guard(cq):
    if cq.message.chat.type!='private' or not await repo.is_admin(cq.from_user.id):
        await cq.answer('Ruxsat yo‘q.',show_alert=True); return False
    return True

def present(member):
    return member.status in ('member','administrator','creator') or (member.status=='restricted' and member.is_member)

@router.chat_member()
async def channel_member(event: ChatMemberUpdated,event_update):
    if event.chat.id!=config.CHANNEL_ID: return
    before,after=present(event.old_chat_member),present(event.new_chat_member)
    if before==after: return
    user=event.new_chat_member.user
    await membership(user.id,after,event.invite_link.invite_link if event.invite_link else None,int(event.date.timestamp())*3000000000+event_update.update_id,user.is_bot)
    await flush_notifications(event.bot)

@router.callback_query(F.data=='upgrade:money')
async def money_menu(cq: CallbackQuery):
    if not await guard(cq): return
    await cq.answer()
    enabled=await repo.get_setting('paid_enabled','1')=='1'
    await cq.message.answer('Pullik tizim: '+('YOQILGAN' if enabled else 'O‘CHIRILGAN')+'\nZakaz va e’lonlar birga boshqariladi. O‘chirilsa bonus hisoblash ham to‘xtaydi. Balans va ma’lumotlar saqlanadi.',reply_markup=kb.ipb([(f'upgrade:toggle:{int(enabled)}','O‘chirish' if enabled else 'Yoqish')]))

@router.callback_query(F.data.startswith('upgrade:toggle:'))
async def money_toggle(cq: CallbackQuery):
    if not await guard(cq): return
    from services.locks import get_lock
    async with get_lock('money-toggle'):
        current=await repo.get_setting('paid_enabled','1')
        if current!=cq.data.split(':')[-1]:
            await cq.answer('Sozlama allaqachon o‘zgargan.',show_alert=True); return
        value=await toggle_money(cq.from_user.id)
    await cq.answer()
    await cq.message.edit_text('✅ Pullik tizim '+('yoqildi. Bonus tizimi faol.' if value else 'o‘chirildi. Bonus tizimi to‘xtatildi.'))
    await flush_notifications(cq.bot)

@router.callback_query(F.data=='upgrade:queue')
async def queue(cq: CallbackQuery):
    if not await guard(cq): return
    await cq.answer()
    entries=await pending_entries()
    await cq.message.answer(f'Kutilayotganlar: {len(entries)} ta.')
    for entry in entries: await send_pending(cq.bot,cq.from_user.id,entry)

@router.callback_query(F.data.startswith('reopen:ask:'))
async def reopen_ask(cq: CallbackQuery):
    item=await repo.get_order(int(cq.data.split(':')[-1]))
    if not item or item['user_id']!=cq.from_user.id:
        await cq.answer('Faqat zakaz egasi.',show_alert=True); return
    await cq.answer()
    if item['reopen_count']>=2 or item['status'] not in ('ASSIGNED','COMPLETION_REVIEW'):
        await cq.message.answer('Qayta ochish mumkin emas: holat mos emas yoki 2 martalik limit tugagan.'); return
    await cq.message.answer(f"{item['public_code']} qayta ochilsinmi?\nKod va kanaldagi post saqlanadi. Eski kelishuv bekor bo‘ladi, oldingi takliflar saqlanadi; keraklilarini qayta tanlovga qaytarasiz. Qolgan imkoniyat: {2-item['reopen_count']}.",reply_markup=kb.ipb([(f"reopen:yes:{item['id']}:{item['reopen_count']}",'🔄 Ha, qayta ochish')]))

@router.callback_query(F.data.startswith('reopen:yes:'))
async def reopen_yes(cq: CallbackQuery):
    _,_,oid,round_=cq.data.split(':')
    try: await reopen(int(oid),cq.from_user.id,int(round_))
    except ValueError as exc:
        await cq.answer(str(exc),show_alert=True); return
    await cq.answer('Qayta ochildi')
    synced=await sync_order_post(cq.bot,int(oid))
    await cq.message.edit_text('✅ Zakaz o‘sha kod bilan qayta ochildi.'+('' if synced else '\nKanal yozuvini yangilash qayta uriniladi.'))
    await flush_notifications(cq.bot)

@router.callback_query(F.data.startswith('finish:confirm:'))
@router.callback_query(F.data.startswith('order:complete:'))
async def finish_ask(cq: CallbackQuery):
    item=await repo.get_order(int(cq.data.split(':')[-1]))
    if not item or cq.from_user.id not in (item['user_id'],item['assigned_to']):
        await cq.answer('Sizga tegishli emas.',show_alert=True); return
    await cq.answer()
    await cq.message.answer(f"{item['public_code']}\nBuyurtmachi: {'✅' if item['owner_done'] else '⏳'}\nBajaruvchi: {'✅' if item['worker_done'] else '⏳'}\nIsh to‘liq tugaganini tasdiqlaysizmi? Ikkala tomon tasdiqlagach admin tekshiradi.",reply_markup=kb.ipb([(f"finish:yes:{item['id']}:{item['reopen_count']}",'🏁 Ha, ish tugadi')]))

@router.callback_query(F.data.startswith('finish:yes:'))
async def finish_yes(cq: CallbackQuery):
    _,_,oid,round_=cq.data.split(':')
    try: both=await confirm_finish(int(oid),cq.from_user.id,int(round_))
    except ValueError as exc:
        await cq.answer(str(exc),show_alert=True); return
    await cq.answer()
    await cq.message.edit_text('✅ Tasdiqingiz saqlandi. '+('Admin tasdig‘i kutilmoqda.' if both else 'Ikkinchi tomon tasdig‘i kutilmoqda.'))
    await sync_order_post(cq.bot,int(oid)); await flush_notifications(cq.bot)

@router.callback_query(F.data.startswith('finish:admin:'))
async def finish_admin(cq: CallbackQuery):
    if not await guard(cq): return
    _,_,oid,round_,answer=cq.data.split(':')
    try: await moderate_finish(int(oid),cq.from_user.id,int(round_),answer=='yes')
    except ValueError as exc:
        await cq.answer(str(exc),show_alert=True); return
    await cq.answer()
    await cq.message.edit_text('🏁 Zakaz to‘liq tugatildi.' if answer=='yes' else 'Yakunlash rad etildi. Kelishuv davom etadi.')
    await sync_order_post(cq.bot,int(oid)); await flush_notifications(cq.bot)

@router.callback_query(F.data=='upgrade:import')
async def import_menu(cq: CallbackQuery,state: FSMContext):
    if not await guard(cq): return
    await cq.answer(); await state.clear()
    await cq.message.answer('Qaysi turdagi post? Keyin matn yoki bitta rasmli xabarni yuboring / forward qiling.',reply_markup=kb.ipb([('upgrade:new:order','🔵 Zakaz'),('upgrade:new:ad','📢 E’lon')]))

@router.callback_query(F.data.regexp(r'^upgrade:new:(ad|order)$'))
async def import_begin(cq: CallbackQuery,state: FSMContext):
    if not await guard(cq): return
    await state.clear(); await state.update_data(import_kind=cq.data.split(':')[-1]); await state.set_state(ImportStates.content)
    await cq.answer(); await cq.message.answer('Matn yoki bitta rasm va izoh yuboring / forward qiling. Post egasi siz bo‘lasiz; zakaz so‘rovlari sizga keladi.',reply_markup=kb.cancel_kb())

async def capture(message):
    from services.channel import validate_content
    if not (message.text or message.photo) or not await validate_content(message): return None
    return dict(message_type='PHOTO' if message.photo else 'TEXT',telegram_file_id=message.photo[-1].file_id if message.photo else None,caption=message.caption if message.photo else None,text=None if message.photo else message.text)

@router.message(ImportStates.content)
async def import_content(message: Message,state: FSMContext):
    if not await repo.is_admin(message.from_user.id): return
    data=await state.get_data()
    if data.get('import_kind')=='order':
        from handlers.user.applications import require_username
        if not await require_username(message,message.from_user): return
    body=await capture(message)
    if not body: return
    await state.update_data(import_body=body)
    await state.set_state(ImportStates.preview)
    markup=kb.ipb([('upgrade:save','✅ Tasdiqlash uchun saqlash'),('upgrade:rewrite','✏️ Tahrirlash'),('cancel','Bekor qilish')])
    if body['message_type']=='PHOTO': await message.answer_photo(body['telegram_file_id'],caption=body['caption'],reply_markup=markup)
    else: await message.answer(body['text'],reply_markup=markup)

@router.callback_query(F.data=='upgrade:rewrite')
async def rewrite(cq: CallbackQuery,state: FSMContext):
    if not await guard(cq): return
    if await state.get_state()!=ImportStates.preview.state:
        await cq.answer('Eski tugma.',show_alert=True); return
    await state.set_state(ImportStates.content); await cq.answer(); await cq.message.answer('Yangi matn yoki rasm va izohni yuboring.')

@router.callback_query(F.data=='upgrade:save')
async def save_import(cq: CallbackQuery,state: FSMContext):
    if not await guard(cq): return
    if await state.get_state()!=ImportStates.preview.state:
        await cq.answer('Allaqachon saqlangan yoki eski tugma.',show_alert=True); return
    data=await state.get_data(); kind=data['import_kind']
    from handlers.user.applications import remember
    await remember(cq.from_user)
    if kind=='order' and not cq.from_user.username:
        await cq.answer('Telegram username o‘rnating.',show_alert=True); return
    # Persist draft id before submitting so a retry never creates another draft.
    iid=data.get('import_id')
    if not iid:
        creator=repo.create_ad if kind=='ad' else repo.create_order
        iid,_=await creator(cq.from_user.id,**data['import_body'])
        await state.update_data(import_id=iid)
    await submit_free(kind,iid,cq.from_user.id,admin=True)
    await state.clear(); await cq.answer()
    await cq.message.answer('✅ Saqlandi. Tahrirlang yoki kanalga chiqarishni tasdiqlang.')
    for entry in await pending_entries():
        if entry[0]=='payment' and entry[2]['id']==iid and bool(entry[1]['ad_id'])==(kind=='ad'):
            await send_pending(cq.bot,cq.from_user.id,entry)
    await cq.message.answer('Yana post qo‘shish:',reply_markup=kb.ipb([('upgrade:new:order','🔵 Zakaz'),('upgrade:new:ad','📢 E’lon')]))

@router.callback_query(F.data.startswith('upgrade:edit:'))
async def edit_begin(cq: CallbackQuery,state: FSMContext):
    if not await guard(cq): return
    payment=await repo.get_payment(int(cq.data.split(':')[-1]))
    if not payment or payment['status']!='WAITING_ADMIN':
        await cq.answer('Bu post endi tahrirlanmaydi.',show_alert=True); return
    await state.clear(); await state.update_data(edit_payment=payment['id']); await state.set_state(ImportStates.edit)
    await cq.answer(); await cq.message.answer('Tasdiqlashdan oldingi yangi matn yoki rasm va izohni yuboring.',reply_markup=kb.cancel_kb())

@router.message(ImportStates.edit)
async def edit_content(message: Message,state: FSMContext):
    if not await repo.is_admin(message.from_user.id): return
    body=await capture(message)
    if not body: return
    data=await state.get_data()
    async with transaction() as db:
        payment=await one(db,'SELECT * FROM payments WHERE id=?',(data.get('edit_payment'),))
        if not payment or payment['status']!='WAITING_ADMIN':
            await message.answer('Post allaqachon tasdiqlangan. Tahrir qo‘llanmadi.'); await state.clear(); return
        table='ads' if payment['ad_id'] else 'orders'; iid=payment['ad_id'] or payment['order_id']
        item=await one(db,f'SELECT * FROM {table} WHERE id=?',(iid,))
        if not item or item['status']!='WAITING_ADMIN' or item['channel_message_id']:
            await message.answer('Bu postni tahrirlab bo‘lmaydi.'); await state.clear(); return
        await db.execute(f"UPDATE {table} SET message_type=?,telegram_file_id=?,caption=?,text=? WHERE id=?",(*body.values(),iid))
        await db.execute("INSERT INTO admin_actions(admin_id,action_type,target_type,target_id) VALUES (?,'EDIT_BEFORE_PUBLICATION',?,?)",(message.from_user.id,table,str(iid)))
    await state.clear(); await message.answer('✅ Tahrir saqlandi.',reply_markup=kb.receipt_admin_kb(payment['id']))

@router.callback_query(F.data=='username:retry')
async def username_retry(cq: CallbackQuery,state: FSMContext):
    from handlers.user.applications import remember
    await remember(cq.from_user)
    if not cq.from_user.username:
        await cq.answer('Username hali yo‘q. Telegram → Sozlamalar → Username.',show_alert=True); return
    await cq.answer('✅ Username aniqlandi')
    await cq.message.answer('✅ @'+cq.from_user.username+' saqlandi. Oldingi tugmani bosing yoki joriy bosqichdagi xabarni qayta yuboring. Kiritilgan ma’lumotlar saqlangan.')
