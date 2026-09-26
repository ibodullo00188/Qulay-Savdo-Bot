"""
Ma'lumotlar bazasi bilan ishlash uchun yagona qatlam.

MUHIM QOIDA: ads / orders / payments / contact_unlocks / complaints
jadvallaridagi user_id maydoni har doim users.telegram_id qiymatini
saqlaydi (users.id emas). Shu tufayli handlerlarda alohida "ichki id"
qidirishga hojat yo'q — hamma joyda to'g'ridan-to'g'ri telegram_id
ishlatiladi.
"""
import random
import re
from datetime import datetime, timezone

from database.db import get_db
import config

PUBLIC_CODE_RE = re.compile(r"^(AD|ZK)-\d{4,48}$")
SIGN_DEFAULT = "🤖 Qulay Savdo Bot — siz izlayotgan tayyor botlar sotuvda!"


def row_to_dict(row):
    if row is None:
        return None
    return dict(row)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ================= PUBLIC CODE =================
async def new_public_code(prefix):
    from services.transactions import transaction, one
    if prefix not in ('AD','ZK'):
        raise ValueError('Noto‘g‘ri kod turi')
    table = 'ads' if prefix=='AD' else 'orders'
    async with transaction() as db:
        row = await one(db, 'SELECT value FROM code_sequences WHERE prefix=?', (prefix,))
        if row is None:
            maximum = await one(db, f"SELECT MAX(CAST(substr(public_code,4) AS INTEGER)) AS n FROM {table}")
            value = max(9999, maximum['n'] or 0) + 1
            await db.execute('INSERT INTO code_sequences VALUES (?,?)',(prefix,str(value)))
        else:
            value = int(row['value']) + 1
            await db.execute('UPDATE code_sequences SET value=? WHERE prefix=?',(str(value),prefix))
        return f'{prefix}-{value}'


# ================= USERS =================
async def get_or_create_user(telegram_id, username=None, first_name=None,
                              last_name=None, referred_by=None):
    db = await get_db()
    await db.execute(
        "INSERT INTO users (telegram_id,username,first_name,last_name,referred_by,created_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(telegram_id) DO NOTHING",
        (telegram_id, username, first_name, last_name, referred_by, _now()))
    if first_name is not None:
        await db.execute("UPDATE users SET username=?,first_name=?,last_name=?,updated_at=? WHERE telegram_id=?",
                         (username,first_name,last_name,_now(),telegram_id))
    return await get_user(telegram_id)


async def get_user(telegram_id):
    db = await get_db()
    cur = await db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    return row_to_dict(await cur.fetchone())


async def update_user(telegram_id, **fields):
    if not fields:
        return
    fields["updated_at"] = _now()
    keys = ", ".join(f"{k} = ?" for k in fields)
    db = await get_db()
    await db.execute(f"UPDATE users SET {keys} WHERE telegram_id = ?",
                      list(fields.values()) + [telegram_id])
    await db.commit()


async def add_balance(telegram_id, amount):
    db = await get_db()
    await db.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?",
                      (amount, telegram_id))
    await db.commit()


async def get_balance(telegram_id) -> int:
    user = await get_user(telegram_id)
    return int(user["balance"]) if user else 0


async def list_users(limit=100, offset=0):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM users ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
    return [row_to_dict(r) for r in await cur.fetchall()]


async def count_users():
    db = await get_db()
    cur = await db.execute("SELECT COUNT(*) FROM users")
    return (await cur.fetchone())[0]


async def search_users(term):
    db = await get_db()
    like = f"%{term}%"
    cur = await db.execute(
        "SELECT * FROM users WHERE CAST(telegram_id AS TEXT) LIKE ? "
        "OR username LIKE ? OR first_name LIKE ? LIMIT 20",
        (like, like, like))
    return [row_to_dict(r) for r in await cur.fetchall()]


