import pprint
import sys
sys.path.extend(['/Users/aamite/PycharmProjects/mia_rs_chat_bot/bot'])

import argparse
import asyncio
import json
import io
import logging
import pathlib
import re
import os

from decorators import *
from dotenv import load_dotenv
from helpers import create_user_ids_set
from inline_keyboards import *
from telegram import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    ChatFullInfo,
    constants,
    ForceReply,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
    User,
)
from telegram.error import RetryAfter, TimedOut, BadRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
    InlineQueryHandler,
    MessageHandler,
    PicklePersistence,
    TypeHandler,
)
from uuid import uuid4

# Read .env file
load_dotenv()

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Parsing run script arguments
msg = "Run telegram bot locally"

parser = argparse.ArgumentParser(description=msg)
parser.add_argument("-t", "--test", default=False, action=argparse.BooleanOptionalAction, help = "Use test bot token")
args = parser.parse_args()

if args.test:
    # Override test envs
    load_dotenv("test.env", override=True)


telegram_config = {
    'token': os.environ['TELEGRAM_BOT_TOKEN'],
    'telegram_channel_id': os.environ.get('TELEGRAM_CHANNEL_ID'),
    'proxy': os.environ.get('PROXY', None) or os.environ.get('TELEGRAM_PROXY', None),
    'admin_ids': create_user_ids_set(os.environ.get('TELEGRAM_BOT_ADMIN_USER_IDS')),
    'moder_ids': create_user_ids_set(os.environ.get('TELEGRAM_BOT_MODER_USER_IDS')),
    'user_ids': create_user_ids_set(os.environ.get('TELEGRAM_BOT_ALLOWED_USER_IDS')),
    'banned_ids': create_user_ids_set(os.environ.get('TELEGRAM_BOT_BANNED_USER_IDS')),
    'budget_period': os.environ.get('BUDGET_PERIOD', 'monthly').lower(),
    'user_budgets': os.environ.get('USER_BUDGETS', os.environ.get('MONTHLY_USER_BUDGETS', '*')),
    'persistence_file': os.environ.get('PICKLE_PERSISTENCE_FILE', "data/mia_rs_chat_bot_data")
}

uuid_pattern = "[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}"
audio_preview_dir = 'bot/previews'
VOICES = [os.path.splitext(filename)[0] for filename in os.listdir(audio_preview_dir)]
MODER_ACTIONS = ["Approve", "Deny", "Ban", "Unban", "Skip"]
# admin_action_pattern = f"^(Approve|Deny|Ban|Unban) ({uuid_pattern}|[0-9]+)$"
moder_action_pattern = f"^({'|'.join(MODER_ACTIONS)}) ({uuid_pattern}|[0-9]+)$"


