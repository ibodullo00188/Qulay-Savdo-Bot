"""Serialize all updates and background jobs while SQLite can be replaced."""
import asyncio
from contextlib import asynccontextmanager
from aiogram import BaseMiddleware

_lock = None
_loop = None


def operation_lock():
    global _lock, _loop
    loop = asyncio.get_running_loop()
    if _loop is not loop:
        _lock, _loop = asyncio.Lock(), loop
    return _lock


@asynccontextmanager
async def guarded_work():
    async with operation_lock():
        yield


class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        async with guarded_work():
            return await handler(event, data)
