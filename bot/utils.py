from __future__ import annotations

import asyncio
import base64
import itertools
import json
import logging
import os
import telegram

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram_bot import ChatGPTTelegramBot

from telegram import ChatMember, constants, Message, MessageEntity, Update, User
from telegram.ext import CallbackContext, ContextTypes

from usage_tracker import UsageTracker

COST_MAP = {
    "monthly": "cost_month",
    "daily": "cost_today",
    "all-time": "cost_all_time"
}


def message_text(message: Message) -> str:
    """
    Returns the text of a message, excluding any bot commands.
    """
    message_txt = message.text
    if message_txt is None:
        return ''

    for _, text in sorted(message.parse_entities([MessageEntity.BOT_COMMAND]).items(),
                          key=(lambda item: item[0].offset)):
        message_txt = message_txt.replace(text, '').strip()

    return message_txt if len(message_txt) > 0 else ''


async def is_user_in_group(update: Update, context: CallbackContext, user_id: int) -> bool:
    """
    Checks if user_id is a member of the group
    """
    try:
        chat_member = await context.bot.get_chat_member(update.message.chat_id, user_id)
        return chat_member.status in [ChatMember.OWNER, ChatMember.ADMINISTRATOR, ChatMember.MEMBER]
    except telegram.error.BadRequest as e:
        if str(e) == "User not found":
            return False
        else:
            raise e
    except Exception as e:
        raise e


def get_thread_id(update: Update) -> int | None:
    """
    Gets the message thread id for the update, if any
    """
    if update.effective_message and update.effective_message.is_topic_message:
        return update.effective_message.message_thread_id
    return None


def get_stream_cutoff_values(update: Update, content: str) -> int:
    """
    Gets the stream cutoff values for the message length
    """
    if is_group_chat(update):
        # group chats have stricter flood limits
        return 180 if len(content) > 1000 else 120 if len(content) > 200 \
            else 90 if len(content) > 50 else 50
    return 90 if len(content) > 1000 else 45 if len(content) > 200 \
        else 25 if len(content) > 50 else 15


def is_group_chat(update: Update) -> bool:
    """
    Checks if the message was sent from a group chat
    """
    if not update.effective_chat:
        return False
    return update.effective_chat.type in [
        constants.ChatType.GROUP,
        constants.ChatType.SUPERGROUP
    ]


def split_into_chunks(text: str, chunk_size: int = 4096) -> list[str]:
    """
    Splits a string into chunks of a given size.
    """
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


async def edit_message_with_retry(context: ContextTypes.DEFAULT_TYPE, chat_id: int | None,
                                  message_id: str, text: str, markdown: bool = True, is_inline: bool = False):
    """
    Edit a message with retry logic in case of failure (e.g. broken markdown)
    :param context: The context to use
    :param chat_id: The chat id to edit the message in
    :param message_id: The message id to edit
    :param text: The text to edit the message with
    :param markdown: Whether to use markdown parse mode
    :param is_inline: Whether the message to edit is an inline message
    :return: None
    """
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=int(message_id) if not is_inline else None,
            inline_message_id=message_id if is_inline else None,
            text=text,
            parse_mode=constants.ParseMode.MARKDOWN if markdown else None,
        )
    except telegram.error.BadRequest as e:
        if str(e).startswith("Message is not modified"):
            return
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(message_id) if not is_inline else None,
                inline_message_id=message_id if is_inline else None,
                text=text,
            )
        except Exception as e:
            logging.warning(f'Failed to edit message: {str(e)}')
            raise e

    except Exception as e:
        logging.warning(str(e))
        raise e


async def error_handler(_: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles errors in the telegram-python-bot library.
    """
    logging.error(f'Exception while handling an update: {context.error}')


def get_rem(bot_obj: ChatGPTTelegramBot, user_id: User.id) -> float:
    """
    Calculate the remaining budget for a user based on their current usage.
    :param bot_obj: The bot object
    :param user_id: Telegram User ID
    :return: The remaining budget for the user as a float
    """
    budget_period = COST_MAP[bot_obj.config['budget_period']]
    user_budget = 0
    match user_id:
        case _ if user_id in bot_obj.admin_ids:
            user_budget = bot_obj.config['budget']['admin']
        case _ if user_id in bot_obj.moder_ids:
            user_budget = bot_obj.config['budget']['moder']
        case _ if user_id in bot_obj.user_ids:
            user_budget = bot_obj.config['budget']['user']
        case _:
            user_budget = 0

    cost = bot_obj.usage[user_id].get_current_cost()[budget_period]
    rem = user_budget - cost
    return rem


def get_reply_to_message_id(config, update: Update):
    """
    Returns the message id of the message to reply to
    :param config: Bot configuration object
    :param update: Telegram update object
    :return: Message id of the message to reply to, or None if quoting is disabled
    """
    if config['enable_quoting'] or is_group_chat(update):
        return update.message.message_id
    return None


def is_direct_result(response: any) -> bool:
    """
    Checks if the dict contains a direct result that can be sent directly to the user
    :param response: The response value
    :return: Boolean indicating if the result is a direct result
    """
    if type(response) is not dict:
        try:
            json_response = json.loads(response)
            return json_response.get('direct_result', False)
        except:
            return False
    else:
        return response.get('direct_result', False)


async def handle_direct_result(config, update: Update, response: any):
    """
    Handles a direct result from a plugin
    """
    if type(response) is not dict:
        response = json.loads(response)

    result = response['direct_result']
    kind = result['kind']
    format = result['format']
    value = result['value']

    common_args = {
        'message_thread_id': get_thread_id(update),
        'reply_to_message_id': get_reply_to_message_id(config, update),
    }

    if kind == 'photo':
        if format == 'url':
            await update.effective_message.reply_photo(**common_args, photo=value)
        elif format == 'path':
            await update.effective_message.reply_photo(**common_args, photo=open(value, 'rb'))
    elif kind == 'gif' or kind == 'file':
        if format == 'url':
            await update.effective_message.reply_document(**common_args, document=value)
        if format == 'path':
            await update.effective_message.reply_document(**common_args, document=open(value, 'rb'))
    elif kind == 'dice':
        await update.effective_message.reply_dice(**common_args, emoji=value)

    if format == 'path':
        cleanup_intermediate_files(response)


def cleanup_intermediate_files(response: any):
    """
    Deletes intermediate files created by plugins
    """
    if type(response) is not dict:
        response = json.loads(response)

    result = response['direct_result']
    format = result['format']
    value = result['value']

    if format == 'path':
        if os.path.exists(value):
            os.remove(value)


# Function to encode the image
def encode_image(fileobj):
    image = base64.b64encode(fileobj.getvalue()).decode('utf-8')
    return f'data:image/jpeg;base64,{image}'


def decode_image(imgbase64):
    image = imgbase64[len('data:image/jpeg;base64,'):]
    return base64.b64decode(image)


def delete_stt_files(files: list[str]) -> None:
    for file in files:
        if os.path.exists(file):
            os.remove(file)
