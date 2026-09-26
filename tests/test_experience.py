"""V4 behavioral tests: previews, deadlines, moderation, discovery and trust."""
import asyncio
import unittest
from datetime import datetime,timedelta
import test_regressions as base
from handlers.experience import Edit
from services import experience as xp
from services.transactions import pay,moderate,one
from services.upgrade import submit_free,reopen
from services.applications import create_application,decide_application
from services.notifications import flush_notifications
import database.db as database
import database.repo as repo
import keyboards.keyboards as kb

class ExperienceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=base.RegressionTests.asyncSetUp
    asyncTearDown=base.RegressionTests.asyncTearDown
    state=base.RegressionTests.state
    message=base.RegressionTests.message
    callback=base.RegressionTests.callback
    new_ad=base.RegressionTests.new_ad
    new_order=base.RegressionTests.new_order
    confirm_preview=base.RegressionTests.confirm_preview

    async def test_price_visible_before_content_and_preview_has_no_payment(self):
        await self.message(kb.BTN_ORDER)
        self.assertIn('34,990',self.session.calls[-1].text)
        self.assertIn('1 soat',self.session.calls[-1].text)
        await self.message('Internet do‘kon kerak')
        self.assertEqual(await self.state().get_state(),Edit.preview.state)
        self.assertEqual(await repo.count_payments(),0)
        self.assertEqual((await repo.list_orders_by_user(1))[0]['status'],'DRAFT')

    async def test_edit_preview_resume_restart_and_stale_submit(self):
        await self.message(kb.BTN_ORDER);await self.message('Oldingi matn')
        data=await self.state().get_data();oid=data['editor_id'];version=data['editor_version']
        await self.callback(f'xp:edit:order:{oid}');await self.message('Yangilangan matn')
        await self.callback(f'xp:submit:order:{oid}:{version}')
        self.assertEqual((await repo.get_order(oid))['status'],'DRAFT')
        await self.message(kb.BTN_HOME);await database.close_db();await database.init_db()
        await self.callback(f'xp:resume:order:{oid}')
        self.assertEqual((await repo.get_order(oid))['text'],'Yangilangan matn')
        await self.confirm_preview()
        self.assertEqual((await repo.get_order(oid))['status'],'WAITING_RECEIPT')

    async def test_other_user_cannot_edit_or_submit_draft(self):
        oid=await self.new_order(uid=2,status='DRAFT')
        await self.callback(f'xp:edit:order:{oid}',uid=1)
        self.assertIsNone(await self.state(1).get_state())
        with self.assertRaises(ValueError):await xp.prepare_submission('order',oid,1,0)
        self.assertEqual((await repo.get_order(oid))['status'],'DRAFT')

    async def test_deadline_60_default_snapshot_admin_change_validation(self):
        ad=await self.new_ad();pid=await pay(1,'ad',ad,100,'receipt','unique')
        p=await repo.get_payment(pid)
        self.assertEqual(p['review_minutes'],60)
        difference=datetime.strptime(p['review_due_at'],'%Y-%m-%d %H:%M:%S')-datetime.strptime(p['created_at'],'%Y-%m-%d %H:%M:%S')
        self.assertAlmostEqual(difference.total_seconds(),3600,delta=2)
        await self.callback('admin:setkey:review_minutes',uid=900)
        await self.message('0',uid=900)
        self.assertEqual(await repo.get_int_setting('review_minutes'),60)
        await self.message('120',uid=900)
        self.assertEqual(await repo.get_int_setting('review_minutes'),120)
        ad2=await self.new_ad();pid2=await pay(1,'ad',ad2,100,'receipt2','unique2')
        self.assertEqual((await repo.get_payment(pid2))['review_minutes'],120)
        await database.init_db()
        self.assertEqual((await repo.get_payment(pid))['review_due_at'],p['review_due_at'])
        self.assertIn('1 soat',await xp.pending_text(p))
        self.assertIn('Toshkent',await xp.pending_text(p))

    async def test_review_setting_not_changeable_by_user(self):
        await self.callback('admin:setkey:review_minutes',uid=1)
        await self.message('120',uid=1)
        self.assertEqual(await repo.get_int_setting('review_minutes'),60)

    async def test_overdue_notification_once_no_automatic_approval(self):
        ad=await self.new_ad();pid=await pay(1,'ad',ad,100,'receipt','unique')
        db=await database.get_db();await db.execute("UPDATE payments SET review_due_at=datetime('now','-1 minute') WHERE id=?",(pid,))
        await xp.alerts(self.bot);await xp.alerts(self.bot)
        p=await repo.get_payment(pid);self.assertEqual(p['status'],'WAITING_ADMIN')
        cur=await db.execute("SELECT text FROM notifications WHERE user_id=1 AND text LIKE '%kechikdi%'")
        self.assertEqual(len(await cur.fetchall()),1)

    async def test_wallet_requires_moderation_and_rejection_refunds_once(self):
        ad=await self.new_ad();await repo.add_balance(1,50000)
        pid=await pay(1,'ad',ad,34990)
        self.assertEqual((await repo.get_ad(ad))['status'],'WAITING_ADMIN')
        self.assertEqual((await repo.get_payment(pid))['payment_source'],'WALLET')
        self.assertEqual(await repo.get_balance(1),15010)
        results=await asyncio.gather(moderate(pid,900,False,'Kontent mos emas'),moderate(pid,901,False,'Mos emas'),return_exceptions=True)
        self.assertEqual(sum(isinstance(x,ValueError) for x in results),1)
        self.assertEqual(await repo.get_balance(1),50000)

    async def test_cash_rejection_does_not_credit_wallet(self):
        ad=await self.new_ad();pid=await pay(1,'ad',ad,500,'receipt','unique')
        await moderate(pid,900,False,'Chek tasdiqlanmadi')
        self.assertEqual(await repo.get_balance(1),0)

    async def test_cash_revenue_excludes_bonus_wallet_and_free(self):
        await repo.add_balance(1,1000)
        pid=await pay(1,'ad',await self.new_ad(),500);await moderate(pid,900,True)
        pid=await pay(1,'ad',await self.new_ad(),200,'receipt','unique');await moderate(pid,900,True)
        await repo.set_setting('paid_enabled','0')
        pid=await submit_free('ad',await self.new_ad(),1);await moderate(pid,900,True)
        stats=await repo.get_general_stats();self.assertEqual(stats['total_revenue'],200)
        await self.callback('xp:analytics',uid=900)
        text=self.session.calls[-1].text
        self.assertIn('Karta tushumi: 200',text);self.assertIn('Balansdan sarflangan: 500',text)

    async def test_open_orders_filter_omits_assigned_and_blocked(self):
        one_id=await self.new_order();await repo.update_order(one_id,category='bot')
        other=await self.new_order(status='ASSIGNED');await repo.update_order(other,category='bot')
        await self.callback('xp:browse:bot:0')
        buttons=[b.callback_data for row in self.session.calls[-1].reply_markup.inline_keyboard for b in row]
        self.assertIn('order:apply:'+(await repo.get_order(one_id))['public_code'],buttons)
        self.assertNotIn('order:apply:'+(await repo.get_order(other))['public_code'],buttons)

    async def test_notifications_require_opt_in_and_respect_opt_out(self):
        oid=await self.new_order();await repo.update_order(oid,category='web',published_at=repo._now())
        await xp.alerts(self.bot)
        db=await database.get_db();self.assertEqual((await one(db,"SELECT COUNT(*) n FROM notifications WHERE event_type='MATCHED_ORDER'"))['n'],0)
        await self.callback('xp:sub:web')
        await xp.alerts(self.bot);await xp.alerts(self.bot)
        self.assertEqual((await one(db,"SELECT COUNT(*) n FROM notifications WHERE event_type='MATCHED_ORDER'"))['n'],1)
        await self.callback('xp:sub:off')
        before=len(self.session.calls);await flush_notifications(self.bot)
        self.assertFalse(any('yangi zakaz' in (getattr(m,'text',None) or '') for m in self.session.calls[before:]))

    async def test_profile_edit_and_invalid_portfolio(self):
        await self.message('👤 Profilim')
        await self.callback('xp:pedit:bio');await self.message('Telegram bot dasturchisi')
        db=await database.get_db();self.assertEqual((await one(db,'SELECT bio FROM profiles WHERE user_id=1'))['bio'],'Telegram bot dasturchisi')
        with self.assertRaises(ValueError):await xp.profile_save(1,'portfolio','javascript:alert(1)')
        await xp.profile_save(1,'portfolio','https://example.com/work')
        await self.callback('xp:profile:1',uid=2)
        self.assertIn('https://example.com/work',self.session.calls[-1].text)
        self.assertFalse(any('xp:pedit:' in (b.callback_data or '') for row in self.session.calls[-1].reply_markup.inline_keyboard for b in row))

    async def completed(self):
        oid=await self.new_order();await repo.update_order(oid,status='COMPLETED',assigned_to=1,completed_at=repo._now())
        return oid

    async def test_reviews_participants_completed_only_duplicate_and_moderated(self):
        oid=await self.new_order()
        with self.assertRaises(ValueError):await xp.review_create(oid,2,5,'Ajoyib ish')
        oid=await self.completed()
        with self.assertRaises(ValueError):await xp.review_create(oid,88,5,'Ajoyib ish')
        rid=await xp.review_create(oid,2,5,'Vaqtida bajarildi')
        with self.assertRaises(ValueError):await xp.review_create(oid,2,4,'Yana bir fikr')
        with self.assertRaises(ValueError):await xp.review_moderate(rid,1,True)
        await self.callback('xp:profile:1',uid=2);self.assertIn('Hali fikr yo‘q',self.session.calls[-1].text)
        await xp.review_moderate(rid,900,True)
        await self.callback('xp:profile:1',uid=2);self.assertIn('5.0/5',self.session.calls[-1].text)

    async def test_showcase_both_consents_and_withdrawal(self):
        oid=await self.completed()
        await xp.consent(oid,1,True)
        await self.message('🏆 Bajarilgan ishlar');self.assertIn('Hozircha',self.session.calls[-1].text)
        await xp.consent(oid,2,True)
        before=len(self.session.calls);await self.message('🏆 Bajarilgan ishlar')
        code=(await repo.get_order(oid))['public_code']
        self.assertTrue(any(code in (getattr(m,'text',None) or '') for m in self.session.calls[before:]))
        await xp.consent(oid,1,False)
        await self.message('🏆 Bajarilgan ishlar');self.assertIn('Hozircha',self.session.calls[-1].text)

    async def test_support_private_permissions_and_one_answer(self):
        oid=await self.new_order()
        with self.assertRaises(ValueError):await xp.support_ticket(1,'Begona zakaz bo‘yicha',oid)
        tid=await xp.support_ticket(1,'To‘lovni tekshirib bering')
        with self.assertRaises(ValueError):await xp.answer_ticket(tid,1,'Javob')
        await xp.answer_ticket(tid,900,'Chek tekshirildi')
        with self.assertRaises(ValueError):await xp.answer_ticket(tid,901,'Ikkinchi javob')
        await flush_notifications(self.bot)
        self.assertTrue(any(getattr(m,'chat_id',None)==1 and 'Chek tekshirildi' in (getattr(m,'text',None) or '') for m in self.session.calls))

    async def test_reopen_preserves_rejected_until_owner_reconsiders(self):
        await repo.get_or_create_user(3,'three','Three')
        oid=await self.new_order();a=await create_application(oid,1,'100','2 kun','A');b=await create_application(oid,3,'200','1 kun','B')
        await decide_application(a,2,False);await decide_application(b,2,True);await reopen(oid,2,0)
        from services.applications import get_application
        self.assertEqual((await get_application(a))['status'],'REJECTED')
        await self.callback(f'xp:reconsider:{a}',uid=1)
        self.assertEqual((await get_application(a))['status'],'REJECTED')
        await self.callback(f'xp:reconsider:{a}',uid=2)
        self.assertEqual((await get_application(a))['status'],'PENDING')

    async def test_user_lists_paginate(self):
        for _ in range(9):await self.new_order(uid=1)
        await self.message(kb.BTN_MY_ORDERS)
        buttons=[b.callback_data for row in self.session.calls[-1].reply_markup.inline_keyboard for b in row]
        self.assertEqual(sum(b.startswith('order:view:') for b in buttons),6)
        self.assertIn('xp:mine:order:all:6',buttons)

    async def test_analytics_funnel_events_idempotent_per_stage(self):
        await self.message(kb.BTN_ORDER);await self.message('Yangi dastur kerak')
        await self.confirm_preview();await self.message(photo=True)
        db=await database.get_db()
        rows=await (await db.execute("SELECT event FROM analytics_events WHERE user_id=1")).fetchall()
        events={r[0] for r in rows}
        self.assertTrue({'VISIT','FLOW_STARTED','DRAFT_SAVED','SUBMITTED'}<=events)
        self.assertEqual(sum(r[0]=='VISIT' for r in rows),1)

    async def test_discard_draft_requires_owner_current_version_and_confirmation(self):
        await self.message(kb.BTN_ORDER);await self.message('O‘chiriladigan qoralama')
        data=await self.state().get_data();oid=data['editor_id'];version=data['editor_version']
        await self.callback(f'xp:discard:order:{oid}')
        self.assertEqual((await repo.get_order(oid))['status'],'DRAFT')
        await self.callback(f'xp:discardyes:order:{oid}:{version}',uid=2)
        self.assertEqual((await repo.get_order(oid))['status'],'DRAFT')
        await self.callback(f'xp:discardyes:order:{oid}:{version}')
        self.assertEqual((await repo.get_order(oid))['status'],'DELETED')

    async def test_custom_admin_copy_survives_v4_migration(self):
        db=await database.get_db()
        await repo.set_setting('start_text','Adminning maxsus salomlashuvi')
        await db.execute("DELETE FROM settings WHERE key='experience_v4'")
        await database.init_db()
        self.assertEqual(await repo.get_setting('start_text'),'Adminning maxsus salomlashuvi')

    async def test_resuming_payment_keeps_previously_quoted_price(self):
        await self.message(kb.BTN_ORDER);await self.message('Sayt kerak')
        await self.confirm_preview();oid=(await self.state().get_data())['item_id']
        await self.message(kb.BTN_HOME);await repo.set_setting('order_price','50000')
        await self.callback(f'xp:resume:order:{oid}');await self.confirm_preview()
        self.assertEqual((await self.state().get_data())['price'],34990)
        self.assertEqual((await repo.get_order(oid))['quoted_price'],34990)
