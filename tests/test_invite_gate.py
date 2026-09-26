"""Referral admission: admin settings, unique members, per-post spending and UI."""
import asyncio
import unittest
from unittest.mock import patch

import test_regressions as base
import database.db as database
import database.repo as repo
from services import invite_gate as gate
from services.upgrade import membership, submit_free
from services.transactions import one
from handlers.experience import Edit
import keyboards.keyboards as kb


class InviteGateTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=base.RegressionTests.asyncSetUp
    asyncTearDown=base.RegressionTests.asyncTearDown
    message=base.RegressionTests.message
    callback=base.RegressionTests.callback
    state=base.RegressionTests.state
    new_ad=base.RegressionTests.new_ad
    new_order=base.RegressionTests.new_order
    confirm_preview=base.RegressionTests.confirm_preview

    async def configure(self,mode='once',count=3):
        await repo.set_setting('paid_enabled','0')
        await gate.change(900,'count',str(count))
        await gate.change(900,'mode',mode)
        await gate.change(900,'enabled','1')
        db=await database.get_db()
        await db.execute('INSERT OR IGNORE INTO referral_links VALUES (?,1)',('https://t.me/+test',))

    async def join(self,uid,present=True,event=1,bot=False):
        await membership(uid,present,'https://t.me/+test',event,bot)

    async def test_default_disabled_no_fixed_count(self):
        opts=await gate.settings()
        self.assertFalse(opts['enabled']);self.assertEqual(opts['count'],0)
        self.assertEqual(opts['mode'],'')
        await repo.set_setting('paid_enabled','0')
        ad=await self.new_ad()
        await submit_free('ad',ad,1)
        self.assertEqual((await repo.get_ad(ad))['status'],'WAITING_ADMIN')

    async def test_admin_must_choose_count_mode_and_users_cannot_change(self):
        with self.assertRaises(ValueError):await gate.change(900,'enabled','1')
        with self.assertRaises(ValueError):await gate.change(1,'count','5')
        for invalid in ('0','-1','10001','abc','2.5'):
            with self.assertRaises(ValueError):await gate.change(900,'count',invalid)
        await gate.change(900,'count','5')
        with self.assertRaises(ValueError):await gate.change(900,'enabled','1')
        await gate.change(900,'mode','per_post')
        await gate.change(900,'enabled','1')
        self.assertEqual((await gate.settings())['count'],5)

    async def test_once_unlock_retained_after_departure_count_change_and_restart(self):
        await self.configure(count=3)
        for uid in (10,11):await self.join(uid)
        ad=await self.new_ad()
        with self.assertRaises(gate.InviteRequired):await submit_free('ad',ad,1)
        await self.join(12)
        await submit_free('ad',ad,1)
        for uid in (10,11,12):await self.join(uid,False,event=2)
        await gate.change(900,'count','10')
        await database.close_db();await database.init_db()
        order=await self.new_order(uid=1,status='DRAFT')
        await submit_free('order',order,1)
        self.assertEqual((await repo.get_order(order))['status'],'WAITING_ADMIN')

    async def test_unique_members_bots_self_and_rejoin(self):
        await self.configure(count=3)
        await self.join(1)
        await self.join(20,bot=True)
        await self.join(10);await self.join(10,event=2)
        await self.join(10,False,event=3);await self.join(10,event=4)
        db=await database.get_db()
        count,granted=await gate.progress(db,1,await gate.settings())
        self.assertEqual(count,1);self.assertFalse(granted)

    async def test_per_post_consumes_only_on_submission_not_preview(self):
        await self.configure('per_post',2)
        await self.join(10);await self.join(11);await self.join(12)
        db=await database.get_db()
        await gate.require(db,1)
        self.assertEqual((await one(db,'SELECT COUNT(*) n FROM invite_spending'))['n'],0)
        ad=await self.new_ad()
        await submit_free('ad',ad,1)
        with self.assertRaises(ValueError):await submit_free('ad',ad,1)
        self.assertEqual((await one(db,'SELECT COUNT(*) n FROM invite_spending'))['n'],2)
        order=await self.new_order(uid=1,status='DRAFT')
        with self.assertRaises(gate.InviteRequired):await submit_free('order',order,1)
        await self.join(10,False,event=2);await self.join(10,event=3)
        with self.assertRaises(gate.InviteRequired):await submit_free('order',order,1)
        await self.join(13)
        await submit_free('order',order,1)
        self.assertEqual((await one(db,'SELECT COUNT(*) n FROM invite_spending'))['n'],4)

    async def test_concurrent_posts_cannot_spend_same_invites(self):
        await self.configure('per_post',2)
        await self.join(10);await self.join(11)
        a,b=await self.new_ad(),await self.new_ad()
        results=await asyncio.gather(submit_free('ad',a,1),submit_free('ad',b,1),return_exceptions=True)
        self.assertEqual(sum(isinstance(r,int) for r in results),1)
        self.assertEqual(sum(isinstance(r,gate.InviteRequired) for r in results),1)

    async def test_disabled_and_paid_modes_do_not_require_invites(self):
        await self.configure('per_post',4)
        await gate.change(900,'enabled','0')
        await submit_free('ad',await self.new_ad(),1)
        await gate.change(900,'enabled','1')
        await repo.set_setting('paid_enabled','1')
        await gate.require(await database.get_db(),1)
        self.assertEqual((await gate.settings())['count'],4)

    async def test_unused_member_leaving_before_submission_not_counted(self):
        await self.configure('per_post',2)
        await self.join(10);await self.join(11);await self.join(11,False,event=2)
        with self.assertRaises(gate.InviteRequired):await submit_free('ad',await self.new_ad(),1)

    async def test_once_to_per_post_keeps_independent_history(self):
        await self.configure('once',2)
        await self.join(10);await self.join(11)
        await submit_free('ad',await self.new_ad(),1)
        await gate.change(900,'mode','per_post')
        await submit_free('ad',await self.new_ad(),1)
        with self.assertRaises(gate.InviteRequired):await submit_free('ad',await self.new_ad(),1)
        await gate.change(900,'mode','once')
        await submit_free('ad',await self.new_ad(),1)

    async def test_admin_import_bypass_is_admin_only(self):
        await self.configure('per_post',5)
        with self.assertRaises(ValueError):await submit_free('ad',await self.new_ad(),1,admin=True)
        await repo.get_or_create_user(900,'admin','Admin')
        ad=await self.new_ad(uid=900)
        await submit_free('ad',ad,900,admin=True)
        self.assertEqual((await repo.get_ad(ad))['status'],'WAITING_ADMIN')

    async def test_ui_blocks_then_resumes_same_draft_with_link(self):
        await self.configure(count=3)
        await self.message(kb.BTN_ORDER);await self.message('Build a useful website')
        data=await self.state().get_data();iid=data['editor_id']
        await self.confirm_preview()
        self.assertEqual((await repo.get_order(iid))['status'],'DRAFT')
        self.assertEqual(await repo.count_pending_payments(),0)
        self.assertTrue(any('https://t.me/+test' in (getattr(c,'text','') or '') for c in self.session.calls))
        for uid in (10,11,12):await self.join(uid)
        await self.confirm_preview()
        self.assertEqual((await repo.get_order(iid))['status'],'WAITING_ADMIN')
        self.assertEqual(await repo.count_pending_payments(),1)
        self.assertEqual(len(await repo.list_orders_by_user(1)),1)

    async def test_admin_count_ui_and_unauthorized_callbacks(self):
        await self.callback('ig:enabled:1',uid=1)
        self.assertFalse((await gate.settings())['enabled'])
        await self.callback('ig:count',uid=900)
        await self.message('7',uid=900)
        await self.callback('ig:mode:per_post',uid=900)
        await self.callback('ig:enabled:1',uid=900)
        opts=await gate.settings()
        self.assertTrue(opts['enabled']);self.assertEqual(opts['count'],7)
        await self.callback('ig:enabled:0',uid=900)
        self.assertFalse((await gate.settings())['enabled'])