async def set_blocked(telegram_id, blocked: bool):
    db = await get_db()
    await db.execute("UPDATE users SET is_blocked = ? WHERE telegram_id = ?",
                      (1 if blocked else 0, telegram_id))
    await db.commit()


async def is_user_blocked(telegram_id) -> bool:
    user = await get_user(telegram_id)
    return bool(user and user.get("is_blocked"))


# ---- adminlik: .env dagi ro'yxat + admin panelda qo'shilganlar ----
async def list_admin_ids():
    ids = set(config.ADMIN_IDS)
    raw = await get_setting("admins", "")
    for part in (raw or "").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return sorted(ids)


async def is_admin(telegram_id) -> bool:
    if telegram_id in config.ADMIN_IDS:
        return True
    return telegram_id in await list_admin_ids()


async def add_admin(telegram_id, added_by=None):
    ids = set(await list_admin_ids())
    ids.add(telegram_id)
    dynamic = ids - set(config.ADMIN_IDS)
    await set_setting("admins", ",".join(str(i) for i in sorted(dynamic)), added_by)


async def remove_admin(telegram_id, removed_by=None):
    if telegram_id in config.ADMIN_IDS:
        return False  # .env orqali qo'shilgan adminni bazadan olib bo'lmaydi
    ids = set(await list_admin_ids())
    ids.discard(telegram_id)
    dynamic = ids - set(config.ADMIN_IDS)
    await set_setting("admins", ",".join(str(i) for i in sorted(dynamic)), removed_by)
    return True


# ================= SETTINGS =================
async def get_setting(key, default=None):
    db = await get_db()
    cur = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = await cur.fetchone()
    return row[0] if row else default


async def get_int_setting(key, default=0):
    try:
        return int(await get_setting(key, default))
    except (TypeError, ValueError):
        return default


async def set_setting(key, value, updated_by=None):
    db = await get_db()
    await db.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (key, value, _now(), updated_by))
    await db.commit()


async def get_all_settings():
    db = await get_db()
    cur = await db.execute("SELECT key, value FROM settings ORDER BY key")
    return {r["key"]: r["value"] for r in await cur.fetchall()}


async def set_sign_text(value: str):
    await set_setting("sign_text", value.strip())


# ================= ADS ("E'lon") =================
async def create_ad(user_id, message_type, telegram_file_id, caption, text,
                     status="DRAFT"):
    public_code = await new_public_code("AD")
    db = await get_db()
    await db.execute(
        "INSERT INTO ads (public_code, user_id, message_type, telegram_file_id, "
        "caption, text, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (public_code, user_id, message_type, telegram_file_id, caption, text,
         status, _now()))
    await db.commit()
    cur = await db.execute("SELECT id FROM ads WHERE public_code = ?", (public_code,))
    ad_id = (await cur.fetchone())[0]
    return ad_id, public_code


async def get_ad(ad_id):
    db = await get_db()
    cur = await db.execute("SELECT * FROM ads WHERE id = ?", (ad_id,))
    return row_to_dict(await cur.fetchone())


async def get_ad_by_code(code):
    db = await get_db()
    cur = await db.execute("SELECT * FROM ads WHERE public_code = ?", (code,))
    return row_to_dict(await cur.fetchone())


async def update_ad(ad_id, **fields):
    if not fields:
        return
    keys = ", ".join(f"{k} = ?" for k in fields)
    db = await get_db()
    await db.execute(f"UPDATE ads SET {keys} WHERE id = ?",
                      list(fields.values()) + [ad_id])
    await db.commit()


async def cas_update_status(table, item_id, expected_status, **fields):
    """Atomik compare-and-swap: yozuv hozir aynan `expected_status`da
    bo'lsagina yangilaydi (bitta SQL UPDATE ichida tekshirish+yozish).
    Bu ikkita tugmani tez-tez bosish yoki tarmoq kechikishi tufayli
    yuzaga keladigan race condition'larni (bir xil to'lovni ikki marta
    qayta ishlash) oldini oladi. Qaytaradi: yangilangan qatorlar soni
    (0 — allaqachon boshqa holatga o'tgan, 1 — muvaffaqiyatli)."""
    if not fields:
        return 0
    keys = ", ".join(f"{k} = ?" for k in fields)
    db = await get_db()
    cur = await db.execute(
        f"UPDATE {table} SET {keys} WHERE id = ? AND status = ?",
        list(fields.values()) + [item_id, expected_status])
    await db.commit()
    return cur.rowcount


