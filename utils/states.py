from aiogram.fsm.state import State, StatesGroup

class TemplateStates(StatesGroup):
    """
    States for the Custom Template Editor.
    Matches the import in handlers/start.py 🥱💅
    """
    # Waiting for the user to send their custom text format
    waiting_for_template = State()

class BillingStates(StatesGroup):
    """
    States for the payment and billing flow.
    """
    # For future features like promo codes or manual receipt verification
    waiting_for_promo = State()
    waiting_for_receipt = State()

class SettingsStates(StatesGroup):
    """
    General settings states if needed in the future.
    """
    # Example: waiting_for_timezone = State()
    pass