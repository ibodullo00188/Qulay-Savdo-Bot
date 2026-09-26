"""Offline round-trip tests: real SQLite, aiogram routing, simulated Telegram transport."""
import asyncio
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch
import zipfile

import test_regressions as base
from aiogram.types import Message, Update, User, Chat, Document
import database.db as database
import database.repo as repo
from services import backup
from services.maintenance import guarded_work, MaintenanceMiddleware
from handlers.admin.backup import BackupRestore


class ManualBackupTests(unittest.IsolatedAsyncioTestCase):
    message = base.RegressionTests.message
    callback = base.RegressionTests.callback
    state = base.RegressionTests.state

    async def asyncSetUp(self):
        await base.RegressionTests.asyncSetUp(self)
        db = await database.get_db()
        await db.execute('UPDATE users SET balance=45000 WHERE telegram_id=1')
        await self.state(1).set_state('Example:draft')
        await self.state(1).set_data({'text':'Saved draft'})

    async def asyncTearDown(self):
        for token in list(backup.pending):
            backup.discard(token)
        await base.RegressionTests.asyncTearDown(self)

    async def document(self, payload, uid=900, filename='backup.zip'):
        self.update_id += 1
        msg = Message(message_id=self.update_id, date=datetime.now(timezone.utc),
            chat=Chat(id=uid,type='private'), from_user=User(id=uid,is_bot=False,first_name='Admin'),
            document=Document(file_id='backup-file',file_unique_id='backup-unique',file_name=filename,file_size=len(payload)))
        async def download(document,destination,**kwargs):
            destination.write(payload)
            destination.seek(0)
            return destination
        with patch.object(self.bot,'download',side_effect=download):
            await base.DP.feed_update(self.bot,Update(update_id=self.update_id,message=msg))

    def archive(self):
        return backup.create_archive(self.bot.id)[0]

    def rewrite_zip(self, payload, mutate):
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            manifest=json.loads(z.read('manifest.json'))
            data=z.read('database.sqlite3')
        mutate(manifest)
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            z.writestr('manifest.json',json.dumps(manifest));z.writestr('database.sqlite3',data)
        return out.getvalue()

    async def test_admin_export_sends_portable_zip(self):
        await self.callback('backup:create',uid=900)
        files=[c for c in self.session.calls if type(c).__name__=='SendDocument']
        self.assertEqual(len(files),1)
        dest=Path(self.tmp.name)/'check.db'
        details=backup.validate_archive(files[0].document.data,self.bot.id,dest)
        self.assertEqual(details['counts']['users'],2)
        self.assertTrue(files[0].document.filename.endswith('.zip'))

    async def test_user_cannot_export_or_start_restore(self):
        await self.callback('backup:create',uid=1)
        await self.callback('backup:restore',uid=1)
        await self.message('/backup',uid=1)
        self.assertFalse(any(type(c).__name__=='SendDocument' for c in self.session.calls))
        self.assertEqual(await self.state(1).get_state(),'Example:draft')

    async def test_restore_roundtrip_replaces_balance_and_preserves_drafts(self):
        payload=self.archive()
        db=await database.get_db()
        await db.execute('UPDATE users SET balance=1 WHERE telegram_id=1')
        await self.callback('backup:restore',uid=900)
        await self.document(payload)
        self.assertEqual(await self.state(900).get_state(),BackupRestore.confirmation.state)
        self.assertEqual(await repo.get_balance(1),1)  # upload alone never restores
        token=next(iter(backup.pending))
        await self.callback('backup:confirm:'+token,uid=900)
        self.assertEqual(await repo.get_balance(1),45000)
        self.assertEqual((await self.state(1).get_data())['text'],'Saved draft')
        self.assertIsNone(await self.state(900).get_state())
        self.assertNotIn(token,backup.pending)
        # Before-restore backup was sent to allow reversing the change.
        files=[c for c in self.session.calls if type(c).__name__=='SendDocument']
        self.assertEqual(len(files),1)
        dest=Path(self.tmp.name)/'prior.db'
        backup.validate_archive(files[0].document.data,self.bot.id,dest)
        import sqlite3
        with sqlite3.connect(dest) as old:
            self.assertEqual(old.execute('SELECT balance FROM users WHERE telegram_id=1').fetchone()[0],1)
        await self.callback('backup:confirm:'+token,uid=900)
        self.assertEqual(await repo.get_balance(1),45000)

    async def test_restore_after_database_loss_with_env_admin(self):
        payload=self.archive()
        await database.close_db()
        Path(database.DATABASE_PATH).unlink()
        await database.init_db()
        self.assertIsNone(await repo.get_user(1))
        await self.callback('backup:restore',uid=900)
        await self.document(payload)
        await self.callback('backup:confirm:'+next(iter(backup.pending)),uid=900)
        self.assertEqual(await repo.get_balance(1),45000)

    async def test_invalid_and_wrong_bot_upload_never_mutate_database(self):
        await self.callback('backup:restore',uid=900)
        for payload in (b'broken zip', self.rewrite_zip(self.archive(),lambda m:m.update(bot_id=999999)),
                        self.rewrite_zip(self.archive(),lambda m:m.update(sha256='wrong'))):
            await self.document(payload)
            self.assertEqual(await repo.get_balance(1),45000)
            self.assertFalse(backup.pending)
            self.assertEqual(await self.state(900).get_state(),BackupRestore.waiting_file.state)

    async def test_cancel_expiry_and_different_admin_cannot_confirm(self):
        await self.callback('backup:restore',uid=900)
        await self.document(self.archive())
        token=next(iter(backup.pending))
        await self.callback('backup:confirm:'+token,uid=901)
        self.assertIn(token,backup.pending)
        await self.message('/cancel',uid=900)
        self.assertFalse(backup.pending)
        self.assertIsNone(await self.state(900).get_state())
        token,_=backup.stage_archive(self.archive(),self.bot.id,900)
        backup.pending[token]['expires']=time.monotonic()-1
        with self.assertRaises(backup.BackupError):backup.get_staged(token,900)
        self.assertNotIn(token,backup.pending)

    async def test_rollback_when_migration_fails(self):
        payload=self.archive()
        db=await database.get_db()
        await db.execute('UPDATE users SET balance=777 WHERE telegram_id=1')
        token,_=backup.stage_archive(payload,self.bot.id,900)
        original=database.init_db
        calls=0
        async def fail_once():
            nonlocal calls
            calls+=1
            if calls==1:raise RuntimeError('migration failed')
            await original()
        with patch.object(database,'init_db',side_effect=fail_once):
            with self.assertRaises(RuntimeError):await backup.restore_staged(token,900)
        self.assertEqual(await repo.get_balance(1),777)
        self.assertEqual(calls,2)

    async def test_before_backup_send_failure_aborts_restore(self):
        payload=self.archive()
        db=await database.get_db()
        await db.execute('UPDATE users SET balance=123 WHERE telegram_id=1')
        await self.callback('backup:restore',uid=900)
        await self.document(payload)
        token=next(iter(backup.pending))
        with patch('handlers.admin.backup.send_backup',side_effect=RuntimeError('send failed')):
            await self.callback('backup:confirm:'+token,uid=900)
        self.assertEqual(await repo.get_balance(1),123)
        self.assertIn(token,backup.pending)

    async def test_archive_limits_traversal_and_sqlite_objects_rejected(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr('../evil','test')
        with self.assertRaises(backup.BackupError):backup.validate_archive(out.getvalue(),self.bot.id,Path(self.tmp.name)/'x')
        with patch.object(backup,'MAX_ARCHIVE',2):
            with self.assertRaises(backup.BackupError):backup.create_archive(self.bot.id)
        db=await database.get_db()
        await db.execute('CREATE VIEW unsupported AS SELECT * FROM users')
        with self.assertRaises(backup.BackupError):self.archive()

    async def test_restored_queues_do_not_replay_old_channel_actions(self):
        db=await database.get_db()
        await db.execute("INSERT INTO orders(public_code,user_id,message_type,status,channel_sync_pending) VALUES ('Z-Q',1,'TEXT','READY_TO_PUBLISH',1)")
        await db.execute("INSERT INTO notifications(user_id,text) VALUES (1,'old notification')")
        token,_=backup.stage_archive(self.archive(),self.bot.id,900)
        await backup.restore_staged(token,900)
        db=await database.get_db()
        row=await (await db.execute("SELECT status,channel_sync_pending FROM orders WHERE public_code='Z-Q'")).fetchone()
        self.assertEqual(tuple(row),('PUBLICATION_REVIEW',0))
        self.assertIsNotNone((await (await db.execute('SELECT sent_at FROM notifications')).fetchone())[0])

    async def test_global_lock_blocks_background_jobs_during_update(self):
        entered, release, worker_entered=asyncio.Event(),asyncio.Event(),asyncio.Event()
        async def handler(event,data):
            entered.set();await release.wait()
        async def worker():
            async with guarded_work():worker_entered.set()
        task=asyncio.create_task(MaintenanceMiddleware()(handler,None,{}))
        await entered.wait()
        background=asyncio.create_task(worker())
        await asyncio.sleep(.01)
        self.assertFalse(worker_entered.is_set())
        release.set()
        await asyncio.gather(task,background)
        self.assertTrue(worker_entered.is_set())