async def expire_stale_pending(hours=24):
    """To'lov qilinmay/chek yubormay tashlab ketilgan (WAITING_PAYMENT /
    WAITING_RECEIPT holatidagi) yozuvlarni belgilangan soatdan keyin
    avtomatik CANCELLED qiladi — aks holda ular abadiy \"faol\" bo'lib,
    foydalanuvchining kvotasini band qilib turadi."""
    db = await get_db()
    cutoff = f"-{int(hours)} hours"
    for table in ("ads", "orders"):
        await db.execute(
            f"UPDATE {table} SET status = 'DRAFT' "
            f"WHERE status IN ('WAITING_PAYMENT','WAITING_RECEIPT') "
            f"AND datetime(created_at) < datetime('now', ?)", (cutoff,))
    await db.commit()


async def soft_delete_ad(ad_id):
    await update_ad(ad_id, deleted_at=_now(), status="DELETED")


async def list_ads_by_user(user_id):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM ads WHERE user_id = ? AND deleted_at IS NULL ORDER BY id DESC",
        (user_id,))
    return [row_to_dict(r) for r in await cur.fetchall()]


async def count_user_active_ads(user_id):
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) FROM ads WHERE user_id = ? AND deleted_at IS NULL "
        "AND status NOT IN ('DRAFT','DELETED','REJECTED','COMPLETED','CANCELLED')",
        (user_id,))
    return (await cur.fetchone())[0]


# ================= ORDERS ("Zakaz") =================
async def create_order(user_id, message_type, telegram_file_id, caption, text,
                        status="DRAFT"):
    public_code = await new_public_code("ZK")
    db = await get_db()
    await db.execute(
        "INSERT INTO orders (public_code, user_id, message_type, telegram_file_id, "
        "caption, text, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (public_code, user_id, message_type, telegram_file_id, caption, text,
         status, _now()))
    await db.commit()
    cur = await db.execute("SELECT id FROM orders WHERE public_code = ?", (public_code,))
    order_id = (await cur.fetchone())[0]
    return order_id, public_code


async def get_order(order_id):
    db = await get_db()
    cur = await db.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    return row_to_dict(await cur.fetchone())


async def get_order_by_code(code):
    db = await get_db()
    cur = await db.execute("SELECT * FROM orders WHERE public_code = ?", (code,))
    return row_to_dict(await cur.fetchone())


async def update_order(order_id, **fields):
    if not fields:
        return
    keys = ", ".join(f"{k} = ?" for k in fields)
    db = await get_db()
    await db.execute(f"UPDATE orders SET {keys} WHERE id = ?",
                      list(fields.values()) + [order_id])
    await db.commit()


async def list_ads_admin(limit=10, offset=0):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM ads WHERE deleted_at IS NULL ORDER BY id DESC "
        "LIMIT ? OFFSET ?", (limit, offset))
    return [row_to_dict(r) for r in await cur.fetchall()]


async def count_ads_admin():
    db = await get_db()
    cur = await db.execute("SELECT COUNT(*) FROM ads WHERE deleted_at IS NULL")
    return (await cur.fetchone())[0]


async def list_orders_admin(limit=10, offset=0):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM orders WHERE deleted_at IS NULL ORDER BY id DESC "
        "LIMIT ? OFFSET ?", (limit, offset))
    return [row_to_dict(r) for r in await cur.fetchall()]


async def count_orders_admin():
    db = await get_db()
    cur = await db.execute("SELECT COUNT(*) FROM orders WHERE deleted_at IS NULL")
    return (await cur.fetchone())[0]


