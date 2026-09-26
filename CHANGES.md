# 4.3 — Sozlanadigan do‘st taklif qilish sharti

- Dastlab o‘chiq, admin belgilaydigan son (1–10000) va talab rejimi.
- Bir martalik doimiy ruxsat yoki har post uchun unikal takliflar hisobi.
- Pullik tizim o‘chirilganda barcha foydalanuvchi e’lon/zakazlarida backend tekshiruvi.
- Shaxsiy kanal linki, progress va ayni qoralamani davom ettirish tugmasi.
- Sarflash va admin navbatiga yuborish bitta tranzaksiyada; bir taklif ikki marta sarflanmaydi.
- 115 offline test muvaffaqiyatli.

# 4.2 — Admin chatidagi backup va restore

- Admin panelida Backup yaratish va Backupni tiklash tugmalari.
- Izchil SQLite/WAL snapshot ZIP fayl sifatida shaxsiy chatga yuboriladi.
- Yuklangan ZIP formati, bot ID, checksum, hajm va SQLite yaxlitligi tekshiriladi.
- Tiklashdan oldin sana/sonlar, tasdiqlash va joriy bazaning alohida backupi.
- Baza almashtirilayotganda update va fon vazifalari navbatda kutadi.
- Migratsiya xatosida oldingi bazaga rollback; eskirgan tasdiqlash bekor qilinadi.
- Faqat Telegram/server orqali ishlaydi; tashqi backup integratsiyasi yo‘q.

# Qulay Savdo Bot — nom yangilanishi

- Bot salomlashuvi, standart kanal imzosi va qo‘llanmalar yangilandi.
- Startup Telegram ko‘rinadigan nomini Qulay Savdo Bot qiladi; username o‘zgarmaydi.
- Eski standart matnlar migratsiya qilinadi, admin yozgan maxsus matn saqlanadi.


# 2026-09-26 — Zakazlar 4.0

- Narxni oldindan ko‘rsatish va yangi o‘zbekcha matnlar.
- Kategoriya, preview, tahrirlash, saqlanadigan qoralamalar va sahifalangan ro‘yxatlar.
- Barcha postlarda admin moderatsiyasi; balans to‘lovi rad etilsa atomik refund.
- Standart tekshirish 60 daqiqa; admin 1–10080 daqiqa belgilaydi.
- Har tekshiruvga muddat snapshot, Toshkent vaqti, holat tugmasi, kechikish eslatmasi.
- Ochiq zakazlar, kategoriya filtrlari va ixtiyoriy mos zakaz xabarlari.
- Profil/bio/portfolio, ishlar soni, haqiqiy ishtirokchilarning moderatsiyali fikrlari.
- Ikki tomon rozilik bergan yakunlangan zakazlar ro‘yxati; rozilikni qaytarib olish.
- Takliflarni solishtirish; qayta ochishda eski rad etilgan taklifni alohida qayta ko‘rib chiqish.
- Yordam so‘rovi, admin javobi va bot chatiga yetkazish.
- Karta tushumi, balans sarfi, bepul postlar va foydalanuvchi bosqichlari analitikasi.
- Qoralama to‘lovini davom ettirganda eski narx saqlanadi.
- Referalda balans yetmasa hech narsa ayrilmaydi — avvalgi foydalanuvchi talabi saqlangan.
- 92 ta oflayn avtomatik test. Production deploy bajarilmagan.
