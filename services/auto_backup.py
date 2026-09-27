"""Twice-daily portable SQLite backups delivered only to current admins."""
import asyncio
from datetime import datetime, timedelta
import logging
from zoneinfo import ZoneInfo

from aiogram.types import BufferedInputFile
import database.repo as repo
from services import backup
from services.maintenance import guarded_work

logger = logging.getLogger(__name__)
TASHKENT = ZoneInfo('Asia/Tashkent')
HOURS = (9, 21)


def latest_slot(now=None):
    now = (now or datetime.now(TASHKENT)).astimezone(TASHKENT)
    for hour in reversed(HOURS):
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            return candidate.isoformat()
    return (now - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0).isoformat()


async def send_due(bot, now=None):
    """One snapshot per due run; failed recipients retry without resending successes.

    The maintenance lock also serializes backup creation with manual restore.
    Persisted receipts survive normal bot restarts. After downtime only the
    latest due slot is sent, using a fresh snapshot, never an invented old one.
    """
    slot = latest_slot(now)
    async with guarded_work():
        recipients = []
        for uid in await repo.list_admin_ids():
            if uid > 0 and await repo.get_setting(f'auto_backup_sent_{uid}') != slot:
                recipients.append(uid)
        if not recipients:
            return
        task = asyncio.create_task(asyncio.to_thread(backup.create_archive, bot.id))
        try:
            payload, filename = await asyncio.shield(task)
        except asyncio.CancelledError:
            # A thread cannot be cancelled: retain the restore lock until it ends.
            await task
            raise
        created = datetime.now(TASHKENT).strftime('%d.%m.%Y %H:%M')
        caption = (
            f'📦 Avtomatik backup · {created} (Toshkent)\n'
            'Har kuni 09:00 va 21:00 da yangi nusxa.\n'
            'Tiklash: Admin panel → Backupni tiklash → shu ZIP faylni forward qiling va tasdiqlang.\n'
            'Bu fayl faqat adminlarga yuborildi. Uni saqlab qo‘ying.'
        )
        for uid in recipients:
            try:
                await bot.send_document(uid, BufferedInputFile(payload, filename=filename),
                                        caption=caption, request_timeout=30)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('Automatic backup delivery failed for admin %s (%s)', uid, type(exc).__name__)
                continue
            await repo.set_setting(f'auto_backup_sent_{uid}', slot)


async def worker(bot):
    retry_at = 0.0
    checked_slot = None
    while True:
        loop = asyncio.get_running_loop()
        slot = latest_slot()
        if slot != checked_slot or loop.time() >= retry_at:
            try:
                await send_due(bot)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Automatic backup failed; will retry')
            checked_slot = slot
            retry_at = loop.time() + 300
        await asyncio.sleep(30)
