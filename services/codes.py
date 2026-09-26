"""Deep-link (t.me/bot?start=...) payload kodlash/dekodlash.

Faqat referal havolalari uchun ishlatiladi: payload — taklif qilgan
foydalanuvchining telegram_id raqami.
"""


def encode_referral(telegram_id: int) -> str:
    return str(telegram_id)


def decode_referral(payload: str):
    payload = (payload or "").strip()
    if payload.isdigit() and len(payload) <= 15:
        return int(payload)
    return None
