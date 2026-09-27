import asyncio
import logging

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.fsm.storage.memory import SimpleEventIsolation
from services.storage import SQLiteStorage
from aiogram.types import BotCommand, BotCommandScopeDefault, TelegramObject

import config
import database.repo as repo
from database.db import init_db, close_db

logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class BlockedUserMiddleware(BaseMiddleware):
    """Admin tomonidan bloklangan foydalanuvchilarni botdan foydalanishdan
    to'xtatadi."""

    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        if user is not None:
            from services.experience import track
            from datetime import datetime, timezone
            today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
            await track(user.id,"VISIT",key=f"visit:{user.id}:{today}")
            if await repo.get_user(user.id):
                await repo.update_user(user.id,username=user.username,first_name=user.first_name,last_name=user.last_name)
            if await repo.is_user_blocked(user.id):
                if hasattr(event,"data") and hasattr(event,"answer"):
                    await event.answer("⛔️ Siz botdan bloklangansiz.",show_alert=True)
                return None
        return await handler(event, data)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=SQLiteStorage(), events_isolation=SimpleEventIsolation(), disable_fsm=True)
    dp["dispatcher"] = dp
    from services.maintenance import MaintenanceMiddleware
    dp.update.outer_middleware(MaintenanceMiddleware())
    dp.update.outer_middleware(dp.fsm)
    dp.message.filter(F.chat.type == "private")
    from handlers.admin.reply_navigation import AdminReplyScope
    dp.message.outer_middleware(AdminReplyScope())
    dp.callback_query.outer_middleware(AdminReplyScope())
    dp.message.middleware(BlockedUserMiddleware())
    dp.callback_query.middleware(BlockedUserMiddleware())

    from handlers.user import start, menu, ads, orders, mine, referral, complaint, applications
    from handlers.admin import panel, broadcast, receipts, sign, backup

    from handlers.admin import navigation
    dp.include_router(navigation.router)
    dp.include_router(backup.router)
    from handlers import invite_gate
    dp.include_router(invite_gate.router)

    # Foydalanuvchi routerlari
    from handlers import experience
    dp.include_router(experience.router)
    from handlers import upgrades
    dp.include_router(upgrades.router)
    dp.include_router(start.router)
    # MUHIM: menu.router har doim ads/orders/mine/referral/admin
    # routerlaridan OLDIN turishi kerak — aks holda ularning menyu
    # tugmalari boshqa oqimning FSM-state catch-all handlerlariga
    # "yutilib" ketishi mumkin (batafsili: handlers/user/menu.py).
    dp.include_router(menu.router)
    dp.include_router(applications.router)
    dp.include_router(ads.router)
    dp.include_router(orders.router)
    dp.include_router(mine.router)
    dp.include_router(referral.router)
    dp.include_router(complaint.router)

    # Admin routerlari
    dp.include_router(panel.router)
    dp.include_router(receipts.router)
    dp.include_router(sign.router)
    dp.include_router(broadcast.router)

    return dp


_delivery_task = None


async def on_startup(bot: Bot):
    await init_db()
    if not config.BOT_USERNAME:
        config.BOT_USERNAME = (await bot.get_me()).username or ""
    from database.db import get_db
    db = await get_db()
    for table in ("ads","orders"):
        await db.execute(f"UPDATE {table} SET status='PUBLICATION_REVIEW' WHERE status IN ('PUBLISHING','PROCESSING')")
        await db.execute(f"UPDATE {table} SET status='PUBLICATION_FAILED' WHERE status='WAITING_ADMIN' AND id IN (SELECT {'ad_id' if table=='ads' else 'order_id'} FROM payments WHERE status='APPROVED' AND type IN ('AD_PUBLICATION','ORDER_PUBLICATION'))")
    # Bot to'xtab turgan vaqtda tashlab ketilgan (hech qachon to'lanmagan/
    # chek yubormagan) e'lon-zakazlarni ishga tushishda tozalab qo'yamiz.
    await repo.expire_stale_pending()
    await bot.set_my_name(name=config.BOT_DISPLAY_NAME)
    await bot.set_my_commands([
        BotCommand(command="start", description="🏠 Bosh menyu"),
        BotCommand(command="menu", description="🏠 Bosh menyu"),
        BotCommand(command="cancel", description="❌ Bekor qilish"),
        BotCommand(command="admin", description="🛠 Admin panel"),
    ], scope=BotCommandScopeDefault())

    if config.RUN_MODE == "webhook":
        if not config.WEBHOOK_URL:
            raise SystemExit(
                "RUN_MODE=webhook, lekin WEBHOOK_HOST .env'da ko'rsatilmagan!")
        await bot.set_webhook(
            url=config.WEBHOOK_URL,
            secret_token=config.WEBHOOK_SECRET or None,
            drop_pending_updates=False,
            max_connections=1,
            allowed_updates=["message", "callback_query", "chat_member", "my_chat_member"])
        logger.info("Webhook o'rnatildi: %s", config.WEBHOOK_URL)
    else:
        await bot.delete_webhook(drop_pending_updates=False)

    from services.channel import publish_ad, publish_order
    from services.notifications import flush_notifications
    for table, publisher in (("ads",publish_ad),("orders",publish_order)):
        cur = await db.execute(f"SELECT * FROM {table} WHERE status='READY_TO_PUBLISH'")
        for row in await cur.fetchall():
            await publisher(bot,dict(row))
    await flush_notifications(bot)

    for admin_id in await repo.list_admin_ids():
        try:
            await bot.send_message(admin_id, "✅ Qulay Savdo Bot ishga tushdi! Admin panel: /admin")
        except Exception as exc:
            logger.warning("Admin %s'ga xabar yuborilmadi: %s", admin_id, exc)


