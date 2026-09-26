"""Bitta jarayon (single-process) ichida item bo'yicha navbatlashtirish.

contact_unlocks oqimida (order o'zi PUBLISHED holatida qolaveradi) CAS
qilinadigan bitta "status" ustuni yo'q, shuning uchun tugmani tez-tez
bosish orqali bir xil to'lovni ikki marta amalga oshirishning oldini
olish uchun kalit (masalan order_id+developer_id) bo'yicha asyncio.Lock
ishlatiladi. Polling va webhook rejimlarining ikkalasi ham bitta Python
jarayonida, bitta event loop'da ishlaydi, shuning uchun bu yetarli."""
import asyncio
from collections import defaultdict

_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def get_lock(key: str) -> asyncio.Lock:
    return _locks[key]
