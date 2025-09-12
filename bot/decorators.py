import logging

import functools
from translations import lt
from usage_tracker import UsageTracker
from utils import (
    get_thread_id,
    is_group_chat,
    is_user_in_group,
    get_remaining_budget,
)


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
            for user in self.admin_ids | self.user_ids:
                if not await is_user_in_group(update, context, user):
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


def budget(func):
    @functools.wraps(func)
    async def wrapped(self, update, context, is_inline=False, *args, **kwargs):
        user = update.inline_query.from_user if is_inline else update.effective_user
        lang = self.config['bot_language'] if self.force_language or not user else user.language_code
        budget_period = lt(self.config['budget_period'], lang)
        msg = lt('messages.budget_limit', lang, period=budget_period)

        if user and user.id not in self.usage:
            self.usage[user.id] = UsageTracker(user.id, user.name)
        remaining_budget = get_remaining_budget(self.config, self.admin_ids, self.user_ids, self.usage, update, is_inline=is_inline)

        if not remaining_budget > 0:
            logging.warning(f'User {user.name} (id: {user.id}) reached their usage limit')
            await self._send_message(update, context, msg, is_inline)
            return
        return await func(self, update, context, *args, **kwargs)

    return wrapped

# class send_action1(object):
#     def __init__(self, func):
#         self._func = func
#         self._obj = None
#         self._wrapped = None
#
#     def __call__(self, *args, **kwargs):
#         if not self._wrapped:
#             if self._obj:
#                 self._wrapped = self._wrap_method(self._func)
#                 self._wrapped = functools.partial(self._wrapped, self._obj)
#             else:
#                 self._wrapped = self._wrap_function(self._func)
#         return self._wrapped(*args, **kwargs)
#
#     def __get__(self, obj, type=None):
#         self._obj = obj
#         return self
#
#     def _wrap_method(self, method):
#         @functools.wraps(method)
#         def inner(self, *args, **kwargs):
#             print('Method called on {}:'.format(type(self).__name__))
#             return method(self, *args, **kwargs)
#
#         return inner
#
#
#     def _wrap_function(self, function):
#         @functools.wraps(function)
#         def inner(*args, **kwargs):
#             print('Function called:')
#             return function(*args, **kwargs)
#
#         return inner
