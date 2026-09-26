import asyncio
import aiosqlite

_connection_lock = asyncio.Lock()

from config import DATABASE_PATH

# MUHIM: barcha jadvallarda user_id maydoni users.telegram_id ga ishora qiladi
# (users.id emas) — bu handlerlar bilan mos kelishi va chalkashlikni oldini
# olish uchun ataylab shunday tanlangan.
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    balance INTEGER NOT NULL DEFAULT 0,
    referred_by INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT,
    is_blocked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_code TEXT UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    message_type TEXT NOT NULL,
    telegram_file_id TEXT,
    caption TEXT,
    text TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    channel_message_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    published_at TEXT,
    completed_at TEXT,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_code TEXT UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    message_type TEXT NOT NULL,
    telegram_file_id TEXT,
    caption TEXT,
    text TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    channel_message_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    published_at TEXT,
    completed_at TEXT,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    type TEXT NOT NULL,            -- AD_PUBLICATION | ORDER_PUBLICATION | CONTACT_UNLOCK
    ad_id INTEGER,
    order_id INTEGER,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'UZS',
    receipt_file_id TEXT,
    status TEXT NOT NULL DEFAULT 'WAITING_RECEIPT',
    reject_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    approved_at TEXT,
    approved_by INTEGER
);

CREATE TABLE IF NOT EXISTS contact_unlocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    developer_user_id INTEGER NOT NULL,
    payment_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    approved_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT,
    updated_by INTEGER
);

CREATE TABLE IF NOT EXISTS complaints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    target_code TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS admin_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    action_type TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DEFAULT_SETTINGS = {
    "paid_enabled": "1",
    "ad_price": "34990",
    "order_price": "34990",
    "contact_price": "25000",
    "card_number": "",
    "card_holder": "",
    "admins": "",
    "image_required": "1",
    "image_format": "16:9, hajmi 20MB gacha",
    "max_active_ads": "5",
    "max_active_orders": "5",
    "referral_enabled": "1",
    "referral_bonus": "5000",
    "start_text": "🚀 Qulay Savdo Bot — raqamli mahsulotlar bozori!\n\n📢 Tayyor dasturingizni e'lon qiling yoki 🔵 kerakli dasturga zakaz bering.\n\nPastdagi menyudan bo'limni tanlang:",
    "ad_guide": "📢 E'lon berish\n\n📝 Dasturingiz haqida yozilgan matn va rasmni bitta xabarda yuboring.\n\n📐 Rasm 16:9 formatda bo'lishi tavsiya etiladi.\n\n👇 E'loningizni yuboring.",
    "order_guide": "🔵 Dasturga zakaz berish\n\nSizga kerak bo'lgan bot, WebApp, sayt, mobil ilova yoki boshqa dastur haqida xabaringizni yuboring.\n\n📝 Xabaringizni o'zingiz xohlagan tarzda yozishingiz mumkin.\n\n📌 Qancha batafsil yozsangiz, dasturchilar uchun shuncha tushunarli bo'ladi.",
    "ad_guarantee": "❓ Botim sotilishiga kafolat bormi?\n\nE'lon joylashtirilishi — botingiz albatta sotiladi degan kafolatni anglatmaydi.\n\nBizning xizmatimiz botingizni kanal auditoriyasiga e'lon sifatida taqdim etish va potensial xaridorlarga yetkazib berishdan iborat.\n\nXaridorning qaroriga botning narxi, sifati, funksiyalari, talabi va boshqa omillar ta'sir qilishi mumkin.",
    "order_guarantee": "❓ Zakaz bajarilishiga kafolat bormi?\n\nZakaz joylashtirilishi — albatta bajariladi degan kafolatni anglatmaydi.\n\nBizning xizmatimiz talabingizni dasturchilar auditoriyasiga yetkazib berishdan iborat.\n\nSiz va dasturchi o'rtasida narx, shart va boshqa omillar kelishiladi.",
    "sign_text": "🤖 Qulay Savdo Bot — siz izlayotgan tayyor botlar sotuvda!",
    "rules_text": "ℹ️ Qoidalar\n\n• Bot faqat raqamli mahsulotlar (bot, webapp, sayt, dastur) e'loni uchun.\n• E'lon va zakaz kontenti qonunga zid bo'lmasligi kerak.\n• Firibgarlik va aldash qat'iyan man etiladi.\n• Kanal orqali shikoyat qilish mumkin.\n• Platforma sotuv/bajarilish uchun kafolat bermaydi — bu aloqa o'rnatuvchi vosita.",
}

# Replace only shipped defaults; administrators' custom wording is retained.
LEGACY_COPY = {key: DEFAULT_SETTINGS[key] for key in ('start_text','order_guide')}
DEFAULT_SETTINGS.update({
    'start_text': "QULAY SAVDO BOT\nRaqamli mahsulotlar va dasturiy buyurtmalar\n\n📢 Tayyor mahsulotingizni e’lon qiling.\n🔵 Zakaz joylashtiring va kelgan takliflardan bajaruvchi tanlang.\n🔎 Zakaz kodini yuboring — so‘rov qoldirish bepul.\n\nKerakli bo‘limni tanlang:",
    'order_guide': "YANGI ZAKAZ\n\nQanday dastur, bot yoki sayt kerakligini yozing. Funksiyalar, taxminiy budjet va istalgan muddatni ko‘rsatishingiz mumkin.\n\n1. Topshiriqni yuboring.\n2. Joylashtirish to‘lovini qilib, chekni yuboring.\n3. Admin tasdiqlagach, zakaz kanalga chiqadi.\n\nNomzodlardan bepul takliflar keladi. Ular bilan shaxsiy chatda gaplashib, bitta bajaruvchini tanlaysiz.\n\nTopshiriq matnini yoki rasm va izohni bitta xabarda yuboring."
})

