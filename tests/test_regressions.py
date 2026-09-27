"""Offline regression tests: real aiogram routing/SQLite, fake Telegram transport."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

# Never use the uploaded credentials/database or contact Telegram in tests.
os.environ['BOT_TOKEN'] = '123456:TEST_TOKEN_FOR_OFFLINE_TESTS'
os.environ['ADMIN_IDS'] = '900,901'
os.environ['BOT_USERNAME'] = 'offline_test_bot'
os.environ['CHANNEL_ID'] = '-100123456'
os.environ['RUN_MODE'] = 'polling'
os.environ['DATABASE_PATH'] = str(Path(tempfile.gettempdir())/'unused-bot-test.db')

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Message, Update, CallbackQuery, User, Chat, PhotoSize, MessageEntity
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.fsm.storage.base import StorageKey
import config
import database.db as database
import database.repo as repo
import main
from services.transactions import pay, moderate, finish_item
from services.storage import SQLiteStorage
from services.channel import publish_ad
from handlers.experience import Edit
from states import AdStates, OrderStates, UnlockStates, SettingsStates, ComplaintStates
import keyboards.keyboards as kb


class OfflineSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.counter = 10000
        self.fail_channel = False

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b''

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        if name == 'GetMe':
            return User(id=bot.id,is_bot=True,first_name='Test',username='offline_test_bot')
        if self.fail_channel and getattr(method,'chat_id',None)==config.CHANNEL_ID:
            raise TimeoutError('simulated unknown send outcome')
        if name in ('SendMessage','SendPhoto','EditMessageText','EditMessageCaption','EditMessageReplyMarkup'):
            self.counter += 1
            return Message(message_id=self.counter,date=datetime.now(timezone.utc),
                chat=Chat(id=method.chat_id,type='private'),
                from_user=User(id=bot.id,is_bot=True,first_name='Test'),
                text=getattr(method,'text',None),caption=getattr(method,'caption',None),
                reply_markup=(getattr(method,'reply_markup',None) if hasattr(getattr(method,'reply_markup',None),'inline_keyboard') else None))
        return True


DP = main.build_dispatcher()


class RegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        database.DATABASE_PATH = config.DATABASE_PATH = str(Path(self.tmp.name)/'bot.db')
        import services.transactions as tx
        tx.DATABASE_PATH = config.DATABASE_PATH
        database._connection_lock = asyncio.Lock()
        DP.fsm.events_isolation = SimpleEventIsolation()
        await database.init_db()
        self.session = OfflineSession()
        self.bot = Bot(config.BOT_TOKEN,session=self.session)
        self.update_id = 0
        await repo.get_or_create_user(1,'one','One')
        await repo.get_or_create_user(2,'two','Two')

    async def asyncTearDown(self):
        await DP.fsm.events_isolation.close()
        await database.close_db()
        await self.bot.session.close()
        self.tmp.cleanup()

    def state(self,uid=1):
        return DP.fsm.get_context(bot=self.bot,chat_id=uid,user_id=uid)

    async def message(self,text=None,photo=False,uid=1,unique='receipt-1',caption=None,album=None):
        self.update_id+=1
        data=dict(message_id=self.update_id,date=datetime.now(timezone.utc),
            chat=Chat(id=uid,type='private'),from_user=User(id=uid,is_bot=False,first_name='User',username=f'user{uid}'),
            text=text,caption=caption,media_group_id=album)
        if text and text.startswith('/'):
            data['entities']=[MessageEntity(type='bot_command',offset=0,length=len(text.split()[0]))]
        if photo:
            data['photo']=[PhotoSize(file_id='file-'+unique,file_unique_id=unique,width=1600,height=900)]
        await DP.feed_update(self.bot,Update(update_id=self.update_id,message=Message(**data)))

    async def callback(self,data,uid=1,chat_id=None,caption=None):
        self.update_id+=1
        msg=Message(message_id=self.update_id,date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id or uid,type='channel' if (chat_id or uid)<0 else 'private'),
            from_user=User(id=self.bot.id,is_bot=True,first_name='Bot'),text=None if caption else 'buttons',caption=caption)
        cq=CallbackQuery(id=str(self.update_id),from_user=User(id=uid,is_bot=False,first_name='User',username=f'user{uid}'),
                         chat_instance='offline',data=data,message=msg)
        await DP.feed_update(self.bot,Update(update_id=self.update_id,callback_query=cq))

    async def new_ad(self,uid=1,status='WAITING_PAYMENT'):
        return (await repo.create_ad(uid,'TEXT',None,None,'Test product',status=status))[0]

    async def new_order(self,uid=2,status='PUBLISHED'):
        return (await repo.create_order(uid,'TEXT',None,None,'Test order',status=status))[0]

    async def confirm_preview(self,uid=1):
        data=await self.state(uid).get_data()
        self.assertEqual(await self.state(uid).get_state(),Edit.preview.state)
        await self.callback(f"xp:submit:{data['editor_kind']}:{data['editor_id']}:{data['editor_version']}",uid=uid)

    async def test_admin_reply_sections_and_permissions(self):
        from aiogram.types import ReplyKeyboardMarkup
        await self.message(kb.BTN_ADMIN, uid=900)
        markup = self.session.calls[-1].reply_markup
        self.assertIsInstance(markup, ReplyKeyboardMarkup)
        self.assertTrue(markup.is_persistent)
        self.assertIn(kb.BTN_HOME, [b.text for row in markup.keyboard for b in row])
        for action, label in kb.ADMIN_SECTIONS:
            with self.subTest(action=action):
                self.session.calls.clear()
                await self.message(label, uid=900)
                self.assertTrue(self.session.calls)
                self.assertFalse(any(type(c).__name__.startswith('EditMessage') for c in self.session.calls))
                self.assertFalse(any(type(c).__name__ == 'AnswerCallbackQuery' for c in self.session.calls))
        await self.message(kb.BTN_ADMIN, uid=900)
        self.assertIsNone(await self.state(900).get_state())
        await self.message('♻️ Backupni tiklash', uid=900)
        await self.message(kb.BTN_HOME, uid=900)
        self.assertIsNone(await self.state(900).get_state())
        for _, label in kb.ADMIN_SECTIONS:
            self.session.calls.clear()
            await self.message(label, uid=1)
            self.assertEqual(len(self.session.calls), 1)
            self.assertIn('huquq', self.session.calls[0].text)

    async def test_low_balance_ad_photo_becomes_receipt(self):
        await self.message(kb.BTN_AD)
        await self.message(photo=True,caption='Product',unique='product')
        await self.confirm_preview()
        self.assertEqual(await self.state().get_state(),AdStates.awaiting_receipt.state)
        self.assertEqual(len(await repo.list_ads_by_user(1)),1)
        await self.message(photo=True)
        self.assertEqual(len(await repo.list_ads_by_user(1)),1)
        self.assertEqual(await repo.count_pending_payments(),1)
        self.assertIsNone(await self.state().get_state())

    async def test_low_balance_order_receipt(self):
        await self.message(kb.BTN_ORDER)
        await self.message('Build a website')
        await self.confirm_preview()
        self.assertEqual(await self.state().get_state(),OrderStates.awaiting_receipt.state)
        await self.message(photo=True)
        self.assertEqual(len(await repo.list_orders_by_user(1)),1)
        self.assertEqual(await repo.count_pending_payments(),1)



    async def test_old_cross_flow_callback_cannot_charge(self):
        ad=await self.new_ad(2)
        order=await self.new_order(1,'WAITING_PAYMENT')
        self.assertEqual(ad,order)
        await repo.add_balance(1,100000)
        await self.state().update_data(kind='order',item_id=order,price=100)
        await self.callback(f'ad:pay_balance:{ad}')
        self.assertEqual(await repo.get_balance(1),100000)
        self.assertEqual((await repo.get_ad(ad))['status'],'WAITING_PAYMENT')

    async def test_stale_same_flow_button_rejected(self):
        old=await self.new_ad();new=await self.new_ad()
        await repo.add_balance(1,1000)
        await self.state().update_data(kind='ad',item_id=new,price=100)
        await self.callback(f'ad:pay_balance:{old}')
        self.assertEqual(await repo.get_balance(1),1000)

    async def test_atomic_balance_across_two_items(self):
        ad=await self.new_ad();oid=await self.new_ad(1)
        await repo.add_balance(1,100)
        result=await asyncio.gather(pay(1,'ad',ad,80),pay(1,'ad',oid,80),return_exceptions=True)
        self.assertEqual(sum(isinstance(x,int) for x in result),1)
        self.assertEqual(await repo.get_balance(1),20)
        self.assertEqual(await repo.count_payments(),1)

    async def test_duplicate_balance_payment_only_once(self):
        ad=await self.new_ad();await repo.add_balance(1,100)
        result=await asyncio.gather(pay(1,'ad',ad,40),pay(1,'ad',ad,40),return_exceptions=True)
        self.assertEqual(sum(isinstance(x,int) for x in result),1)
        self.assertEqual(await repo.get_balance(1),60)

    async def test_payment_failure_rolls_back_debit(self):
        ad=await self.new_ad();await repo.add_balance(1,100)
        with patch('services.transactions.grant_bonus',side_effect=RuntimeError('fail after writes')):
            with self.assertRaises(RuntimeError): await pay(1,'ad',ad,40)
        self.assertEqual(await repo.get_balance(1),100)
        self.assertEqual(await repo.count_payments(),0)
        self.assertEqual((await repo.get_ad(ad))['status'],'WAITING_PAYMENT')

    async def test_two_admins_only_one_approval(self):
        ad=await self.new_ad();pid=await pay(1,'ad',ad,40,'receipt','unique')
        result=await asyncio.gather(moderate(pid,900,True),moderate(pid,901,True),return_exceptions=True)
        self.assertEqual(sum(isinstance(x,dict) for x in result),1)
        self.assertEqual((await repo.get_ad(ad))['status'],'READY_TO_PUBLISH')

    async def test_late_rejection_cannot_override_approval(self):
        ad=await self.new_ad();pid=await pay(1,'ad',ad,40,'receipt','unique')
        await moderate(pid,900,True)
        with self.assertRaises(ValueError): await moderate(pid,901,False,'late')
        self.assertEqual((await repo.get_payment(pid))['status'],'APPROVED')


    async def test_duplicate_receipt_rejected(self):
        a=await self.new_ad();b=await self.new_ad()
        await pay(1,'ad',a,50,'file1','same-image')
        with self.assertRaises(ValueError): await pay(1,'ad',b,50,'file2','same-image')
        self.assertEqual(await repo.count_payments(),1)


    async def test_foreign_ad_cannot_be_paid(self):
        ad=await self.new_ad(2);await repo.add_balance(1,100)
        with self.assertRaises(ValueError): await pay(1,'ad',ad,50)
        self.assertEqual(await repo.get_balance(1),100)

    async def test_fsm_survives_connection_restart(self):
        await self.state().set_state(AdStates.awaiting_receipt)
        await self.state().update_data(kind='ad',item_id=3,price=100)
        await database.close_db()
        store=SQLiteStorage();key=StorageKey(bot_id=self.bot.id,chat_id=1,user_id=1)
        self.assertEqual(await store.get_state(key),AdStates.awaiting_receipt.state)
        self.assertEqual((await store.get_data(key))['item_id'],3)

    async def test_start_preserves_old_flow_as_resumable_draft(self):
        ad=await self.new_ad()
        await self.state().set_state(AdStates.awaiting_receipt)
        await self.state().update_data(kind='ad',item_id=ad)
        await self.message('/start')
        self.assertIsNone(await self.state().get_state())
        self.assertEqual((await repo.get_ad(ad))['status'],'WAITING_PAYMENT')

    async def test_back_list_uses_human(self):
        ad=await self.new_ad()
        await self.callback('ad:back_list')
        markup=self.session.calls[-1].reply_markup
        self.assertTrue(any(b.callback_data==f'ad:view:{ad}' for row in markup.inline_keyboard for b in row))
        self.assertIsNone(await repo.get_user(self.bot.id))

    async def test_admin_pending_next(self):
        for n in range(2): await pay(1,'ad',await self.new_ad(),50,f'file{n}',f'unique{n}')
        await self.callback('admin:pending:next:1',uid=900,caption='receipt')
        self.assertIn('#2',self.session.calls[-1].caption)
        await self.callback('admin:sections',uid=900,caption='receipt')
        self.assertEqual(type(self.session.calls[-1]).__name__,'SendMessage')

    async def test_admin_complaints_next(self):
        await repo.create_complaint(1,'AD-1000','First')
        await repo.create_complaint(1,'AD-1001','Second')
        await self.callback('admin:complaints:next:1',uid=900)
        self.assertIn('First',self.session.calls[-1].text)

    async def test_negative_price_rejected(self):
        await self.callback('admin:setkey:ad_price',uid=900)
        await self.message('-100',uid=900)
        self.assertEqual(await repo.get_int_setting('ad_price'),34990)
        self.assertEqual(await self.state(900).get_state(),SettingsStates.awaiting_value.state)
        ad=await self.new_ad()
        with self.assertRaises(ValueError): await pay(1,'ad',ad,-10)

    async def test_paid_item_not_deleted_while_pending(self):
        ad=await self.new_ad();await pay(1,'ad',ad,50,'file','unique')
        with self.assertRaises(ValueError): await finish_item('ad',ad,1,'DELETED')
        self.assertEqual((await repo.get_ad(ad))['status'],'WAITING_ADMIN')

    async def test_only_one_channel_publication(self):
        ad=await self.new_ad();await repo.add_balance(1,100);pid=await pay(1,'ad',ad,50);await moderate(pid,900,True)
        item=await repo.get_ad(ad)
        result=await asyncio.gather(publish_ad(self.bot,item),publish_ad(self.bot,item))
        self.assertEqual(sum(x is not None for x in result),1)
        sent=[x for x in self.session.calls if getattr(x,'chat_id',None)==config.CHANNEL_ID]
        self.assertEqual(len(sent),1)

    async def test_ambiguous_publication_needs_admin_review(self):
        ad=await self.new_ad();await repo.add_balance(1,100);pid=await pay(1,'ad',ad,50);await moderate(pid,900,True)
        self.session.fail_channel=True
        await publish_ad(self.bot,await repo.get_ad(ad))
        self.assertEqual((await repo.get_ad(ad))['status'],'PUBLICATION_REVIEW')
        self.assertEqual(await repo.get_balance(1),50)
        self.session.fail_channel=False
        await self.callback(f'admin:retry_confirm:ad:{ad}',uid=900)
        self.assertEqual((await repo.get_ad(ad))['status'],'PUBLISHED')
        self.assertEqual(await repo.count_payments(),1)

    async def test_payments_no_longer_grant_referral_bonus(self):
        await repo.update_user(1,referred_by=2)
        await repo.add_balance(1,100)
        a=await self.new_ad();b=await self.new_ad()
        await asyncio.gather(pay(1,'ad',a,30),pay(1,'ad',b,30))
        self.assertEqual(await repo.get_balance(2),0)
        self.assertEqual((await repo.get_user(1))['referral_bonus_paid'],0)

    async def test_expiration_parses_iso_datetime(self):
        ad=await self.new_ad()
        await repo.update_ad(ad,created_at=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat())
        await repo.expire_stale_pending()
        self.assertEqual((await repo.get_ad(ad))['status'],'DRAFT')

    async def test_channel_complaint_enters_private_state(self):
        ad=await self.new_ad(status='PUBLISHED');item=await repo.get_ad(ad)
        await self.callback('complaint:start:'+item['public_code'],chat_id=config.CHANNEL_ID)
        self.assertEqual(await self.state().get_state(),ComplaintStates.awaiting_reason.state)
        await self.message('Scam')
        self.assertEqual(await repo.count_complaints(),1)

    async def test_deeplink_new_visitor(self):
        oid=await self.new_order();item=await repo.get_order(oid)
        await self.message('/start order_'+item['public_code'],uid=3)
        self.assertIsNotNone(await repo.get_user(3))
        markup=self.session.calls[-1].reply_markup
        self.assertEqual(markup.inline_keyboard[0][0].callback_data,'application:start:'+item['public_code'])

    async def test_username_is_refreshed(self):
        await self.message('/start')
        self.assertEqual((await repo.get_user(1))['username'],'user1')

    async def test_webhook_url_has_no_token(self):
        self.assertNotIn(config.BOT_TOKEN,config.WEBHOOK_PATH)

    async def test_admin_approval_handler_publishes_once(self):
        ad=await self.new_ad();pid=await pay(1,'ad',ad,50,'file','unique')
        await asyncio.gather(self.callback(f'pay:approve:{pid}',uid=900,caption='receipt'),
                             self.callback(f'pay:approve:{pid}',uid=901,caption='receipt'))
        self.assertEqual((await repo.get_ad(ad))['status'],'PUBLISHED')
        self.assertEqual(len([x for x in self.session.calls if getattr(x,'chat_id',None)==config.CHANNEL_ID]),1)

    async def test_card_button_shows_requisites(self):
        await repo.add_balance(1,100000)
        await self.message(kb.BTN_AD)
        await self.message(photo=True,caption='Product',unique='product')
        await self.confirm_preview()
        self.assertEqual(await self.state().get_state(),AdStates.awaiting_payment.state)
        ad=(await self.state().get_data())['item_id']
        await self.callback(f'ad:pay_card:{ad}')
        self.assertEqual(await self.state().get_state(),AdStates.awaiting_receipt.state)
        self.assertIn(await repo.get_setting('card_number'),self.session.calls[-1].text)

    async def test_album_does_not_create_false_receipt(self):
        await self.message(kb.BTN_AD)
        await self.message(photo=True,caption='Album',unique='a',album='group1')
        await self.message(photo=True,caption='Album',unique='b',album='group1')
        self.assertEqual(await repo.list_ads_by_user(1),[])
        self.assertEqual(await repo.count_payments(),0)

    async def test_long_content_rejected_before_payment(self):
        await self.message(kb.BTN_AD)
        await self.message(photo=True,caption='x'*1024,unique='product')
        self.assertEqual(await repo.list_ads_by_user(1),[])
        self.assertEqual(await self.state().get_state(),Edit.content.state)

    async def test_missing_channel_preserves_payment_and_allows_retry(self):
        ad=await self.new_ad();await repo.add_balance(1,100);pid=await pay(1,'ad',ad,50);await moderate(pid,900,True)
        with patch.object(config,'CHANNEL_ID',0):
            await publish_ad(self.bot,await repo.get_ad(ad))
        self.assertEqual((await repo.get_ad(ad))['status'],'PUBLICATION_FAILED')
        await self.callback(f'admin:retry_confirm:ad:{ad}',uid=900)
        self.assertEqual((await repo.get_ad(ad))['status'],'PUBLISHED')
        self.assertEqual(await repo.get_balance(1),50)

    async def test_startup_keeps_pending_updates_and_recovers_interrupted_post(self):
        ad=await self.new_ad(status='PUBLISHING')
        await main.on_startup(self.bot)
        self.assertEqual((await repo.get_ad(ad))['status'],'PUBLICATION_REVIEW')
        deletion=[x for x in self.session.calls if type(x).__name__=='DeleteWebhook']
        self.assertFalse(deletion[-1].drop_pending_updates)

    async def test_webhook_readiness_auth_and_failure(self):
        from aiohttp import web,ClientSession
        import socket
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        entered=asyncio.Event();release=asyncio.Event()
        async def slow_start(bot):
            entered.set()
            await release.wait()
        loop=asyncio.get_running_loop()
        with patch.object(config,'PORT',port), patch.object(config,'WEBHOOK_URL','https://example.invalid/webhook'), \
             patch.object(config,'WEBHOOK_SECRET','test-secret'), patch.object(main,'build_bot',return_value=self.bot), \
             patch.object(main,'build_dispatcher',return_value=DP), patch.object(main,'on_startup',side_effect=slow_start), \
             patch.object(loop,'add_signal_handler'):
            task=asyncio.create_task(main._run_webhook_async())
            try:
                await asyncio.wait_for(entered.wait(),3)
                async with ClientSession() as client:
                    url=f'http://127.0.0.1:{port}'
                    async with client.get(url+'/health') as response: self.assertEqual(response.status,503)
                    async with client.post(url+'/webhook',json={}) as response: self.assertEqual(response.status,503)
                    release.set()
                    await asyncio.sleep(0)
                    async with client.get(url+'/health') as response: self.assertEqual(response.status,200)
                    async with client.post(url+'/webhook',json={}) as response: self.assertEqual(response.status,401)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task

    async def test_webhook_startup_error_exits(self):
        loop=asyncio.get_running_loop()
        with patch.object(config,'PORT',0), patch.object(config,'WEBHOOK_URL','https://example.invalid/webhook'), \
             patch.object(config,'WEBHOOK_SECRET','test-secret'), patch.object(main,'build_bot',return_value=self.bot), \
             patch.object(main,'build_dispatcher',return_value=DP), patch.object(main,'on_startup',side_effect=RuntimeError('startup failed')), \
             patch.object(loop,'add_signal_handler'):
            with self.assertRaisesRegex(RuntimeError,'startup failed'):
                await main._run_webhook_async()


    async def test_unpaid_legacy_item_cannot_be_republished(self):
        ad=await self.new_ad(status='PUBLICATION_REVIEW')
        await self.callback(f'admin:retry_confirm:ad:{ad}',uid=900)
        self.assertEqual((await repo.get_ad(ad))['status'],'PUBLICATION_REVIEW')
        self.assertEqual(len([x for x in self.session.calls if getattr(x,'chat_id',None)==config.CHANNEL_ID]),0)

    async def test_referral_notification_is_durable_and_sent_once(self):
        from services.notifications import flush_notifications
        from services.upgrade import membership
        db=await database.get_db()
        await db.execute('INSERT INTO referral_links VALUES (?,?)',('test-link',2))
        await membership(1,True,'test-link',1)
        await database.close_db()
        await flush_notifications(self.bot)
        await flush_notifications(self.bot)
        messages=[x for x in self.session.calls if getattr(x,'chat_id',None)==2]
        self.assertEqual(len(messages),1)
        self.assertIn('5,000',messages[0].text)

    async def test_order_requires_receipt_even_with_wallet_balance(self):
        await repo.add_balance(1,100000)
        await self.message(kb.BTN_ORDER)
        await self.message('Build a shop')
        await self.confirm_preview()
        self.assertEqual(await self.state().get_state(),OrderStates.awaiting_receipt.state)
        oid=(await self.state().get_data())['item_id']
        await self.callback(f'order:pay_balance:{oid}')
        self.assertEqual(await repo.get_balance(1),100000)
        self.assertEqual(await repo.count_payments(),0)
        await self.message(photo=True)
        pid=(await repo.list_payments_by_status('WAITING_ADMIN'))[0]['id']
        self.assertEqual((await repo.get_order(oid))['status'],'WAITING_ADMIN')
        await self.callback(f'pay:approve:{pid}',uid=900,caption='receipt')
        self.assertEqual((await repo.get_order(oid))['status'],'PUBLISHED')
        channel=[x for x in self.session.calls if getattr(x,'chat_id',None)==config.CHANNEL_ID][-1]
        self.assertIn('OCHIQ',channel.text)

    async def test_free_application_entire_flow_and_private_chat_link(self):
        from services.applications import find_application
        from states import ApplicationStates
        oid=await self.new_order();code=(await repo.get_order(oid))['public_code']
        await self.message(code.lower())
        self.assertEqual(self.session.calls[-1].reply_markup.inline_keyboard[0][0].callback_data,'application:start:'+code)
        await self.callback('application:start:'+code)
        self.assertEqual(await self.state().get_state(),ApplicationStates.price.state)
        await self.message('500000');await self.message('5 kun');await self.message('Portfolio: example.com <test>')
        self.assertEqual(await self.state().get_state(),ApplicationStates.preview.state)
        await self.callback(f'application:send:{oid}')
        app=await find_application(oid,1)
        self.assertIsNotNone(app)
        self.assertEqual(await repo.count_payments(),0)
        self.assertEqual(await repo.get_balance(1),0)
        owner_cards=[x for x in self.session.calls if getattr(x,'chat_id',None)==2 and getattr(x,'parse_mode',None)=='HTML']
        card=owner_cards[-1]
        self.assertIn('@user1',card.text)
        self.assertIn('&lt;test&gt;',card.text)
        self.assertTrue(any(b.url=='https://t.me/user1' for row in card.reply_markup.inline_keyboard for b in row))
        self.assertTrue(any((b.callback_data or '').startswith('app:accept:') for row in card.reply_markup.inline_keyboard for b in row))

    async def test_many_candidates_reject_one_keep_order_open(self):
        from services.applications import create_application,decide_application,get_application
        await repo.get_or_create_user(3,'three','Three')
        oid=await self.new_order()
        a=await create_application(oid,1,'100 so‘m','1 kun','A')
        b=await create_application(oid,3,'200 so‘m','2 kun','B')
        await decide_application(a,2,False)
        self.assertEqual((await get_application(a))['status'],'REJECTED')
        self.assertEqual((await get_application(b))['status'],'PENDING')
        self.assertEqual((await repo.get_order(oid))['status'],'PUBLISHED')
        self.assertEqual(await repo.count_payments(),0)

    async def test_duplicate_free_application_is_atomic(self):
        from services.applications import create_application,list_order_applications
        oid=await self.new_order()
        results=await asyncio.gather(create_application(oid,1,'100','1 day','A'),create_application(oid,1,'100','1 day','A'),return_exceptions=True)
        self.assertEqual(sum(isinstance(x,int) for x in results),1)
        self.assertEqual((await list_order_applications(oid,2))[2],1)

    async def test_two_candidates_cannot_both_be_selected(self):
        from services.applications import create_application,decide_application,get_application
        await repo.get_or_create_user(3,'three','Three')
        oid=await self.new_order()
        a=await create_application(oid,1,'100','1 day','A')
        b=await create_application(oid,3,'100','1 day','B')
        results=await asyncio.gather(decide_application(a,2,True),decide_application(b,2,True),return_exceptions=True)
        self.assertEqual(sum(isinstance(x,int) for x in results),1)
        self.assertEqual({(await get_application(a))['status'],(await get_application(b))['status']},{'ACCEPTED','CLOSED'})
        self.assertEqual((await repo.get_order(oid))['status'],'ASSIGNED')

    async def test_order_owner_is_only_decision_maker(self):
        from services.applications import create_application,decide_application
        oid=await self.new_order();aid=await create_application(oid,1,'100','1 day','A')
        with self.assertRaises(ValueError):await decide_application(aid,900,True)
        await self.callback(f'app:accept:{aid}',uid=1)
        self.assertEqual((await repo.get_order(oid))['status'],'PUBLISHED')

    async def test_selection_closes_new_and_stale_requests(self):
        from services.applications import create_application,decide_application
        await repo.get_or_create_user(3,'three','Three')
        oid=await self.new_order();a=await create_application(oid,1,'100','1 day','A')
        await decide_application(a,2,True)
        with self.assertRaises(ValueError):await create_application(oid,3,'100','1 day','B')
        with self.assertRaises(ValueError):await decide_application(a,2,False)
        code=(await repo.get_order(oid))['public_code']
        await self.message(code,uid=3)
        self.assertTrue(any('so‘rovlar yopilgan' in (getattr(x,'text','') or '') for x in self.session.calls))
        self.assertFalse(any(getattr(b,'callback_data',None)=='application:start:'+code for x in self.session.calls for row in getattr(getattr(x,'reply_markup',None),'inline_keyboard',[]) for b in row))

    async def test_pending_preview_cannot_submit_after_selection(self):
        from services.applications import create_application,decide_application,find_application
        from states import ApplicationStates
        await repo.get_or_create_user(3,'three','Three')
        oid=await self.new_order();code=(await repo.get_order(oid))['public_code']
        await self.callback('application:start:'+code,uid=3)
        await self.message('100',uid=3);await self.message('1 kun',uid=3);await self.message('B',uid=3)
        a=await create_application(oid,1,'100','1 kun','A')
        await decide_application(a,2,True)
        await self.callback(f'application:send:{oid}',uid=3)
        self.assertIsNone(await find_application(oid,3))

    async def test_selected_channel_post_is_edited_not_deleted(self):
        from services.applications import create_application
        oid=await self.new_order();await repo.update_order(oid,channel_message_id=777)
        aid=await create_application(oid,1,'100','1 kun','A')
        await self.callback(f'app:accept:{aid}',uid=2)
        calls=[x for x in self.session.calls if getattr(x,'chat_id',None)==config.CHANNEL_ID]
        self.assertEqual(type(calls[-1]).__name__,'EditMessageText')
        self.assertIn('ZAKAZ QABUL QILINGAN',calls[-1].text)
        self.assertLess(calls[-1].text.index('ZAKAZ QABUL QILINGAN'),calls[-1].text.index('ZK-'))
        self.assertNotIn('So‘rov yuborish',str(calls[-1].reply_markup))
        self.assertEqual((await repo.get_order(oid))['channel_sync_pending'],0)

    async def test_failed_channel_edit_keeps_order_locked_then_retries(self):
        from services.applications import create_application
        from services.order_channel import flush_order_posts
        oid=await self.new_order();await repo.update_order(oid,channel_message_id=777)
        aid=await create_application(oid,1,'100','1 kun','A')
        self.session.fail_channel=True
        await self.callback(f'app:accept:{aid}',uid=2)
        order=await repo.get_order(oid)
        self.assertEqual(order['status'],'ASSIGNED')
        self.assertEqual(order['channel_sync_pending'],1)
        self.session.fail_channel=False
        await database.close_db()
        await flush_order_posts(self.bot)
        self.assertEqual((await repo.get_order(oid))['channel_sync_pending'],0)

    async def test_complete_preserves_order_and_updates_channel(self):
        from services.applications import create_application,decide_application
        oid=await self.new_order();await repo.update_order(oid,channel_message_id=777)
        aid=await create_application(oid,1,'100','1 kun','A');await decide_application(aid,2,True)
        await self.callback(f'order:complete:{oid}',uid=2)
        self.assertEqual((await repo.get_order(oid))['status'],'ASSIGNED')
        await self.callback(f'finish:yes:{oid}:0',uid=2)
        await self.callback(f'finish:yes:{oid}:0',uid=1)
        self.assertEqual((await repo.get_order(oid))['status'],'COMPLETION_REVIEW')
        await self.callback(f'finish:admin:{oid}:0:yes',uid=900)
        self.assertEqual((await repo.get_order(oid))['status'],'COMPLETED')
        edits=[x for x in self.session.calls if getattr(x,'chat_id',None)==config.CHANNEL_ID]
        self.assertIn('ISH BAJARILDI',edits[-1].text)
        self.assertFalse(any(type(x).__name__=='DeleteMessage' for x in edits))

    async def test_order_close_closes_all_pending_requests(self):
        from services.applications import create_application,close_order,get_application
        oid=await self.new_order();aid=await create_application(oid,1,'100','1 kun','A')
        await close_order(oid,2,'DELETED')
        self.assertEqual((await get_application(aid))['status'],'CLOSED')
        with self.assertRaises(ValueError):await create_application(oid,1,'100','1 kun','A')

    async def test_photo_order_caption_updated_within_limit(self):
        from services.applications import create_application,decide_application
        from services.order_channel import sync_order_post
        oid=(await repo.create_order(2,'PHOTO','image','X'*1000,None,status='PUBLISHED'))[0]
        await repo.update_order(oid,channel_message_id=777)
        aid=await create_application(oid,1,'100','1 kun','A');await decide_application(aid,2,True)
        await sync_order_post(self.bot,oid)
        edit=[x for x in self.session.calls if type(x).__name__=='EditMessageCaption'][-1]
        self.assertLessEqual(len(edit.caption.encode('utf-16-le'))//2,1024)
        self.assertIn('ZAKAZ QABUL QILINGAN',edit.caption)

    async def test_no_username_cannot_apply_or_create_order(self):
        from services.applications import create_application
        from handlers.user.applications import require_username
        from unittest.mock import AsyncMock
        oid=await self.new_order()
        await repo.update_user(1,username=None)
        with self.assertRaises(ValueError):await create_application(oid,1,'100','1 kun','A')
        user=User(id=1,is_bot=False,first_name='No username')
        msg=AsyncMock()
        self.assertFalse(await require_username(msg,user))
        self.assertIn('username',msg.answer.call_args.args[0].lower())

    async def test_request_notification_survives_restart(self):
        from services.applications import create_application
        from services.notifications import flush_notifications
        oid=await self.new_order();aid=await create_application(oid,1,'100','1 kun','A')
        await database.close_db()
        await flush_notifications(self.bot)
        messages=[x for x in self.session.calls if getattr(x,'chat_id',None)==2]
        self.assertEqual(len(messages),1)
        self.assertTrue(any(b.callback_data==f'app:accept:{aid}' for row in messages[0].reply_markup.inline_keyboard for b in row))
        await flush_notifications(self.bot)
        self.assertEqual(len([x for x in self.session.calls if getattr(x,'chat_id',None)==2]),1)

    async def test_foreign_request_cannot_be_viewed(self):
        from services.applications import create_application
        oid=await self.new_order();aid=await create_application(oid,1,'100','1 kun','A')
        await self.callback(f'app:view:{aid}',uid=3)
        self.assertEqual(type(self.session.calls[-1]).__name__,'AnswerCallbackQuery')
        self.assertTrue(self.session.calls[-1].show_alert)

    async def test_old_contact_payment_buttons_never_charge(self):
        oid=await self.new_order();await repo.add_balance(1,100000)
        await self.state().update_data(kind='unlock',order_id=oid,price=25000)
        await self.callback(f'unlock:pay_balance:{oid}')
        self.assertEqual(await repo.get_balance(1),100000)
        with self.assertRaises(ValueError):await pay(1,'unlock',oid,25000)
        self.assertEqual(await repo.count_payments(),0)

    async def test_request_lists_are_paginated(self):
        from services.applications import create_application
        oid=await self.new_order()
        for uid in range(10,18):
            await repo.get_or_create_user(uid,f'user{uid}','User')
            await create_application(oid,uid,'100','1 kun','A')
        await self.callback(f'apps:order:{oid}:0',uid=2)
        self.assertEqual(len(self.session.calls[-1].reply_markup.inline_keyboard),8)
        await self.callback(f'apps:order:{oid}:6',uid=2)
        self.assertEqual(len(self.session.calls[-1].reply_markup.inline_keyboard),4)

    async def test_rejected_candidate_cannot_spam_same_order(self):
        from services.applications import create_application,decide_application
        oid=await self.new_order();aid=await create_application(oid,1,'100','1 kun','A')
        await decide_application(aid,2,False)
        with self.assertRaises(ValueError):await create_application(oid,1,'100','1 kun','A')

    async def test_owner_cannot_apply_to_own_order(self):
        from services.applications import create_application
        oid=await self.new_order()
        with self.assertRaises(ValueError):await create_application(oid,2,'100','1 kun','A')

    async def test_application_decision_rolls_back_if_notification_write_fails(self):
        from services.applications import create_application,decide_application,get_application
        oid=await self.new_order();aid=await create_application(oid,1,'100','1 kun','A')
        with patch('services.applications.enqueue',side_effect=RuntimeError('disk failure')):
            with self.assertRaises(RuntimeError):await decide_application(aid,2,True)
        self.assertEqual((await repo.get_order(oid))['status'],'PUBLISHED')
        self.assertEqual((await get_application(aid))['status'],'PENDING')

    async def test_schema_upgrade_keeps_existing_rows(self):
        oid=await self.new_order();await repo.add_balance(1,555)
        await database.init_db();await database.init_db()
        self.assertEqual(await repo.get_balance(1),555)
        self.assertEqual((await repo.get_order(oid))['status'],'PUBLISHED')

    async def test_channel_reward_insufficient_balance_no_debit_or_future_claim(self):
        from services.upgrade import membership,toggle_money
        db=await database.get_db()
        await db.execute('INSERT INTO referral_links VALUES (?,?)',('link',1))
        await asyncio.gather(membership(40,True,'link',1),membership(40,True,'link',1))
        self.assertEqual(await repo.get_balance(1),5000)
        await repo.add_balance(1,-4500)
        before=(await (await db.execute('SELECT COUNT(*) FROM notifications')).fetchone())[0]
        await membership(40,False,event_id=2)
        self.assertEqual(await repo.get_balance(1),500)
        self.assertEqual((await repo.get_user(1))['bonus_penalty'],0)
        self.assertEqual((await (await db.execute('SELECT COUNT(*) FROM notifications')).fetchone())[0],before)
        await membership(41,True,'link',3)
        self.assertEqual(await repo.get_balance(1),5500)
        await toggle_money(900); await toggle_money(900)
        self.assertEqual(await repo.get_balance(1),5500) # no postponed debit
        await membership(40,True,'link',4)
        self.assertEqual(await repo.get_balance(1),5500) # no second reward
        await membership(40,False,event_id=5)
        self.assertEqual(await repo.get_balance(1),500) # new departure, enough now
        note=await (await db.execute('SELECT text FROM notifications ORDER BY id DESC LIMIT 1')).fetchone()
        self.assertIn('kanaldan chiqdi',note[0]); self.assertIn('5,000',note[0])
        await membership(40,False,event_id=5)
        self.assertEqual(await repo.get_balance(1),500)
        await membership(40,True,'link',6)
        self.assertEqual(await repo.get_balance(1),5500)

    async def test_zero_balance_and_legacy_penalty_cleared(self):
        from services.upgrade import membership
        db=await database.get_db()
        await db.execute('INSERT INTO referral_links VALUES (?,?)',('link',1))
        await membership(40,True,'link',1)
        await repo.add_balance(1,-5000)
        await membership(40,False,event_id=2)
        self.assertEqual(await repo.get_balance(1),0)
        await repo.update_user(1,bonus_penalty=4000)
        await database.init_db()
        self.assertEqual((await repo.get_user(1))['bonus_penalty'],0)
        await membership(41,True,'link',3)
        self.assertEqual(await repo.get_balance(1),5000)

    async def test_channel_rewards_pause_and_resume(self):
        from services.upgrade import membership,toggle_money
        db=await database.get_db()
        await db.execute('INSERT INTO referral_links VALUES (?,?)',('link',1))
        await membership(40,True,'link',1)
        await toggle_money(900)
        await membership(40,False,event_id=2)
        await membership(41,True,'link',3)
        self.assertEqual(await repo.get_balance(1),5000)
        await toggle_money(900)
        self.assertEqual(await repo.get_balance(1),0)
        await membership(42,True,'link',4)
        self.assertEqual(await repo.get_balance(1),5000)

    async def test_member_update_real_routing(self):
        from aiogram.types import ChatMemberUpdated,ChatMemberLeft,ChatMemberMember,ChatInviteLink
        db=await database.get_db()
        await db.execute('INSERT INTO referral_links VALUES (?,?)',('https://t.me/+test',1))
        user=User(id=88,is_bot=False,first_name='Friend')
        event=ChatMemberUpdated(chat=Chat(id=config.CHANNEL_ID,type='channel'),from_user=user,
            date=datetime.now(timezone.utc),old_chat_member=ChatMemberLeft(user=user),
            new_chat_member=ChatMemberMember(user=user),
            invite_link=ChatInviteLink(invite_link='https://t.me/+test',creator=User(id=self.bot.id,is_bot=True,first_name='Bot'),creates_join_request=False,is_primary=False,is_revoked=False))
        await DP.feed_update(self.bot,Update(update_id=100,chat_member=event))
        self.assertEqual(await repo.get_balance(1),5000)

    async def test_reopen_twice_same_code_post_and_stale_finish(self):
        from services.applications import create_application,decide_application,get_application
        from services.upgrade import reopen,confirm_finish
        oid=await self.new_order(); await repo.update_order(oid,channel_message_id=777)
        original=await repo.get_order(oid)
        aid=await create_application(oid,1,'100','1 kun','A')
        for round_ in range(2):
            await decide_application(aid,2,True)
            await confirm_finish(oid,1,round_)
            await reopen(oid,2,round_)
            self.assertEqual((await get_application(aid))['status'],'CLOSED')
            await self.callback(f'xp:reconsider:{aid}',uid=2)
            current=await repo.get_order(oid)
            self.assertEqual(current['public_code'],original['public_code'])
            self.assertEqual(current['channel_message_id'],777)
            self.assertEqual(current['status'],'PUBLISHED')
            self.assertEqual(current['worker_done'],0)
            self.assertEqual((await get_application(aid))['username'],'one')
        await decide_application(aid,2,True)
        with self.assertRaises(ValueError): await reopen(oid,2,2)
        with self.assertRaises(ValueError): await confirm_finish(oid,1,0)
        with self.assertRaises(ValueError): await confirm_finish(oid,88,2)
        self.assertEqual((await repo.get_order(oid))['reopen_count'],2)

    async def test_completion_requires_two_parties_admin_and_no_bypass(self):
        from services.applications import create_application,decide_application
        from services.upgrade import confirm_finish,moderate_finish
        oid=await self.new_order(); aid=await create_application(oid,1,'100','1 kun','A')
        await decide_application(aid,2,True)
        await confirm_finish(oid,1,0)
        with self.assertRaises(ValueError): await moderate_finish(oid,900,0,True)
        with self.assertRaises(ValueError): await finish_item('order',oid,2,'COMPLETED')
        await confirm_finish(oid,2,0)
        with self.assertRaises(ValueError): await moderate_finish(oid,1,0,True)
        await moderate_finish(oid,900,0,False)
        self.assertEqual((await repo.get_order(oid))['owner_done'],0)
        await asyncio.gather(confirm_finish(oid,1,0),confirm_finish(oid,2,0))
        results=await asyncio.gather(moderate_finish(oid,900,0,True),moderate_finish(oid,901,0,True),return_exceptions=True)
        self.assertEqual(sum(isinstance(x,ValueError) for x in results),1)
        self.assertEqual((await repo.get_order(oid))['status'],'COMPLETED')

    async def test_free_ad_order_submission_still_moderated(self):
        from services.upgrade import toggle_money
        await toggle_money(900)
        await self.message(kb.BTN_ORDER)
        await self.message('Free order')
        await self.confirm_preview()
        oid=(await repo.list_orders_by_user(1))[0]['id']
        self.assertEqual((await repo.get_order(oid))['status'],'WAITING_ADMIN')
        await self.message(kb.BTN_AD)
        await self.message(photo=True,caption='Free ad',unique='product')
        await self.confirm_preview()
        aid=(await repo.list_ads_by_user(1))[0]['id']
        self.assertEqual((await repo.get_ad(aid))['status'],'WAITING_ADMIN')
        payments=await repo.list_payments_by_status('WAITING_ADMIN')
        self.assertEqual(len(payments),2)
        self.assertTrue(all(p['amount']==0 and p['receipt_file_id'] is None for p in payments))
        for p in payments: await self.callback(f"pay:approve:{p['id']}",uid=900)
        self.assertEqual((await repo.get_order(oid))['status'],'PUBLISHED')
        self.assertEqual((await repo.get_ad(aid))['status'],'PUBLISHED')

    async def test_codes_grow_beyond_four_digits_concurrent_and_restart(self):
        db=await database.get_db()
        await db.execute("INSERT INTO orders(public_code,user_id,message_type) VALUES ('ZK-999999999999',1,'TEXT')")
        codes=await asyncio.gather(*(repo.new_public_code('ZK') for _ in range(35)))
        self.assertEqual(len(set(codes)),35)
        self.assertIn('ZK-1000000000000',codes)
        await database.close_db(); await database.init_db()
        self.assertNotIn(await repo.new_public_code('ZK'),codes)
        self.assertTrue(all(repo.PUBLIC_CODE_RE.fullmatch(c) for c in codes))

    async def test_admin_import_edit_approve_both_types_and_permissions(self):
        for kind in ('ad','order'):
            await self.callback(f'upgrade:new:{kind}',uid=900)
            await self.message('Original content',uid=900)
            await self.callback('upgrade:save',uid=900)
            p=(await repo.list_payments_by_status('WAITING_ADMIN'))[0]
            await self.callback(f"upgrade:edit:{p['id']}",uid=1)
            self.assertIsNone(await self.state(1).get_state())
            await self.callback(f"upgrade:edit:{p['id']}",uid=900)
            await self.message('Edited content',uid=900)
            await self.callback(f"pay:approve:{p['id']}",uid=900)
            item=await (repo.get_ad(p['ad_id']) if kind=='ad' else repo.get_order(p['order_id']))
            self.assertEqual(item['text'],'Edited content')
            self.assertEqual(item['status'],'PUBLISHED')

    async def test_hourly_reminders_durable_throttle_and_all_admins(self):
        from services.upgrade import hourly_reminders
        aid=await self.new_ad(); await pay(1,'ad',aid,50,'receipt','unique')
        await hourly_reminders(self.bot)
        first=len(self.session.calls)
        self.assertTrue(any(getattr(x,'chat_id',None)==900 for x in self.session.calls))
        self.assertTrue(any(getattr(x,'chat_id',None)==901 for x in self.session.calls))
        await database.close_db(); await hourly_reminders(self.bot)
        self.assertEqual(len(self.session.calls),first)
        db=await database.get_db(); await db.execute('UPDATE reminder_delivery SET sent_at=sent_at-3601')
        await hourly_reminders(self.bot)
        self.assertGreater(len(self.session.calls),first)

    async def test_copy_code_and_snapshot_retained_after_rejection(self):
        from services.application_views import application_text,application_keyboard
        from services.applications import create_application,decide_application,get_application
        oid=await self.new_order(); aid=await create_application(oid,1,'100','1 kun','A')
        await decide_application(aid,2,False); await repo.update_user(1,username=None)
        app=await get_application(aid); order=await repo.get_order(oid); user=await repo.get_user(1)
        self.assertIn('@one',application_text(app,order,user))
        self.assertIn('https://t.me/one',str(application_keyboard(app,order,user)))
        self.assertEqual(kb.order_channel_kb(order['public_code']).inline_keyboard[0][0].copy_text.text,order['public_code'])


    async def test_import_cancel_and_home_are_not_swallowed(self):
        from handlers.upgrades import ImportStates
        await self.callback('upgrade:new:ad',uid=900)
        self.assertEqual(await self.state(900).get_state(),ImportStates.content.state)
        await self.message('/cancel',uid=900)
        self.assertIsNone(await self.state(900).get_state())
        await self.callback('upgrade:new:order',uid=900)
        await self.message(kb.BTN_HOME,uid=900)
        self.assertIsNone(await self.state(900).get_state())

    async def test_existing_channel_posts_scheduled_once_on_upgrade(self):
        oid=await self.new_order(); await repo.update_order(oid,channel_message_id=777)
        db=await database.get_db()
        await db.execute("DELETE FROM settings WHERE key='channel_format_v3'")
        await database.init_db()
        self.assertEqual((await repo.get_order(oid))['channel_sync_pending'],1)
        await repo.update_order(oid,channel_sync_pending=0)
        await database.init_db()
        self.assertEqual((await repo.get_order(oid))['channel_sync_pending'],0)


if __name__=='__main__':
    unittest.main()
