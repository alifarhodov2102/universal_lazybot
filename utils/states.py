from aiogram.fsm.state import State, StatesGroup

class TemplateStates(StatesGroup):
    """
    Used by handlers/start.py for custom template editing. 💅
    """
    waiting_for_template = State()

class SettingsStates(StatesGroup):
    """
    Used by handlers/settings.py. 🥱
    Added waiting_for_template to fix the AttributeError in Railway.
    """
    waiting_for_template = State()
    waiting_for_timezone = State() # Future feature for Ali 💅

class BillingStates(StatesGroup):
    """
    States for the payment and billing flow.
    """
    waiting_for_promo = State()
    waiting_for_receipt = State()