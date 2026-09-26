from aiogram.fsm.state import State, StatesGroup


class AdStates(StatesGroup):
    awaiting_payment = State()
    awaiting_content = State()       # matn/rasm kutilmoqda
    awaiting_receipt = State()       # to'lov cheki kutilmoqda


class OrderStates(StatesGroup):
    awaiting_payment = State()
    awaiting_content = State()
    awaiting_receipt = State()


class UnlockStates(StatesGroup):
    awaiting_payment = State()
    awaiting_receipt = State()       # developer kontakt to'lovi cheki


class ReferralStates(StatesGroup):
    pass


class ComplaintStates(StatesGroup):
    awaiting_reason = State()


class BroadcastStates(StatesGroup):
    awaiting_content = State()


class SettingsStates(StatesGroup):
    awaiting_value = State()         # tanlangan sozlama uchun yangi qiymat
    awaiting_sign_text = State()
    awaiting_admin_id = State()


class RejectStates(StatesGroup):
    awaiting_reason = State()        # to'lovni rad etish sababi


class ApplicationStates(StatesGroup):
    price = State()
    deadline = State()
    proposal = State()
    preview = State()


class LookupStates(StatesGroup):
    awaiting_code = State()
