from aiogram.fsm.state import State, StatesGroup

class SettingsStates(StatesGroup):
    """
    Sozlamalar bo'limi uchun holatlar.
    """
    # Foydalanuvchi o'zining shaxsiy formatini yuborishini kutish holati
    waiting_for_template = State()

class BillingStates(StatesGroup):
    """
    To'lov jarayoni uchun holatlar (agar kerak bo'lsa).
    """
    # Masalan, kupon kiritish yoki maxsus chegirmalar uchun ishlatsa bo'ladi
    waiting_for_promo = State()