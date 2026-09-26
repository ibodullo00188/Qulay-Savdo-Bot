# Qulay Savdo Bot · Zakazlar 4.3

Python 3.12 / aiogram 3 / SQLite. Pullik rejimda zakaz egasi joylashtirish uchun to'laydi,
bajaruvchilar esa bepul taklif yuboradi. Suhbat Telegramdagi shaxsiy chatda bo'ladi.

## Foydalanuvchi jarayoni

1. **Narx oldindan:** e’lon yoki zakazni boshlaganda narx, xizmat tarkibi va tekshirish muddati ko‘rsatiladi.
2. **Qoralama:** kategoriya va matn/rasm → oldindan ko‘rish → tahrirlash → davom etish. Menyudan chiqish matnni o‘chirmaydi.
3. **Moderatsiya:** karta, bonus balansidan va bepul yuborilgan barcha postlar admin tekshiruvidan o‘tadi. Rad etilgan bonus to‘lovi bir marta balansga qaytariladi.
4. **Chek muddati:** standart 60 daqiqa. Admin panel → Sozlamalar → Chek tekshirish muddati (daqiqada). 1–10080 oralig‘ida. O‘zgarish keyingi yuborishlarga taalluqli; mavjud chek muddatini surmaydi.
5. **Aniq holat:** qabul xabarida muddat Toshkent vaqti bilan chiqadi. Kechiksa bir martalik foydalanuvchi xabari va admin eslatmasi bor. Avtomatik tasdiqlash yo‘q.
6. **Ochiq zakazlar:** bot, sayt, ilova, boshqa yo‘nalishlar bo‘yicha saralash. Kodni bilmasdan topshiriq ochish mumkin.
7. **Bepul taklif:** narx, muddat va izoh. Bir zakazga bir taklif; egasi profil/portfolio va solishtirish orqali bajaruvchi tanlaydi.
8. **Qayta ochish:** egasi kelishuvli zakazni jami 2 marta o‘sha kod/post bilan qayta ochadi. Eski takliflar saqlanadi; rad etilgan/yopilganlarni egasi alohida “Qayta ko‘rib chiqish” bilan faollashtiradi.
9. **Yakunlash:** ikki tomon, keyin admin tasdiqlaydi. Ikkinchi tomon javob bermasa yordam so‘rovi yuborish mumkin.
10. **Profil va fikrlar:** ochiq bio, portfolio, mutaxassislik, tasdiqlangan tugallangan ishlar va moderatsiyadan o‘tgan 1–5 baholi fikrlar.
11. **Natijalar:** yakunlangan zakaz faqat ikkala tomon roziligi bilan ommaviy ro‘yxatda ko‘rinadi. Rozilik qaytarib olinadi.
12. **Mos xabarlar:** foydalanuvchi kategoriya obunasini o‘zi yoqadi/o‘chiradi. Bitta zakaz uchun bitta xabar; yuborishda obuna va status qayta tekshiriladi.
13. **Yordam:** murojaat bazaga saqlanadi, admin javobi bot chatiga yetkaziladi.
14. **Referal:** kanalga kirganda bonus. Chiqsa balans yetarli bo‘lgandagina bonus to‘liq ayriladi va sabab yuboriladi. Yetmasa jarima, qisman ayrish yoki keyin ushlash yo‘q.
15. **Admin hisoboti:** karta tushumi, balans sarfi, bepul postlar alohida. Bosqichlar, qaytgan foydalanuvchilar va birinchi taklifgacha vaqt ko‘rsatiladi.

To‘liq foydalanish: [QOLLANMA.md](QOLLANMA.md).

## Eski versiyadan yangilash

1. Bot jarayonini to'xtating; `.env` va bazaning to'liq zaxira nusxasini oling.
2. ZIPdagi **barcha fayllarni** yangilang. Faqat `main.py`ni almashtirish yetmaydi.
3. O'zingizdagi `.env` va bazani saqlang. Arxivga maxfiy `.env` va haqiqiy baza kiritilmagan.
4. `python -m pip install -r requirements.txt` va `python main.py`.
5. Yangi ustun va jadvallar avtomatik yaratiladi. Eski foydalanuvchilar, balanslar,
   to'lovlar, e'lonlar va zakazlar saqlanadi. Standart salomlashuv/yo'riqnoma
   yangilanadi; admin tahrirlagan maxsus matnlar saqlanadi.

