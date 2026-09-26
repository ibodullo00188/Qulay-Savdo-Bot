# Qulay Savdo Bot — Render Blueprint va Telegram backup

## Render’da ishga tushirish

1. Repozitoriy ildizida `render.yaml`, `main.py` va `requirements.txt` bo‘lsin.
2. Render → **New → Blueprint** → `ibodullo00188/Qulay-Savdo-Bot` repozitoriysini tanlang.
3. Quyidagi uchta qiymatni kiriting:

| Maydon | Qiymat |
| --- | --- |
| `BOT_TOKEN` | BotFather bergan bot tokeni |
| `ADMIN_IDS` | O‘zingizning Telegram raqamli ID’ingiz; bir nechta bo‘lsa vergul bilan |
| `CHANNEL_ID` | Kanalning raqamli ID’i, odatda `-100...` |

4. **Deploy Blueprint** bosing. Free Web Service, Python va webhook sozlamalari tayyor.
5. Botni kanalga administrator qiling; yozish, tahrirlash, o‘chirish va taklif havolalari
   huquqlarini bering. Botga `/start`, keyin `/admin` yuboring.
6. Admin panel → Sozlamalar orqali karta raqami va egasini kiriting. Shaxsiy
   rekvizitlar ochiq repozitoriyga kiritilmagan; ular bazada saqlanadi.

Domen Render tomonidan avtomatik olinadi. Webhook secret avtomatik yaratiladi.
Google hisob, OAuth, tashqi xotira yoki backup kaliti talab qilinmaydi.
Bot ishga tushganda Telegram’dagi ko‘rinadigan nomi **Qulay Savdo Bot** bo‘ladi;
username BotFather’dagi amaldagi username bo‘lib qoladi.

## Backup yaratish

1. Botning shaxsiy chatida **Admin panel → 📦 Backup yaratish** tugmasini bosing.
2. Serverdagi ishlayotgan bazadan izchil nusxa olinadi. SQLite WAL yozuvlari ham kiradi.
3. Bot `qulay-savdo-backup-SANA-VAQT.zip` faylini shu chatga yuboradi.
4. Faylni yuklab yoki Telegram Saved Messages’da saqlab qo‘ying.

Backupda foydalanuvchi, balans, to‘lov, bonus, zakaz, e’lon, qoralama, sozlama,
admin yozuvlari, murojaatlar va qolgan baza jadvallari bor. Rasm va cheklarning
Telegram fayl ID’lari kiradi; rasmlarning o‘zi ko‘chirilmaydi.
Serverning kodi va `.env` fayli backupga kiritilmaydi. Tokenlar Render Environment’da qoladi.
Backup bazadagi shaxsiy ma’lumotlarni saqlaydi; uni faqat ishonchli administratorlarga bering.

## Backupni tiklash

1. **Admin panel → ♻️ Backupni tiklash** tugmasini bosing.
2. Shu bot yaratgan ZIP faylini **hujjat/fayl** sifatida yuboring.
3. Bot format, bot ID, fayl checksum’i, SQLite yaxlitligi va kerakli jadvallarni tekshiradi.
4. Sana (Toshkent vaqti) va foydalanuvchi/zakaz/e’lon/to‘lov sonlarini ko‘rsatadi.
5. **✅ Backupni tiklash** tugmasi bilan tasdiqlang. Tasdiq 10 daqiqa amal qiladi.
6. Bot avval **tiklashdan oldingi joriy bazani** alohida backup qilib sizga yuboradi.
   Bu fayl yuborilmasa, tiklash boshlanmaydi.
7. Boshqa amallar va fon vazifalari navbatda kutadi. Baza ulanishi yopiladi,
   tekshirilgan nusxa atomik almashtiriladi, migratsiyalar bajariladi va bot davom etadi.
8. Yakuniy muvaffaqiyat xabaridan so‘ng balans va zakazlarni tekshiring.

Nosoz yoki boshqa botga tegishli fayl joriy bazani almashtirmaydi. O‘rnatish yoki
migratsiya xatosida joriy bazani qaytarish bajariladi. Serverning majburiy o‘chishi
kabi tashqi nosozliklarda chatga yuborilgan backup bilan qayta tiklang.

Eski backupga qaytish undan keyingi bazadagi o‘zgarishlarni qaytaradi.
Telegram kanali va allaqachon yuborilgan xabarlar orqaga qaytmaydi. Shu sababli
restore’dan keyin noaniq/tayyor kanal nashrlari admin tekshiruviga o‘tadi,
eski kanal tahriri navbati va yuborilmagan eski bildirishnomalar avtomatik
qayta bajarilmaydi. Foydalanuvchi qoralamalari saqlanadi; eski restore-dialog holatlari tozalanadi.

Bir vaqtning o‘zida faqat bitta bot jarayonini ishlating. Navbatlashtirish bitta
jarayon ichida ishlaydi; ikkita server nusxasi uchun taqsimlangan bloklash yo‘q.

## Render Free’da baza o‘chsa

Free disk vaqtinchalik: restart, redeploy yoki servis uyquga ketganda lokal
baza yo‘qolishi mumkin. Bot yangi baza bilan ochiladi. `ADMIN_IDS` dagi admin
`/admin` orqali oldingi ZIP faylini tiklaydi. Shu sababli admin ID’ni
faqat bot paneliga emas, Render Environment’dagi `ADMIN_IDS` ga ham kiriting.

**Avtomatik tashqi backup va avtomatik tiklash yo‘q.** Oxirgi qo‘lda yaratilgan
backupdan keyingi yozuvlar server bilan birga yo‘qolishi mumkin. Muhim ishlar va
har bir yangilashdan oldin backup yarating. Doimiy saqlash kerak bo‘lsa,
keyinchalik doimiy diskli tarifga o‘tish mumkin.

Free servis 15 daqiqa faoliyatsizlikdan keyin uxlaydi; uyg‘onishda kechikish bor.
Uyquda soatlik eslatmalar ishlamaydi; bot uyg‘ongach davom etadi.
[Render Free rasmiy cheklovlari](https://render.com/docs/free).

## Hajm va buyruqlar

- ZIP fayl: ko‘pi bilan **19 MiB**; ichidagi SQLite: **128 MiB** gacha.
- Bu cheklov Telegram botining standart fayl yuklab olish limiti va xotira sarfi uchun.
- `/backup` — “Backup yaratish” bilan bir xil.
- `/restore_backup` — “Backupni tiklash” bilan bir xil.
- `/cancel` — tiklashni bekor qilish.
- Faqat shaxsiy chatdagi admin bu amallardan foydalanadi.

## Birinchi tekshiruv

Test qoralama yarating → backup oling → boshqa test o‘zgarishini kiriting →
eski ZIP’ni tiklang → qoralama va balans eski holatga qaytganini tekshiring.
Keyin yangi backup oling, Render’ni restart qilib, shu fayl bilan qayta tiklashni sinang.
Jonli Telegram/Render tekshiruvi haqiqiy token bilan deploydan keyin bajariladi.
