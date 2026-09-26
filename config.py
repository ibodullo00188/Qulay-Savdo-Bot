import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/bot.db").strip()
if not os.path.isabs(DATABASE_PATH):
    DATABASE_PATH = str(BASE_DIR / DATABASE_PATH)


def _parse_admin_ids(raw: str):
    out = set()
    for x in raw.split(","):
        x = x.strip()
        if x.lstrip("-").isdigit():
            out.add(int(x))
    return out


# .env dagi ADMIN_IDS har doim admin bo'lib qoladi (bazadan o'chirib bo'lmaydi)
INITIAL_ADMINS = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))
ADMIN_IDS = sorted(INITIAL_ADMINS)

BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")


def _parse_channel_id(raw: str) -> int:
    raw = raw.strip()
    # Telegram kanal/guruh ID'lari manfiy bo'lishi mumkin (masalan -1001234567890),
    # shuning uchun oddiy .isdigit() ishlatib bo'lmaydi.
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


CHANNEL_ID = _parse_channel_id(os.getenv("CHANNEL_ID", "0"))


# ================= RENDER / WEBHOOK SOZLAMALARI =================
# Render "Web Service" bepul tarifi doimiy ishlab turuvchi fon jarayonini
# (worker) qo'llab-quvvatlamaydi — u faqat HTTP so'rovlarga javob beruvchi
# xizmatlarni bepul beradi va 15 daqiqa harakatsizlikdan keyin "uxlab
# qoladi". Shu sababli botni ikkita rejimda ishlata olishi kerak:
#   - polling  -> lokal kompyuterda ishlab chiqish uchun
#   - webhook  -> Render'da HTTP orqali Telegram xabarlarini qabul qilish
#
# RUN_MODE=webhook bo'lganda WEBHOOK_HOST albatta to'ldirilishi shart
# (masalan: https://qulay-savdo-bot.onrender.com — oxirida "/" bo'lmasin).
RUN_MODE = os.getenv("RUN_MODE", "polling").strip().lower()

PORT = int(os.getenv("PORT", "10000"))

WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "").strip().rstrip("/")
if not WEBHOOK_HOST:
    # Render "Web Service"lar uchun bu o'zgaruvchini avtomatik beradi —
    # WEBHOOK_HOST'ni qo'lda kiritish shart emas.
    WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}" if WEBHOOK_HOST else ""

# Telegram har bir so'rovda shu sarlavhani yuboradi — soxta so'rovlarni
# (webhook manzilini bilib olgan har kim emas, faqat Telegram) filtrlaydi.
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

BOT_DISPLAY_NAME = "Qulay Savdo Bot"