Eski kontakt sotib olish tugmalari endi pul yechmaydi. Eski «Kontakt olish»
tugmasi yangi bepul taklif jarayoniga yo'naltiradi. Kontakt cheki kutish bosqichida
qolgan foydalanuvchiga yangi tartib tushuntiriladi.

**Avval yuborilgan kontakt to'lovlari:** yozuvlar saqlanadi, avtomatik pul
qaytarilmaydi. Kutilayotgan eski kontakt to'lovini tasdiqlash bloklangan.
Admin haqiqiy tushumni tekshirib, foydalanuvchi bilan qaytarishni hal qilishi va
rad etish sababini yozishi kerak. Eski tasdiqlangan to'lovlar tarixdan o'chirilmaydi.

## Ishga tushirish

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# .env ni to'ldiring
.venv/bin/python main.py
```

Windowsda Python `.venv\Scripts\python.exe` manzilida bo'ladi.

| Sozlama | Qiymat |
| --- | --- |
| `BOT_TOKEN` | O'zingizning bot tokeningiz |
| `ADMIN_IDS` | Vergul bilan ajratilgan admin Telegram ID'lari |
| `BOT_USERNAME` | `@`siz username; bo'sh bo'lsa Telegramdan olinadi |
| `CHANNEL_ID` | Kanalning raqamli ID'si |
| `DATABASE_PATH` | Doimiy diskdagi SQLite fayli; standart `data/bot.db` |
| `RUN_MODE` | Lokal uchun `polling`, HTTP hosting uchun `webhook` |
| `WEBHOOK_HOST` | Webhook uchun HTTPS domen; `RENDER_EXTERNAL_URL` ham qo'llanadi |
| `WEBHOOK_SECRET` | Webhook so'rovlarini tekshirish uchun tasodifiy maxfiy qiymat |
| `PORT` | Standart 10000 |

Bot kanal administratori bo‘lsin: post yuborish, tahrirlash, o‘chirish va taklif havolalarini yaratish huquqlari kerak. `chat_member` hodisalari polling va webhook’da yoqilgan.
Webhook `/webhook` manzilida; URL ichida token yo'q. `/health` bot tayyor bo'lguncha
503, keyin 200 qaytaradi. Ishga tushishda xato bo'lsa, jarayon to'xtaydi.

**Render Free uchun tayyor:** `render.yaml` orqali Blueprint deploy.
Admin panelda **Backup yaratish** server bazasini ZIP qilib chatga yuboradi.
**Backupni tiklash** shu faylni qabul qiladi, tekshiradi va tasdiqdan keyin
server bazasini almashtiradi. Tiklashdan oldingi nusxa ham adminga yuboriladi.

Free’da lokal disk vaqtinchalik. Restartdan keyin saqlagan ZIP faylingizni
admin panel orqali qo‘lda tiklang. `ADMIN_IDS` Render Environment’da bo‘lishi shart.
Avtomatik tashqi backup yo‘q. To‘liq tartib: [RENDER_DEPLOY.md](RENDER_DEPLOY.md).

## Ishonchlilik

- Har bir to‘lov yoki kanal bonusini yozish/qaytarish alohida atomik SQLite tranzaksiyasida bajariladi.
- Bajaruvchini tanlash, boshqa so'rovlarni yopish va bildirishnomalar navbati ham
  bitta tranzaksiyada bajariladi. Bir vaqtning o'zida ikki nomzod tanlanmaydi.
- FSM holati bazada saqlanadi; restartdan keyin jarayon davom etadi.
- Kanal tahriri ishlamasa ham, botdagi tanlov darhol yopiladi. Tahrir va
  bildirishnomalar navbatda qoladi; bot ishlayotganda har 30 soniyada qayta uriniladi.
  Hosting to'xtagan bo'lsa, bu ishlar u yana ishga tushganda davom etadi.
- `PUBLICATION_REVIEW` holatida kanalga dastlabki yuborish natijasi noma'lum:
  admin post borligini tekshiradi. Mavjud postni `/linkpost order 12 345`
  bilan bog'lash mumkin. Post yo'q bo'lsa, admin paneldagi qayta joylash ishlatiladi.
- Shaxsiy xabar yuborilganidan keyin jarayon uzilib, yetkazilganligi saqlanmay qolsa,
  bildirishnoma takror kelishi mumkin; so'rov yoki tanlovning o'zi takror yaratilmaydi.

Cheklar rasm identifikatori bilan takrorlanishga tekshiriladi. Bankdagi haqiqiy
pul tushumini admin tekshiradi. Ish bajaruvchi bilan narx/shart bo'yicha kelishuv
shaxsiy chatda amalga oshiriladi; bot xizmat haqini bajaruvchiga o'tkazmaydi.

## Sinov

```bash
python -m unittest discover -s tests -v
```

92 ta test: haqiqiy aiogram marshrutlash va SQLite, soxta Telegram transporti.
Yangi oqim, ruxsatlar, parallel tanlash, takroriy so'rovlar, eski pullik tugmalar,
kanal matni/rasm izohi, xato va qayta urinish, restart, to'lovlar va webhook tekshirilgan.
Haqiqiy Telegramga xabar yuborilmagan, productionga joylashtirilmagan.

## Ishlash cheklovlari

Soatlik eslatmalar va referal hisobini vaqtida yangilash uchun bot doimiy ishlashi kerak.
Telegram saqlab bermagan yoki yetib kelmagan eski a’zolik hodisalarini bot tiklay olmaydi.
Botni yangi kanalga ko‘chirganda referal havolalari va a’zolik tarixi alohida migratsiya talab qiladi.
O‘chiq bonus davrida birinchi kirganlarga orqaga hisoblab bonus yozilmaydi; oldin bonus
berilgan a’zolarning kirish/chiqishi tizim yoqilganda hisob bilan moslashtiriladi.
Oldingi 4 xonali kodlar ishlaydi; yangi kodlar saqlangan eng katta koddan davom etadi.
Kanal tahriri navbatda; avvalgi post bot tomonidan yuborilgan va tahrirlash mumkin bo‘lishi kerak.
Admin importida post egasi admin; yashirilgan forward muallifining shaxsini bot taxmin qilmaydi.

## V4 migratsiyasi va qabul tekshiruvi

- Bazani zaxiralang; barcha fayllarni yangilang. Eski kodlar, balanslar, postlar va referal qoidasi saqlanadi.
- Yangi ustun/jadvallar avtomatik qo‘shiladi. Standart matnlar yangilanadi; adminning boshqa maxsus matnlari saqlanadi.
- Eski to‘lovlar manbasi yozuvlardan aniqlanadi: chek mavjud bo‘lsa karta, cheksiz musbat summa balans, nol summa bepul.
- Bosqich statistikasi v4 dan boshlanadi. Alohida bosqichdagi noyob foydalanuvchi sonlari bir xil kohort konversiyasi emas.
- Qoralama davom ettirilganda oldin ko‘rsatilgan summa saqlanadi. Pullik tizim o‘chirilsa bepul moderatsiya oqimi ishlaydi.
- Live tekshiruv: yangi zakaz → kategoriya → preview → edit → qoralamani qayta ochish → chek → muddat → admin tasdiq → kanal.
- Boshqa akkauntda mos yo‘nalish obunasini yoqing; post kelishini, o‘chirilganda xabar kelmasligini tekshiring.
- Yordam javobi, fikr moderatsiyasi va ikki tomon natija roziligini ikki test akkauntida tekshiring.
- Muddatni o‘zgartirish eski chekning saqlangan muddatini yangilamasligini tekshiring. Tizimga yangi foydalanuvchilar kelishidan oldin adminlar `/start` bosgan bo‘lsin.
- Jonli bot/kanalga bu muhitdan joylashtirish amalga oshirilmagan.


## Taklif orqali bepul joylashtirish

Admin panel → **🤝 Taklif orqali joylashtirish**.
Rejim dastlab o‘chiq; son va talab turi dastlab belgilanmagan.
Admin do‘stlar sonini 1–10000 oralig‘ida, talabni **bir marta** yoki
**har bir yangi post uchun** deb tanlaydi va rejimni alohida yoqadi.
Bu shart pullik tizim o‘chirilganda e’lon hamda zakazlar uchun ishlaydi.
Talab yetmasa qoralama saqlanadi, shaxsiy kanal havolasi va qayta tekshirish tugmasi chiqadi.
