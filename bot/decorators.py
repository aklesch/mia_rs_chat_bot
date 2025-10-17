import logging

import functools
from telegram.ext import ApplicationHandlerStop
from usage_tracker import UsageTracker
from utils import (
    get_thread_id,
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
