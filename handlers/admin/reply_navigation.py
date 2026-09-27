"""Persist bot submenus separately from form state and render reply buttons."""
from contextvars import ContextVar
from dataclasses import replace

from aiogram import BaseMiddleware
from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from aiogram.methods import SendMessage
from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Message

import keyboards.keyboards as kb
import database.repo as repo

_scope = ContextVar('admin_reply_scope', default=None)


def navigation_context(state):
    return FSMContext(storage=state.storage, key=replace(state.key, destiny='admin_navigation'))


class AdminReplyScope(BaseMiddleware):
    async def __call__(self, handler, event, data):
        state = data.get('state')
        user = getattr(event, 'from_user', None)
        message = getattr(event, 'message', event)
        if not state or not user or getattr(message.chat, 'type', None) != 'private':
            return await handler(event, data)
        bot = data['bot']
        if not any(isinstance(m, AdminReplyTransport) for m in bot.session.middleware):
            bot.session.middleware(AdminReplyTransport())
        context = navigation_context(state)
        saved = await context.get_data()
        saved.setdefault('active', True)
        if isinstance(event, Message):
            text = event.text or ''
            user_buttons = {b.text for menu in (kb.main_menu_rb(), kb.additional_menu_rb())
                            for row in menu.keyboard for b in row}
            command = text.split()[0].split('@')[0] if text.startswith('/') else ''
            if (text in user_buttons and text not in saved.get('actions', {})) or command in ('/start', '/menu'):
                saved = {'active': True, 'admin_mode': False, 'actions': {}}
        await context.set_data(saved)
        token = _scope.set((user.id, context))
        try:
            return await handler(event, data)
        finally:
            _scope.reset(token)


class AdminReplyTransport:
    async def __call__(self, make_request, bot, method):
        scope = _scope.get()
        markup = getattr(method, 'reply_markup', None)
        if not scope or getattr(method, 'chat_id', None) != scope[0]:
            return await make_request(bot, method)
        _, context = scope
        saved = await context.get_data()
        if not saved.get('active'):
            return await make_request(bot, method)
        if isinstance(markup, ReplyKeyboardMarkup):
            await context.set_data({**saved, 'active': True, 'actions': {}})
        if not isinstance(markup, InlineKeyboardMarkup):
            return await make_request(bot, method)
        actions, rows = {}, []
        for row in markup.inline_keyboard:
            labels = []
            for button in row:
                label = button.text
                suffix = 2
                while label in actions:
                    label = f'{button.text} ({suffix})'
                    suffix += 1
                if button.callback_data:
                    action = button.callback_data
                elif button.url:
                    action = 'navigation:url:' + button.url
                elif button.copy_text:
                    action = 'navigation:copy:' + button.copy_text.text
                else:
                    return await make_request(bot, method)
                actions[label] = action
                labels.append(KeyboardButton(text=label))
            rows.append(labels)
        navigation = [KeyboardButton(text=kb.BTN_HOME)]
        if saved.get('admin_mode') and await repo.is_admin(scope[0]):
            navigation.insert(0, KeyboardButton(text=kb.BTN_ADMIN))
        rows.append(navigation)
        reply = ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)
        if method.__api_method__.startswith('editMessage'):
            method = SendMessage(chat_id=method.chat_id,
                                 text=getattr(method, 'text', None) or getattr(method, 'caption', None) or 'Bo‘limni tanlang:',
                                 reply_markup=reply)
        else:
            method = method.model_copy(update={'reply_markup': reply})
        result = await make_request(bot, method)
        await context.set_data({**saved, 'active': True, 'actions': actions})
        return result


class SubmenuButton(Filter):
    async def __call__(self, message, state):
        saved = await navigation_context(state).get_data()
        action = saved.get('actions', {}).get(message.text)
        if saved.get('active') and action:
            return {'admin_action': action}
        return False