async def error_handler(_: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles errors in the telegram-python-bot library.
    """
    logging.error(f'Exception while handling an update: {context.error}')


class TelegramBot:
    def __init__(self, config: dict):
        self.config = config
        self.chat_id = self.config.get('telegram_channel_id')

        self.__admin_ids: set[User.id] = config['admin_ids']
        self.__moder_ids: set[User.id] = config['moder_ids']
        self.__user_ids: set[User.id] = config['user_ids']
        self.__banned_ids: set[User.id] = config['banned_ids']

    @property
    def all_ids(self) -> set[User.id]:
        """
        Returns set of bot admin IDs.
        :return: set[int]
        """
        return self.admin_ids | self.user_ids | self.moder_ids

    @property
    def admin_ids(self) -> set[User.id]:
        """
        Returns set of bot admin IDs.
        :return: set[int]
        """
        return self.__admin_ids

    @admin_ids.setter
    def admin_ids(self, admin: User) -> None:
        """
        Adds user to the set of bot admin IDs.
        :param admin: telegram._user
        """
        self.__admin_ids.add(admin.id)

    @property
    def moder_ids(self) -> set[User.id]:
        """
        Returns set of bot moderators IDs.
        :return: set[int]
        """
        return self.__moder_ids

    @moder_ids.setter
    def moder_ids(self, moder: User) -> None:
        """
        Adds user to the set of bot moderators IDs.
        :param admin: telegram._user
        """
        self.__moder_ids.add(moder.id)

    @property
    def user_ids(self) -> set[User.id]:
        """
        Returns set of bot user IDs.
        :return: set[int]
        """
        return self.__user_ids

    @user_ids.setter
    def user_ids(self, user: User) -> None:
        """
        Adds user to the set of bot user IDs.
        :param user: telegram._user
        """
        self.__user_ids.add(user.id)

    @property
    def banned_ids(self) -> set[User.id]:
        """
        Returns set of bot banned or denied user IDs.
        :return: set[User.id]
        """
        return self.__banned_ids

    @banned_ids.setter
    def banned_ids(self, user: User) -> None:
        """
        Adds user to the set of bot banned or denied user IDs.
        :param user: User
        """
        self.__banned_ids.add(user.id)

    def del_user_id(self, user: User, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.bot_data['users'].discard(user)
        self.__user_ids.discard(user.id)
        if user.id in self.moder_ids:
            context.bot_data['moders'].discard(user)
            self.__moder_ids.discard(user.id)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send a message when the command /start is issued."""
        user = update.effective_user
        await update.message.reply_html(
            rf"Hi {user.mention_html()}!",
            reply_markup=ForceReply(selective=True),
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send a message when the command /help is issued."""
        await update.message.reply_text("Help!")

    async def echo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Echo the user message."""
        await update.message.reply_text(f"echo: {update.message.text}")

    @moder_restricted
    async def moder_actions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        moder = update.effective_user
        query = update.callback_query
        action = query.data.split()[0]
        key = query.data.split()[-1]
        user = context.bot_data.get(key)

        await query.answer()

        if key is not None and query.message in context.bot_data['mod_msgs'][key][moder.id]:
            context.bot_data['mod_msgs'][key][moder.id].remove(query.message)

        match action:
            case "Approve":
                action_text = 'approved'
                self.user_ids = user
                context.bot_data['users'].add(user)
                await context.bot.send_message(
                    chat_id=user.id,
                    text="✅ Теперь Вы можете пользоваться ботом!",
                    disable_web_page_preview=True
                )
            case "Deny":
                action_text = 'denied'
                self.banned_ids = user
                context.bot_data['banned'].add(user)
                await context.bot.send_message(
                    chat_id=user.id,
                    text="⛔ Извините, Вам запрещено использовать данного бота",
                    disable_web_page_preview=True
                )
            case "Unban":
                print("Unban Action")
                action_text = 'unbanned'
                context.bot_data['banned'].discard(user)
                self.__banned_ids.discard(user.id)
                self.user_ids = user
                context.bot_data['users'].add(user)
                await context.bot.send_message(
                    chat_id=user.id,
                    text="✅ Теперь Вы можете пользоваться ботом!",
                    disable_web_page_preview=True
                )
            case _:
                print("Default Action")
                return

        await query.edit_message_text(
            text=f"User {user.mention_html()} was {action_text}!",
            reply_markup=None,
            parse_mode="HTML"
        )

        for chat_id in context.bot_data['mod_msgs'][key].values():
            for msg in chat_id:
                await msg.edit_text(
                    text=f"User {user.mention_html()} was {action_text} by {moder.mention_html()}!",
                    reply_markup=None,
                    parse_mode="HTML"
                )
                chat_id.remove(msg)

        logging.info(f"User {user.id} was {action_text} by moderator {moder.id}")
        del context.bot_data[key]

    async def post_init(self, application: Application) -> None:
        """
        Post initialization hook for the bot.
        """
        bot_user = await application.bot.get_me()
        log_str = ""

        if self.chat_id:
            self.channel = await application.bot.get_chat(self.chat_id)
            log_str = f" with @{self.channel.username} subscription check"


        user_budgets = self.config['user_budgets'].split(',')
        if len(user_budgets) == 1:
            logging.warning(f"Only one value for budgets is set, this value ({user_budgets}) will be used as "
                            f"{self.config['budget_period']} budget for every regular bot user")

        application.bot_data.setdefault('admins', set())
        application.bot_data.setdefault('moders', set())
        application.bot_data.setdefault('users', set())
        application.bot_data.setdefault('banned', set())
        application.bot_data['mod_msgs'] = {}
        for user in application.bot_data['users']:
            self.user_ids = user
        for user in application.bot_data['banned']:
            self.banned_ids = user

        logging.info(f'Initializing @{bot_user.username}{log_str}...')

    async def post_stop(self, application: Application) -> None:
        """
        Post stop hook for the bot.
        Removes all unused keys from persistence by uuid_v4 pattern
        Deletes all admin action messages from their chats and clears messages persistence
        """
        pattern = re.compile(uuid_pattern)
        [application.bot_data.pop(key, None) for key in list(application.bot_data) if pattern.match(key)]
        for key in application.bot_data['mod_msgs']:
            for chat_id, msgs in application.bot_data['mod_msgs'][key].items():
                if not msgs:
                    continue
                await application.bot.delete_messages(chat_id, [msg.id for msg in msgs])
        del application.bot_data['mod_msgs']

    async def send_message(self, update: Update, _: ContextTypes.DEFAULT_TYPE, is_inline=False, msg: str = None) -> None:
        """
        Sends the message to the user
        """
        if msg is None:
            msg = f"⛔ Вам запрещено использовать данного бота"

        if is_inline:
            result_id = str(uuid4())
            await self.send_inline_query_result(update, result_id, message_content=msg)
        else:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=msg,
                disable_web_page_preview=True
            )

    @check_permission
    async def _update(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Ask bot moderators fow actions on new users
        """
        user = update.effective_user
        if user not in context.bot_data.values():
            user_msg = "⚠️Дождитесь разрешения модератора"

            key = str(uuid4())  # Generate ID and separate value from command
            context.bot_data[key] = user  # Store user in bot_data

            for moder in self.moder_ids:
                try:
                    mod_msg = await context.bot.send_message(
                        chat_id=moder,
                        reply_markup=await moder_approve_keyboard(key),
                        parse_mode=constants.ParseMode.HTML,
                        text=f"Новый пользователь {user.mention_html()}! Что с ним делать?"
                    )
                    context.bot_data['mod_msgs'].setdefault(key, {}).setdefault(moder, []).append(mod_msg)
                except:
                    continue
        else:
            user_msg = "⚠️Ждем решения модератора..."

        await self.send_message(update, context, msg=user_msg)
        raise ApplicationHandlerStop

    @admin_restricted
    async def del_users(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Delete all bot regular users (clean only user_ids set, not the bot_data)
        Safe to use
        """
        self.__user_ids.clear()

    def run(self):
        """
        Runs the bot indefinitely until the user presses Ctrl+C
        """
        pathlib.Path("data").mkdir(exist_ok=True)
        persistence = PicklePersistence(
            filepath=self.config['persistence_file']
        )

        application = (
            ApplicationBuilder()
            .token(self.config['token'])
            .proxy(self.config['proxy'])
            .get_updates_proxy(self.config['proxy'])
            .concurrent_updates(True)
            .persistence(persistence)
            .post_init(self.post_init)
            .post_stop(self.post_stop)
            .build()
        )

        application.add_handler(TypeHandler(Update, callback=self._update), group=-1)
        # on different commands - answer in Telegram
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("del_all", self.del_users))

        # on non command i.e message - echo the message on Telegram
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.echo))

        application.add_handler(CallbackQueryHandler(self.moder_actions, pattern=moder_action_pattern))
        application.add_error_handler(error_handler)

        application.run_polling()


telegram_bot = TelegramBot(config=telegram_config)
telegram_bot.run()