async def on_shutdown(bot: Bot):
    pass  # close after dispatcher/webhook cleanup, not before active handlers finish


def build_bot() -> Bot:
    return Bot(token=config.BOT_TOKEN)


# ============================================================
#  POLLING — lokal ishlab chiqish uchun (RUN_MODE=polling, standart)
# ============================================================
async def start_delivery(bot):
    global _delivery_task
    from services.order_channel import delivery_worker
    _delivery_task = asyncio.create_task(delivery_worker(bot))


async def stop_delivery():
    global _delivery_task
    if _delivery_task:
        _delivery_task.cancel()
        try:
            await _delivery_task
        except asyncio.CancelledError:
            pass
        _delivery_task = None


async def run_polling():
    bot = build_bot()
    dp = build_dispatcher()
    dp.startup.register(on_startup)
    dp.startup.register(start_delivery)
    dp.shutdown.register(on_shutdown)
    try:
        await dp.start_polling(bot, handle_as_tasks=False)
    finally:
        await stop_delivery()
        await close_db()
        await bot.session.close()


# ============================================================
#  WEBHOOK — Render (va boshqa bepul/HTTP-asoslangan hostinglar) uchun.
#  Render bepul "Web Service" tarifi doimiy fon jarayonini emas, faqat
#  HTTP so'rovlarga javob beruvchi xizmatni qo'llab-quvvatlaydi, shuning
#  uchun bot shu rejimda kichik aiohttp veb-serveri ichida ishlaydi va
#  Telegram xabar yuborganda "uyg'onadi".
#
#  MUHIM: port avval ochiladi, Telegram bilan bog'lanish (set_webhook,
#  admin xabarlari) esa PORT OCHILGANDAN KEYIN amalga oshiriladi. Aks
#  holda (masalan web.run_app() ishlatilganda) aiohttp avval on_startup
#  callback'ini to'liq bajarib bo'lguncha portni ochmaydi — va agar shu
#  callback ichida Telegram API sekinlashsa yoki xato bersa, Render hech
#  qachon ochiq port topa olmay "Port scan timeout" bilan servisni
#  ishga tushirolmaydi.
# ============================================================
async def _run_webhook_async():
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    bot = build_bot()
    dp = build_dispatcher()

    if not config.WEBHOOK_URL or not config.WEBHOOK_SECRET:
        raise SystemExit("Webhook uchun WEBHOOK_HOST va WEBHOOK_SECRET kerak.")
    class DurableRequestHandler(SimpleRequestHandler):
        async def _handle_request(self, bot, request):
            # aiogram's feed_webhook_update can acknowledge after its 55s timeout.
            # Wait for the whole handler, including any requested database restoration.
            from aiogram.types import Update
            update = Update.model_validate(await request.json(), context={"bot": bot})
            result = await self.dispatcher.feed_update(bot, update, **self.data)
            from aiogram.methods import TelegramMethod
            return web.Response(body=self._build_response_writer(
                bot=bot, result=result if isinstance(result, TelegramMethod) else None))

    ready = False
    async def readiness_gate(request, handler):
        if request.path == config.WEBHOOK_PATH and not ready:
            return web.Response(status=503,text="Starting")
        return await handler(request)
    app = web.Application(middlewares=[web.middleware(readiness_gate)])

    async def health(request):
        return web.Response(status=200 if ready else 503,
                            text="Ready" if ready else "Starting")
    app.router.add_get("/",health)
    app.router.add_get("/health",health)
    DurableRequestHandler(dispatcher=dp,bot=bot,secret_token=config.WEBHOOK_SECRET,
                         handle_in_background=False).register(app,path=config.WEBHOOK_PATH)
    setup_application(app,dp,bot=bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner,host="0.0.0.0",port=config.PORT)
    await site.start()
    logger.info("Port %s ochildi; boshlang'ich sozlash kutilmoqda",config.PORT)
    stop = asyncio.Event()
    import signal
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT,signal.SIGTERM):
        try:
            loop.add_signal_handler(sig,stop.set)
        except NotImplementedError:
            pass
    try:
        # Failure stops the process so the host can restart it; health never lies.
        await on_startup(bot)
        await start_delivery(bot)
        ready = True
        await stop.wait()
    finally:
        ready = False
        await stop_delivery()
        await runner.cleanup()
        await close_db()
        await bot.session.close()


def run_webhook():
    asyncio.run(_run_webhook_async())


def main():
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN .env faylida ko'rsatilmagan!")
    if config.RUN_MODE not in ("polling","webhook"):
        raise SystemExit("RUN_MODE polling yoki webhook bo‘lsin.")
    if config.RUN_MODE == "webhook":
        run_webhook()
    else:
        asyncio.run(run_polling())


if __name__ == "__main__":
    main()
