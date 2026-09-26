"""Portable Telegram backup, validated staging, atomic SQLite replacement and rollback."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import tempfile
import time
import zipfile

import database.db as database

APP = 'qulay-savdo-bot'
MAX_ARCHIVE = 19 * 1024 * 1024
MAX_DATABASE = 128 * 1024 * 1024
TTL = 10 * 60
pending = {}


class BackupError(ValueError):
    pass


def validate_database(path):
    required = {'users': {'telegram_id','balance'}, 'orders': {'id','public_code','status'},
                'ads': {'id','public_code','status'}, 'payments': {'id','amount','status'},
                'settings': {'key','value'}}
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('PRAGMA trusted_schema=OFF')
            objects = list(db.execute('SELECT type,name,sql FROM sqlite_master'))
            if any(kind in ('trigger','view') or 'CREATE VIRTUAL TABLE' in (sql or '').upper()
                   for kind, name, sql in objects):
                raise BackupError('Backup ichida ruxsat etilmagan baza obyektlari bor.')
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise BackupError('Backup bazasining yaxlitligi buzilgan.')
            for table, columns in required.items():
                actual = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
                if not columns <= actual:
                    raise BackupError('Bu Qulay Savdo Bot backup bazasi emas.')
            return {table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                    for table in ('users','orders','ads','payments')}
    except sqlite3.DatabaseError as exc:
        raise BackupError('Backup bazasi ochilmadi yoki buzilgan.') from exc


def snapshot(source, target):
    # SQLite backup captures committed WAL, unlike copying just the .db file.
    with closing(sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
    os.chmod(target, 0o600)
    if Path(target).stat().st_size > MAX_DATABASE:
        raise BackupError('Baza 128 MiB dan katta. Bu hajm uchun serverdan alohida backup oling.')
    validate_database(target)


def create_archive(bot_id):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / 'database.sqlite3'
        snapshot(database.DATABASE_PATH, db_path)
        data = db_path.read_bytes()
        created = datetime.now(timezone.utc)
        manifest = {'app': APP, 'format_version': 1, 'bot_id': int(bot_id),
                    'created_at': created.isoformat(), 'sha256': hashlib.sha256(data).hexdigest(),
                    'database_bytes': len(data)}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
            archive.writestr('database.sqlite3', data)
        payload = buffer.getvalue()
        if len(payload) > MAX_ARCHIVE:
            raise BackupError('Backup 19 MiB dan oshdi; uni Telegram orqali qayta yuklab bo‘lmaydi.')
        return payload, f'qulay-savdo-backup-{created:%Y%m%d-%H%M%S}.zip'


def validate_archive(payload, bot_id, destination):
    if len(payload) > MAX_ARCHIVE:
        raise BackupError('Backup fayli 19 MiB dan oshmasin.')
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            if len(infos) != 2 or {i.filename for i in infos} != {'manifest.json','database.sqlite3'}:
                raise BackupError('Bot yaratgan backup ZIP faylini yuboring.')
            if archive.getinfo('manifest.json').file_size > 65536 or archive.getinfo('database.sqlite3').file_size > MAX_DATABASE:
                raise BackupError('Backup ichidagi fayllar hajmi ruxsat etilganidan katta.')
            manifest = json.loads(archive.read('manifest.json'))
            if not isinstance(manifest, dict) or manifest.get('app') != APP or manifest.get('format_version') != 1:
                raise BackupError('Backup formati mos emas.')
            if manifest.get('bot_id') != int(bot_id):
                raise BackupError('Bu backup boshqa botga tegishli.')
            data = archive.read('database.sqlite3')
            if len(data) != manifest.get('database_bytes') or hashlib.sha256(data).hexdigest() != manifest.get('sha256'):
                raise BackupError('Backup fayli o‘zgargan yoki buzilgan.')
            created = datetime.fromisoformat(manifest['created_at'])
            if created.tzinfo is None:
                raise BackupError('Backup sanasi noto‘g‘ri.')
        Path(destination).write_bytes(data)
        os.chmod(destination, 0o600)
        counts = validate_database(destination)
        return {'created_at': created, 'counts': counts}
    except BackupError:
        raise
    except (zipfile.BadZipFile, KeyError, TypeError, ValueError, RuntimeError, NotImplementedError) as exc:
        raise BackupError('Yaroqsiz yoki buzilgan backup. Bot yuborgan ZIP faylini tanlang.') from exc


def discard(token):
    item = pending.pop(token, None)
    if item:
        shutil.rmtree(item['directory'], ignore_errors=True)


def discard_user(user_id):
    for token, item in list(pending.items()):
        if item['user_id'] == user_id or time.monotonic() >= item['expires']:
            discard(token)


def stage_archive(payload, bot_id, user_id):
    discard_user(user_id)
    directory = tempfile.mkdtemp(prefix='qulay-restore-')
    path = Path(directory) / 'candidate.db'
    try:
        details = validate_archive(payload, bot_id, path)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    token = secrets.token_hex(16)
    pending[token] = dict(details, directory=directory, path=path, user_id=user_id,
                          expires=time.monotonic() + TTL)
    return token, details


def get_staged(token, user_id):
    item = pending.get(token)
    if not item or item['user_id'] != user_id:
        raise BackupError('Tiklash so‘rovi topilmadi. Backupni qayta yuboring.')
    if time.monotonic() >= item['expires']:
        discard(token)
        raise BackupError('Tasdiqlash vaqti tugadi. Backupni qayta yuboring.')
    return item


def install_database(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.restore-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as dest, open(source, 'rb') as src:
            shutil.copyfileobj(src, dest)
            dest.flush()
            os.fsync(dest.fileno())
        validate_database(tmp)
        for suffix in ('-wal','-shm'):
            Path(str(target) + suffix).unlink(missing_ok=True)
        os.replace(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)


async def _restore(item, admin_id):
    path = database.DATABASE_PATH
    rollback = Path(item['directory']) / 'before-restore.db'
    await asyncio.to_thread(validate_database, item['path'])
    await asyncio.to_thread(snapshot, path, rollback)
    await database.close_db()
    try:
        await asyncio.to_thread(install_database, item['path'], path)
        await database.init_db()
        db = await database.get_db()
        # Telegram posts are external: an old snapshot must not republish ambiguous posts.
        for table in ('ads','orders'):
            await db.execute(f"UPDATE {table} SET status='PUBLICATION_REVIEW' WHERE status IN ('READY_TO_PUBLISH','PUBLISHING','PROCESSING')")
        await db.execute('UPDATE orders SET channel_sync_pending=0')
        await db.execute("UPDATE notifications SET sent_at=datetime('now') WHERE sent_at IS NULL")
        # Do not restore stale admin file-upload states; preserve all other draft states.
        await db.execute("DELETE FROM fsm_state WHERE state LIKE 'BackupRestore:%'")
        await db.execute("INSERT INTO admin_actions(admin_id,action_type,description) VALUES (?, 'RESTORE_BACKUP', ?)",
                         (admin_id, item['created_at'].isoformat()))
    except BaseException:
        await database.close_db()
        await asyncio.to_thread(install_database, rollback, path)
        await database.init_db()
        raise


async def restore_staged(token, admin_id):
    item = get_staged(token, admin_id)
    task = asyncio.create_task(_restore(item, admin_id))
    try:
        # Finish replacement or rollback before the global operation lock is released.
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
    finally:
        discard(token)
