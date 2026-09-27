import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, AsyncMock

import test_regressions as base
import database.repo as repo
from services import auto_backup, backup


class AutoBackupTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base.RegressionTests.asyncSetUp
    asyncTearDown = base.RegressionTests.asyncTearDown

    def at(self, hour, minute=0):
        return datetime(2026, 9, 27, hour, minute, tzinfo=auto_backup.TASHKENT)

    def test_local_time_boundaries(self):
        self.assertEqual(auto_backup.latest_slot(self.at(8, 59)), '2026-09-26T21:00:00+05:00')
        self.assertEqual(auto_backup.latest_slot(self.at(9)), '2026-09-27T09:00:00+05:00')
        self.assertEqual(auto_backup.latest_slot(self.at(20, 59)), '2026-09-27T09:00:00+05:00')
        self.assertEqual(auto_backup.latest_slot(self.at(21)), '2026-09-27T21:00:00+05:00')

    async def test_admins_only_portable_fresh_backups_and_restart_receipts(self):
        await auto_backup.send_due(self.bot, self.at(9))
        files = [x for x in self.session.calls if type(x).__name__ == 'SendDocument']
        self.assertEqual({x.chat_id for x in files}, {900, 901})
        details = backup.validate_archive(files[0].document.data, self.bot.id, Path(self.tmp.name)/'auto.db')
        self.assertEqual(details['counts']['users'], 2)
        await auto_backup.send_due(self.bot, self.at(12))
        self.assertEqual(len(self.session.calls), 2)
        await repo.get_or_create_user(3, 'three', 'Three')
        await auto_backup.send_due(self.bot, self.at(21))
        self.assertEqual(len(self.session.calls), 4)
        details = backup.validate_archive(self.session.calls[-1].document.data, self.bot.id, Path(self.tmp.name)/'evening.db')
        self.assertEqual(details['counts']['users'], 3)

    async def test_failed_admin_retried_without_duplicate_success(self):
        calls = []
        async def send(uid, *args, **kwargs):
            calls.append(uid)
            if uid == 900:
                raise TimeoutError()
        with patch.object(self.bot, 'send_document', side_effect=send):
            await auto_backup.send_due(self.bot, self.at(9))
        self.assertEqual(calls, [900, 901])
        with patch.object(self.bot, 'send_document', new_callable=AsyncMock) as send:
            await auto_backup.send_due(self.bot, self.at(9, 5))
            self.assertEqual(send.await_count, 1)
            self.assertEqual(send.await_args.args[0], 900)

    async def test_no_admin_does_not_create_archive(self):
        with patch.object(repo, 'list_admin_ids', new_callable=AsyncMock, return_value=[]), patch.object(backup, 'create_archive') as create:
            await auto_backup.send_due(self.bot, self.at(9))
            create.assert_not_called()
