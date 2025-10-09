import logging

import functools
from telegram.ext import ApplicationHandlerStop
from usage_tracker import UsageTracker
from utils import (
    get_thread_id,
    is_group_chat,
    is_user_in_group,
    get_rem
)


def check_permissions(func):
    """
    Check user permission to use bot
    """
    @functools.wraps(func)
    async def wrapped(self, update, context, *args, **kwargs):
        user = update.effective_user

        match user:
            case _ if not user or user.is_bot or user.id in self.banned_ids:
                raise ApplicationHandlerStop
            case _ if user.id in self.all_ids:
                if user.id in self.admin_ids:
                    context.bot_data['admins'].add(user)
                if user.id in self.moder_ids:
                    context.bot_data['moders'].add(user)
                if user.id in self.user_ids:
                    context.bot_data['users'].add(user)
                return None
            case _:
                logging.info(f"New user {user.name} is trying to access the bot")
                return await func(self, update, context, *args, **kwargs)

    return wrapped


def budget_check(func):
    """
    Check user remaining budget for period
    """
    @functools.wraps(func)
    async def wrapped(self, update, context, *args, **kwargs):
        user = update.effective_user
        if not user:
            raise ApplicationHandlerStop

        self.usage.setdefault(user.id, UsageTracker(user.id, user.name))
        # TODO - check {func.__name__}

        remaining_budget = get_rem(self, user.id)

        if remaining_budget > 0:
            return None
        else:
            logging.warning(f'User {user.name} (id: {user.id}) reached their usage limit')
            return await func(self, update, context, *args, **kwargs)

    return wrapped


def admin_restricted(func):
    """
    Decorator for handlers allowing only admin access.
    """
    @functools.wraps(func)
    async def wrapped(self, update, context, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in self.admin_ids:
            logging.warning(f"Unauthorized access to {func.__name__} from user {user_id}")
            return
        return await func(self, update, context, *args, **kwargs)

    return wrapped


def moder_restricted(func):
    """
    Decorator for handlers allowing only moderators access.
    """
    @functools.wraps(func)
    async def wrapped(self, update, context, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in self.moder_ids | self.admin_ids:
            logging.warning(f"Unauthorized access to {func.__name__} from user {user_id}")
            return
        return await func(self, update, context, *args, **kwargs)

    return wrapped


def user_restricted(func):
    """
    Decorator for handlers allowing only user access.
    """
    @functools.wraps(func)
    async def wrapped(self, update, context, is_inline=False, *args, **kwargs):
        user_id = update.inline_query.from_user.id if is_inline else update.message.from_user.id
        name = update.inline_query.from_user.name if is_inline else update.message.from_user.name
        print(user_id, "is", " in" if user_id in self.all_ids else "not in", self.all_ids)
        if user_id not in self.all_ids:
            await self.send_disallowed_message(update, context)
            logging.warning(f"Unauthorized access to '{func.__name__}' from user {user_id}")
            return
        if not is_inline and is_group_chat(update):
            for user_id in self.admin_ids | self.user_ids:
                if not await is_user_in_group(update, context, user_id):
                    # logging.info(f'{user} is a member. Allowing group chat message...')
                    return
            logging.info(f'Group chat messages from user {name} (id: {user_id}) are not allowed')
        return await func(self, update, context, *args, **kwargs)

    return wrapped


def send_action(action):
    """
    Sends `action` while processing func command.
    """
    def decorator(func):
        @functools.wraps(func)
        async def command_func(self, update, context, is_inline=False, *args, **kwargs):
            if not is_inline:
                await update.effective_message.reply_chat_action(
                    action=action,
                    message_thread_id=get_thread_id(update)
                )
            return await func(self, update, context, *args, **kwargs)

        return command_func

    return decorator
