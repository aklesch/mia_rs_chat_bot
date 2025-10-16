from translations import localized_text
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


async def moder_action_keyboard(key, lang) -> InlineKeyboardMarkup:
    """
    Generates keyboard for bot admins to approve or deny new bot users
    """
    prefix = 'keyboards.moder_approve'
    keyboard = [
        [InlineKeyboardButton(localized_text(f"{prefix}.approve", lang), callback_data=f"Approve {key}")],
        [InlineKeyboardButton(localized_text(f"{prefix}.deny", lang), callback_data=f"Deny {key}")]
    ]
    return InlineKeyboardMarkup(keyboard)