_conn = None


async def get_db():
    global _conn
    async with _connection_lock:
        if _conn is None:
            from pathlib import Path
            Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
            _conn = await aiosqlite.connect(DATABASE_PATH, isolation_level=None, timeout=30)
            _conn.row_factory = aiosqlite.Row
            await _conn.execute("PRAGMA journal_mode = WAL")
            await _conn.execute("PRAGMA foreign_keys = ON")
        return _conn


async def _add_column_if_missing(db, table, column, ddl):
    """SQLite'da 'ALTER TABLE ... ADD COLUMN IF NOT EXISTS' yo'q, shuning
    uchun ustun mavjudligini tekshirib, yo'q bo'lsagina qo'shamiz. Eski
    bazalarni buzmasdan yangi ustunlarni migratsiya qilish uchun."""
    cur = await db.execute(f"PRAGMA table_info({table})")
    cols = {r[1] for r in await cur.fetchall()}
    if column not in cols:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


async def init_db():
    db = await get_db()
    await db.executescript(SCHEMA)

    from services.upgrade import migrate
    await migrate(db)
    # ---- migratsiyalar (eski bazalarga yangi ustunlarni qo'shish) ----
    await _add_column_if_missing(
        db, "users", "referral_bonus_paid",
        "referral_bonus_paid INTEGER NOT NULL DEFAULT 0")
    await _add_column_if_missing(db, "payments", "receipt_unique_id", "receipt_unique_id TEXT")
    await db.execute("CREATE INDEX IF NOT EXISTS payments_status_idx ON payments(status)")
    await db.execute("CREATE INDEX IF NOT EXISTS unlock_pair_idx ON contact_unlocks(order_id, developer_user_id)")
    await db.execute("""CREATE TABLE IF NOT EXISTS fsm_state (
        key TEXT PRIMARY KEY, state TEXT, data TEXT NOT NULL DEFAULT '{}'
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        text TEXT NOT NULL, sent_at TEXT
    )""")
    for column, ddl in (
        ("assigned_to", "assigned_to INTEGER"),
        ("selected_application_id", "selected_application_id INTEGER"),
        ("assigned_at", "assigned_at TEXT"),
        ("channel_sync_pending", "channel_sync_pending INTEGER NOT NULL DEFAULT 0"),
        ("channel_body", "channel_body TEXT"),
    ):
        await _add_column_if_missing(db,"orders",column,ddl)
    await _add_column_if_missing(db,"notifications","event_type","event_type TEXT")
    await _add_column_if_missing(db,"notifications","entity_id","entity_id INTEGER")
    await db.execute("""CREATE TABLE IF NOT EXISTS order_applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL REFERENCES orders(id),
        applicant_id INTEGER NOT NULL REFERENCES users(telegram_id),
        username TEXT NOT NULL,
        price TEXT NOT NULL,
        deadline TEXT NOT NULL,
        proposal TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        created_at TEXT NOT NULL,
        decided_at TEXT,
        UNIQUE(order_id,applicant_id)
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS application_order_idx ON order_applications(order_id,status)")
    await db.execute("CREATE INDEX IF NOT EXISTS application_user_idx ON order_applications(applicant_id,id)")
    cur = await db.execute("SELECT value FROM settings WHERE key='channel_format_v3'")
    if not await cur.fetchone():
        await db.execute("UPDATE orders SET channel_sync_pending=1 WHERE channel_message_id IS NOT NULL AND status IN ('PUBLISHED','ASSIGNED','COMPLETED','DELETED','REJECTED')")
        await db.execute("INSERT INTO settings(key,value) VALUES ('channel_format_v3','1')")
    await db.commit()

    cur = await db.execute("SELECT COUNT(*) FROM settings")
    if (await cur.fetchone())[0] == 0:
        await db.executemany(
            "INSERT INTO settings(key, value) VALUES (?, ?)",
            list(DEFAULT_SETTINGS.items()),
        )
    else:
        # yangi qo'shilgan sozlamalarni mavjud bazaga to'ldirish
        cur = await db.execute("SELECT key FROM settings")
        existing = {r[0] for r in await cur.fetchall()}
        missing = [(k, v) for k, v in DEFAULT_SETTINGS.items() if k not in existing]
        if missing:
            await db.executemany(
                "INSERT INTO settings(key, value) VALUES (?, ?)", missing)
    for key, old_value in LEGACY_COPY.items():
        await db.execute("UPDATE settings SET value=? WHERE key=? AND value=?",
                         (DEFAULT_SETTINGS[key],key,old_value))
    await db.commit()

    from services.experience import migrate
    await migrate(db)
    from services.branding import migrate_branding
    await migrate_branding(db)
    from services.invite_gate import migrate as migrate_invite_gate
    await migrate_invite_gate(db)


async def close_db():
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None
