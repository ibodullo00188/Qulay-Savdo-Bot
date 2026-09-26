"""Rename only known shipped branding; preserve administrator-written copy."""
OLD_START_TEXTS = ["🚀 Tezda Sotdim — raqamli mahsulotlar bozori!\n\n📢 Tayyor dasturingizni e'lon qiling yoki 🔵 kerakli dasturga zakaz bering.\n\nPastdagi menyudan bo'limni tanlang:", 'TEZDA SOTDIM\nRaqamli mahsulotlar va dasturiy buyurtmalar\n\n📢 Tayyor mahsulotingizni e’lon qiling.\n🔵 Zakaz joylashtiring va kelgan takliflardan bajaruvchi tanlang.\n🔎 Zakaz kodini yuboring — so‘rov qoldirish bepul.\n\nKerakli bo‘limni tanlang:', '👋 Tezda Sotdim’ga xush kelibsiz!\n\nTayyor bot, sayt yoki ilovangizni sotuvga qo‘ying. Yangi dastur kerak bo‘lsa, zakaz joylashtirib, kelgan takliflardan bajaruvchi tanlang.\n\n👨\u200d💻 Dasturchimisiz? Ochiq zakazlarni ko‘ring — narx va muddat taklif qilish bepul.\n\n👇 Nima qilmoqchisiz?']
OLD_SIGN = '🤖 @tezda_sotdim_bot — siz izlayotgan tayyor botlar sotuvda!'

async def migrate_branding(db):
    from services.experience import DEFAULTS
    from database.db import DEFAULT_SETTINGS
    for text in OLD_START_TEXTS:
        await db.execute("UPDATE settings SET value=? WHERE key='start_text' AND value=?", (DEFAULTS['start_text'], text))
    await db.execute("UPDATE settings SET value=? WHERE key='sign_text' AND value=?", (DEFAULT_SETTINGS['sign_text'], OLD_SIGN))
