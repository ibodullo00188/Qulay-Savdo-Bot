import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_regressions as base
import main
import config
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SetMyName, SetMyCommands
from aiogram.types import BotCommand


class StartupProfileTests(unittest.IsolatedAsyncioTestCase):
    def bot(self):
        return SimpleNamespace(get_my_name=AsyncMock(return_value=SimpleNamespace(name='Old name')),
                               set_my_name=AsyncMock(), get_my_commands=AsyncMock(return_value=[]),
                               set_my_commands=AsyncMock())

    async def test_rate_limit_does_not_abort_or_sleep(self):
        bot = self.bot()
        bot.set_my_name.side_effect = TelegramRetryAfter(method=SetMyName(name='Name'), message='Too Many Requests', retry_after=63911)
        bot.set_my_commands.side_effect = TelegramRetryAfter(method=SetMyCommands(commands=[]), message='Too Many Requests', retry_after=63911)
        await main.configure_profile(bot)
        bot.set_my_commands.assert_awaited_once()

    async def test_unchanged_profile_not_rewritten(self):
        bot = self.bot()
        bot.get_my_name.return_value = SimpleNamespace(name=config.BOT_DISPLAY_NAME)
        bot.get_my_commands.return_value = [
            BotCommand(command='start', description='🏠 Bosh menyu'),
            BotCommand(command='menu', description='🏠 Bosh menyu'),
            BotCommand(command='cancel', description='❌ Bekor qilish'),
            BotCommand(command='admin', description='🛠 Admin panel'),
        ]
        await main.configure_profile(bot)
        bot.set_my_name.assert_not_awaited()
        bot.set_my_commands.assert_not_awaited()

    async def test_read_timeout_does_not_abort(self):
        bot = self.bot()
        bot.get_my_name.side_effect = TimeoutError()
        await main.configure_profile(bot)
        bot.set_my_name.assert_not_awaited()
        bot.set_my_commands.assert_awaited_once()
