"""Reply-keyboard entry points for the existing admin section handlers."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import database.repo as repo
import keyboards.keyboards as kb
from services.flow import abandon_pending_flow

from handlers.admin.reply_navigation import navigation_context, SubmenuButton

router = Router(name='admin_navigation')


class SectionMessage:
    """A reply-button entry sends a new message instead of editing user text."""
    def __init__(self, message):
        self._message = message

    def __getattr__(self, name):
        return getattr(self._message, name)

    async def edit_caption(self, caption=None, **kwargs):
        return await self._message.answer(caption or "✅ Tayyor", **kwargs)

    async def edit_reply_markup(self, reply_markup=None, **kwargs):
        if reply_markup is not None:
            return await self._message.answer("Bo‘limni tanlang:", reply_markup=reply_markup)

    async def edit_text(self, text, **kwargs):
        return await self._message.answer(text, **kwargs)


class SectionRequest:
    """Reuse section logic without fabricating a Telegram callback query."""
    def __init__(self, message, action):
        self.message = SectionMessage(message)
        self.from_user = message.from_user
        self.bot = message.bot
        self.data = action

    async def answer(self, text=None, **kwargs):
        if text:
            await self.message.answer(text)


@router.message(Command('admin'))
@router.message(F.text == kb.BTN_ADMIN)
@router.message(F.text.in_(kb.ADMIN_SECTION_ACTIONS))
async def open_section(message: Message, state: FSMContext):
    if message.chat.type != 'private' or not await repo.is_admin(message.from_user.id):
        await message.answer('⛔️ Sizda bu bo‘limga huquq yo‘q.')
        return
    from handlers.admin import panel, backup, receipts, sign, broadcast
    from handlers import experience, upgrades, invite_gate
    from services import backup as backup_service

    await navigation_context(state).set_data({"active": True, "admin_mode": True, "actions": {}})
    backup_service.discard_user(message.from_user.id)
    await abandon_pending_flow(state)
    action = kb.ADMIN_SECTION_ACTIONS.get(message.text)
    if action is None:
        await panel.admin_cmd(message, state)
        return
    handlers = {
        'ig:admin': (invite_gate.open_admin, True),
        'backup:create': (backup.export_button, False),
        'backup:restore': (backup.restore_button, True),
        'xp:admin:tickets:0': (experience.tickets, False),
        'xp:admin:reviews:0': (experience.review_queue, False),
        'xp:analytics': (experience.analytics, False),
        'upgrade:queue': (upgrades.queue, False),
        'upgrade:import': (upgrades.import_menu, True),
        'upgrade:money': (upgrades.money_menu, False),
        'admin:pending': (panel.admin_pending, False),
        'admin:ads': (panel.admin_ads, False),
        'admin:orders': (panel.admin_orders, False),
        'admin:users': (panel.admin_users, False),
        'admin:complaints': (receipts.admin_complaints, False),
        'admin:stats': (panel.admin_stats, False),
        'admin:settings': (panel.admin_settings, False),
        'admin:sign': (sign.cb_sign_start, True),
        'admin:broadcast': (broadcast.cb_broadcast_start, True),
        'admin:admins': (panel.admin_admins, False),
    }
    handler, needs_state = handlers[action]
    request = SectionRequest(message, action)
    if needs_state:
        await handler(request, state)
    else:
        await handler(request)


@router.message(F.text == kb.BTN_MORE)
async def additional_menu(message: Message, state: FSMContext):
    from services import backup as backup_service
    backup_service.discard_user(message.from_user.id)
    await abandon_pending_flow(state)
    await navigation_context(state).set_data({"active": True, "admin_mode": False, "actions": {}})
    await message.answer('➕ Qo‘shimcha imkoniyatlar', reply_markup=kb.additional_menu_rb())


@router.message(SubmenuButton())
async def submenu(message: Message, state: FSMContext, dispatcher, admin_action: str):
    if message.chat.type != 'private':
        return
    saved = await navigation_context(state).get_data()
    if saved.get('admin_mode') and not await repo.is_admin(message.from_user.id):
        return
    if admin_action.startswith(('navigation:url:', 'navigation:copy:')):
        await message.answer(admin_action.split(':', 2)[2])
        return
    if admin_action == 'cancel' and saved.get('admin_mode'):
        await open_section(message.model_copy(update={'text': kb.BTN_ADMIN}), state)
        return
    request = SectionRequest(message, admin_action)
    await dispatcher.propagate_event(
        'callback_query', request, bot=message.bot, state=state,
        dispatcher=dispatcher, event_from_user=message.from_user,
        raw_state=await state.get_state())


@router.message(F.text == kb.BTN_HOME)
async def home(message: Message, state: FSMContext):
    from handlers.user.start import on_menu
    from services import backup as backup_service
    backup_service.discard_user(message.from_user.id)
    await navigation_context(state).set_data({"active": True, "admin_mode": False, "actions": {}})
    await on_menu(message, state)
