from telegram import InlineKeyboardButton, InlineKeyboardMarkup


async def moder_approve_keyboard(key) -> InlineKeyboardMarkup:
    """
    Generates keyboard for bot moderators to approve or deny new bot users
    """
    keyboard = [
        [InlineKeyboardButton(f"Approve", callback_data=f"Approve {key}")],
        [InlineKeyboardButton(f"Deny", callback_data=f"Deny {key}")]
    ]

    return InlineKeyboardMarkup(keyboard)
