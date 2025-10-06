from i18n import localized_text
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


async def moder_action_keyboard(key, lang) -> InlineKeyboardMarkup:
    """
    Generates keyboard for bot admins to approve or deny new bot users
    """
    keyboard = [
        [InlineKeyboardButton(localized_text('moder_kb_approve', lang), callback_data=f"Approve {key}")],
        [InlineKeyboardButton(localized_text('moder_kb_deny', lang), callback_data=f"Deny {key}")]
    ]
    return InlineKeyboardMarkup(keyboard)