async def soft_delete_order(order_id):
    await update_order(order_id, deleted_at=_now(), status="DELETED")


async def list_orders_by_user(user_id):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM orders WHERE user_id = ? AND deleted_at IS NULL "
        "ORDER BY id DESC", (user_id,))
    return [row_to_dict(r) for r in await cur.fetchall()]


async def count_user_active_orders(user_id):
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) FROM orders WHERE user_id = ? AND deleted_at IS NULL "
        "AND status NOT IN ('DRAFT','DELETED','REJECTED','COMPLETED','CANCELLED')",
        (user_id,))
    return (await cur.fetchone())[0]


# ================= PAYMENTS =================
async def create_payment(user_id, ptype, amount, ad_id=None, order_id=None,
                          status="WAITING_RECEIPT"):
    db = await get_db()
    cur = await db.execute(
        "INSERT INTO payments (user_id, type, ad_id, order_id, amount, status, "
        "created_at) VALUES (?,?,?,?,?,?,?)",
        (user_id, ptype, ad_id, order_id, amount, status, _now()))
    await db.commit()
    return cur.lastrowid


async def get_payment(payment_id):
    db = await get_db()
    cur = await db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,))
    return row_to_dict(await cur.fetchone())


async def update_payment(payment_id, **fields):
    if not fields:
        return
    keys = ", ".join(f"{k} = ?" for k in fields)
    db = await get_db()
    await db.execute(f"UPDATE payments SET {keys} WHERE id = ?",
                      list(fields.values()) + [payment_id])
    await db.commit()


async def list_payments_by_status(status, limit=50, offset=0):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM payments WHERE status = ? ORDER BY id ASC LIMIT ? OFFSET ?",
        (status, limit, offset))
    return [row_to_dict(r) for r in await cur.fetchall()]


async def count_pending_payments():
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) FROM payments WHERE status = 'WAITING_ADMIN'")
    return (await cur.fetchone())[0]


async def count_payments(status=None):
    db = await get_db()
    if status:
        cur = await db.execute("SELECT COUNT(*) FROM payments WHERE status = ?",
                                (status,))
    else:
        cur = await db.execute("SELECT COUNT(*) FROM payments")
    return (await cur.fetchone())[0]


async def find_approved_payment_by_receipt(receipt_file_id):
    """Chekni qayta ishlatishga urinishni aniqlash uchun."""
    if not receipt_file_id:
        return None
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM payments WHERE receipt_file_id = ? AND status = 'APPROVED' "
        "LIMIT 1", (receipt_file_id,))
    return row_to_dict(await cur.fetchone())


# ================= KONTAKT UNLOCK =================
async def create_unlock(order_id, developer_user_id, payment_id=None):
    db = await get_db()
    cur = await db.execute(
        "INSERT INTO contact_unlocks (order_id, developer_user_id, payment_id, "
        "created_at) VALUES (?,?,?,?)",
        (order_id, developer_user_id, payment_id, _now()))
    await db.commit()
    return cur.lastrowid


async def get_unlock(unlock_id):
    db = await get_db()
    cur = await db.execute("SELECT * FROM contact_unlocks WHERE id = ?", (unlock_id,))
    return row_to_dict(await cur.fetchone())


async def update_unlock(unlock_id, **fields):
    if not fields:
        return
    keys = ", ".join(f"{k} = ?" for k in fields)
    db = await get_db()
    await db.execute(f"UPDATE contact_unlocks SET {keys} WHERE id = ?",
                      list(fields.values()) + [unlock_id])
    await db.commit()


async def get_approved_unlock(order_id, developer_user_id):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM contact_unlocks WHERE order_id = ? AND developer_user_id = ? "
        "AND approved_at IS NOT NULL LIMIT 1", (order_id, developer_user_id))
    return row_to_dict(await cur.fetchone())


async def has_pending_unlock(order_id, developer_user_id):
    db = await get_db()
    cur = await db.execute(
        "SELECT u.* FROM contact_unlocks u JOIN payments p ON p.id=u.payment_id "
        "WHERE u.order_id = ? AND u.developer_user_id = ? "
        "AND u.approved_at IS NULL AND p.status IN ('WAITING_RECEIPT','WAITING_ADMIN') LIMIT 1", (order_id, developer_user_id))
    return row_to_dict(await cur.fetchone())


async def count_unlocks_for_order(order_id):
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) FROM contact_unlocks WHERE order_id = ? AND "
        "approved_at IS NOT NULL", (order_id,))
    return (await cur.fetchone())[0]


# ================= COMPLAINTS (Shikoyat) =================
async def create_complaint(user_id, target_code, reason):
    db = await get_db()
    cur = await db.execute(
        "INSERT INTO complaints (user_id, target_code, reason, status, created_at) "
        "VALUES (?,?,?,?,?)", (user_id, target_code, reason, "OPEN", _now()))
    await db.commit()
    return cur.lastrowid


async def get_complaint(complaint_id):
    db = await get_db()
    cur = await db.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,))
    return row_to_dict(await cur.fetchone())


async def update_complaint(complaint_id, **fields):
    if not fields:
        return
    keys = ", ".join(f"{k} = ?" for k in fields)
    db = await get_db()
    await db.execute(f"UPDATE complaints SET {keys} WHERE id = ?",
                      list(fields.values()) + [complaint_id])
    await db.commit()


async def list_complaints(status="OPEN", limit=50, offset=0):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM complaints WHERE status = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (status, limit, offset))
    return [row_to_dict(r) for r in await cur.fetchall()]


async def count_complaints(status=None):
    db = await get_db()
    if status:
        cur = await db.execute("SELECT COUNT(*) FROM complaints WHERE status = ?",
                                (status,))
    else:
        cur = await db.execute("SELECT COUNT(*) FROM complaints")
    return (await cur.fetchone())[0]


# ================= ADMIN ACTIONS (audit log) =================
async def log_admin_action(admin_id, action_type, target_type=None,
                            target_id=None, description=None):
    db = await get_db()
    await db.execute(
        "INSERT INTO admin_actions (admin_id, action_type, target_type, target_id, "
        "description, created_at) VALUES (?,?,?,?,?,?)",
        (admin_id, action_type, target_type, target_id, description, _now()))
    await db.commit()


async def list_admin_actions(limit=50):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM admin_actions ORDER BY id DESC LIMIT ?", (limit,))
    return [row_to_dict(r) for r in await cur.fetchall()]


# ================= REFERAL / STATISTIKA =================
async def get_referral_count(telegram_id):
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) FROM users WHERE referred_by = ?", (telegram_id,))
    return (await cur.fetchone())[0]


async def list_referrals(telegram_id, limit=50):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM users WHERE referred_by = ? ORDER BY id DESC LIMIT ?",
        (telegram_id, limit))
    return [row_to_dict(r) for r in await cur.fetchall()]


async def get_general_stats():
    db = await get_db()
    stats = {}
    stats["users"] = await count_users()
    cur = await db.execute("SELECT COUNT(*) FROM ads WHERE deleted_at IS NULL")
    stats["ads"] = (await cur.fetchone())[0]
    cur = await db.execute("SELECT COUNT(*) FROM ads WHERE status='PUBLISHED'")
    stats["ads_published"] = (await cur.fetchone())[0]
    cur = await db.execute("SELECT COUNT(*) FROM orders WHERE deleted_at IS NULL")
    stats["orders"] = (await cur.fetchone())[0]
    cur = await db.execute("SELECT COUNT(*) FROM orders WHERE status='PUBLISHED'")
    stats["orders_published"] = (await cur.fetchone())[0]
    cur = await db.execute(
        "SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='APPROVED' AND payment_source='CARD'")
    stats["total_revenue"] = (await cur.fetchone())[0]
    stats["complaints_open"] = await count_complaints("OPEN")
    return stats
