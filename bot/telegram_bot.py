from __future__ import annotations

import asyncio
import io
import logging
import os
import pathlib
import re

from decorators import *
from i18n import localized_text
from inline_keyboards import *
from openai_helper import OpenAIHelper
from PIL import Image
from pydub import AudioSegment
from telegram import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    ChatFullInfo,
    constants,
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
from usage_tracker import UsageTracker
from utils import (
    add_chat_request_to_usage_tracker,
    cleanup_intermediate_files,
    edit_message_with_retry,
    error_handler,
    get_remaining_budget,
    get_reply_to_message_id,
    get_stream_cutoff_values,
    get_thread_id,
    handle_direct_result,
    is_group_chat,
    is_allowed,
    is_direct_result,
    is_within_budget,
    message_text,
    split_into_chunks,
)
from uuid import uuid4

uuid_pattern = "[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}"
audio_preview_dir = 'bot/previews'
VOICES = [os.path.splitext(filename)[0] for filename in os.listdir(audio_preview_dir)]
MODER_ACTIONS = ["Approve", "Deny", "Ban", "Unban"]
# admin_action_pattern = f"^(Approve|Deny|Ban|Unban) ({uuid_pattern}|[0-9]+)$"
moder_action_pattern = f"^({'|'.join(MODER_ACTIONS)}) ({uuid_pattern}|[0-9]+)$"

class ChatGPTTelegramBot:
    """
    Class representing a ChatGPT Telegram Bot.
    """

    def __init__(self, config: dict, openai: OpenAIHelper):
        """
        Initializes the bot with the given configuration and GPT bot object.
        :param config: A dictionary containing the bot configuration
        :param openai: OpenAIHelper object
        """
        self.config = config
        self.openai = openai
        self.bot_language = self.config['bot_language']
        self.chat_id = self.config.get('telegram_channel_id')
        self.commands = [
            BotCommand(command='help', description=localized_text('help_description', self.bot_language)),
            BotCommand(command='reset', description=localized_text('reset_description', self.bot_language)),
            BotCommand(command='stats', description=localized_text('stats_description', self.bot_language)),
            BotCommand(command='resend', description=localized_text('resend_description', self.bot_language))
        ]
        # If imaging is enabled, add the "image" command to the list
        if self.config.get('enable_image_generation', False):
            self.commands.append(BotCommand(command='image', description=localized_text('image_description', self.bot_language)))

        if self.config.get('enable_tts_generation', False):
            self.commands.append(BotCommand(command='tts', description=localized_text('tts_description', self.bot_language)))

        self.group_commands = [BotCommand(
            command='chat', description=localized_text('chat_description', self.bot_language)
        )] + self.commands
        self.disallowed_message = localized_text('disallowed', self.bot_language)
        self.budget_limit_message = localized_text('budget_limit', self.bot_language)
        self.usage = {}
        self.last_message = {}
        self.inline_queries_cache = {}
        self.channel = ChatFullInfo

        self.__admin_ids: set = config['admin_ids']
        self.__moder_ids: set = config['moder_ids']
        self.__user_ids: set = config['user_ids']
        self.__banned_ids: set = config['banned_ids']

    @property
    def all_ids(self) -> set[int]:
        """
        Returns set of bot admin IDs.
        :return: set[int]
        """
        # return self.__admin_ids | self.__user_ids
        return self.admin_ids | self.moder_ids | self.user_ids

    @property
    def admin_ids(self) -> set[int]:
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
    def moder_ids(self) -> set[int]:
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
    def user_ids(self) -> set[int]:
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

    async def help(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Shows the help menu.
        """
        commands = self.group_commands if is_group_chat(update) else self.commands
        commands_description = [f'/{command.command} - {command.description}' for command in commands]
        help_text = (
                localized_text('help_text', self.bot_language)[0] +
                '\n\n' +
                '\n'.join(commands_description) +
                '\n\n' +
                localized_text('help_text', self.bot_language)[1] +
                '\n\n' +
                localized_text('help_text', self.bot_language)[2]
        )
        await update.message.reply_text(help_text, disable_web_page_preview=True)

    @send_action(constants.ChatAction.TYPING)
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Returns token usage statistics for current day and month.
        """
        if not await is_allowed(self.config, update, context):
            logging.warning(f'User {update.message.from_user.name} (id: {update.message.from_user.id}) '
                            'is not allowed to request their usage statistics')
            await self.send_disallowed_message(update, context)
            return

        logging.info(f'User {update.message.from_user.name} (id: {update.message.from_user.id}) '
                     'requested their usage statistics')

        user_id = update.message.from_user.id
        if user_id not in self.usage:
            self.usage[user_id] = UsageTracker(user_id, update.message.from_user.name)

        tokens_today, tokens_month = self.usage[user_id].get_current_token_usage()
        images_today, images_month = self.usage[user_id].get_current_image_count()
        (transcribe_minutes_today, transcribe_seconds_today, transcribe_minutes_month,
         transcribe_seconds_month) = self.usage[user_id].get_current_transcription_duration()
        vision_today, vision_month = self.usage[user_id].get_current_vision_tokens()
        characters_today, characters_month = self.usage[user_id].get_current_tts_usage()
        current_cost = self.usage[user_id].get_current_cost()

        chat_id = update.effective_chat.id
        chat_messages, chat_token_length = self.openai.get_conversation_stats(chat_id)
        remaining_budget = get_remaining_budget(self.config, self.usage, update)
        
        text_current_conversation = (
            f"*{localized_text('stats_conversation', self.bot_language)[0]}*:\n"
            f"{chat_messages} {localized_text('stats_conversation', self.bot_language)[1]}\n"
            f"{chat_token_length} {localized_text('stats_conversation', self.bot_language)[2]}\n"
            "----------------------------\n"
        )
        
        # Check if image generation is enabled and, if so, generate the image statistics for today
        text_today_images = ""
        if self.config.get('enable_image_generation', False):
            text_today_images = f"{images_today} {localized_text('stats_images', self.bot_language)}\n"

        text_today_vision = ""
        if self.config.get('enable_vision', False):
            text_today_vision = f"{vision_today} {localized_text('stats_vision', self.bot_language)}\n"

        text_today_tts = ""
        if self.config.get('enable_tts_generation', False):
            text_today_tts = f"{characters_today} {localized_text('stats_tts', self.bot_language)}\n"
        
        text_today = (
            f"*{localized_text('usage_today', self.bot_language)}:*\n"
            f"{tokens_today} {localized_text('stats_tokens', self.bot_language)}\n"
            f"{text_today_images}"  # Include the image statistics for today if applicable
            f"{text_today_vision}"
            f"{text_today_tts}"
            f"{transcribe_minutes_today} {localized_text('stats_transcribe', self.bot_language)[0]} "
            f"{transcribe_seconds_today} {localized_text('stats_transcribe', self.bot_language)[1]}\n"
            f"{localized_text('stats_total', self.bot_language)}{current_cost['cost_today']:.2f}\n"
            "----------------------------\n"
        )
        
        text_month_images = ""
        if self.config.get('enable_image_generation', False):
            text_month_images = f"{images_month} {localized_text('stats_images', self.bot_language)}\n"

        text_month_vision = ""
        if self.config.get('enable_vision', False):
            text_month_vision = f"{vision_month} {localized_text('stats_vision', self.bot_language)}\n"

        text_month_tts = ""
        if self.config.get('enable_tts_generation', False):
            text_month_tts = f"{characters_month} {localized_text('stats_tts', self.bot_language)}\n"
        
        # Check if image generation is enabled and, if so, generate the image statistics for the month
        text_month = (
            f"*{localized_text('usage_month', self.bot_language)}:*\n"
            f"{tokens_month} {localized_text('stats_tokens', self.bot_language)}\n"
            f"{text_month_images}"  # Include the image statistics for the month if applicable
            f"{text_month_vision}"
            f"{text_month_tts}"
            f"{transcribe_minutes_month} {localized_text('stats_transcribe', self.bot_language)[0]} "
            f"{transcribe_seconds_month} {localized_text('stats_transcribe', self.bot_language)[1]}\n"
            f"{localized_text('stats_total', self.bot_language)}{current_cost['cost_month']:.2f}"
        )

        # text_budget filled with conditional content
        text_budget = "\n\n"
        budget_period = self.config['budget_period']
        if remaining_budget < float('inf'):
            text_budget += (
                f"{localized_text('stats_budget', self.bot_language)}"
                f"{localized_text(budget_period, self.bot_language)}: "
                f"${remaining_budget:.2f}.\n"
            )

        usage_text = text_current_conversation + text_today + text_month + text_budget
        await update.message.reply_text(usage_text, parse_mode=constants.ParseMode.MARKDOWN)

    async def resend(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Resend the last request
        """
        if not await is_allowed(self.config, update, context):
            logging.warning(f'User {update.message.from_user.name}  (id: {update.message.from_user.id})'
                            ' is not allowed to resend the message')
            await self.send_disallowed_message(update, context)
            return

        chat_id = update.effective_chat.id
        if chat_id not in self.last_message:
            logging.warning(f'User {update.message.from_user.name} (id: {update.message.from_user.id})'
                            ' does not have anything to resend')
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('resend_failed', self.bot_language)
            )
            return

        # Update message text, clear self.last_message and send the request to prompt
        logging.info(f'Resending the last prompt from user: {update.message.from_user.name} '
                     f'(id: {update.message.from_user.id})')
        with update.message._unfrozen() as message:
            message.text = self.last_message.pop(chat_id)

        await self.prompt(update=update, context=context)

    async def reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Resets the conversation.
        """
        if not await is_allowed(self.config, update, context):
            logging.warning(f'User {update.message.from_user.name} (id: {update.message.from_user.id}) '
                            'is not allowed to reset the conversation')
            await self.send_disallowed_message(update, context)
            return

        logging.info(f'Resetting the conversation for user {update.message.from_user.name} '
                     f'(id: {update.message.from_user.id})...')

        chat_id = update.effective_chat.id
        reset_content = message_text(update.message)
        self.openai.reset_chat_history(chat_id=chat_id, content=reset_content)
        await update.effective_message.reply_text(
            message_thread_id=get_thread_id(update),
            text=localized_text('reset_done', self.bot_language)
        )

    async def image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Generates an image for the given prompt using DALL·E APIs
        """
        if not self.config['enable_image_generation'] \
                or not await self.check_allowed_and_within_budget(update, context):
            return

        image_query = message_text(update.message)
        if image_query == '':
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('image_no_prompt', self.bot_language)
            )
            return

        logging.info(f'New image generation request received from user {update.message.from_user.name} '
                     f'(id: {update.message.from_user.id})')


        await self._generate_image(update, context, image_query)

    @send_action(constants.ChatAction.UPLOAD_PHOTO)
    async def _generate_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE, image_query) -> None:
        try:
            image_url, image_size = await self.openai.generate_image(prompt=image_query)
            if self.config['image_receive_mode'] == 'photo':
                await update.effective_message.reply_photo(
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    photo=image_url
                )
            elif self.config['image_receive_mode'] == 'document':
                await update.effective_message.reply_document(
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    document=image_url
                )
            else:
                raise Exception(f"env variable IMAGE_RECEIVE_MODE has invalid value {self.config['image_receive_mode']}")
            # add image request to users usage tracker
            user_id = update.message.from_user.id
            self.usage[user_id].add_image_request(image_size, self.config['image_prices'])
            # add guest chat request to guest usage tracker
            if str(user_id) not in self.config['allowed_user_ids'].split(',') and 'guests' in self.usage:
                self.usage["guests"].add_image_request(image_size, self.config['image_prices'])

        except Exception as e:
            logging.exception(e)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                reply_to_message_id=get_reply_to_message_id(self.config, update),
                text=f"{localized_text('image_fail', self.bot_language)}: {str(e)}",
                parse_mode=constants.ParseMode.MARKDOWN
            )

    async def tts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Generates an speech for the given input using TTS APIs
        """
        if not self.config['enable_tts_generation'] \
                or not await self.check_allowed_and_within_budget(update, context):
            return

        tts_query = message_text(update.message)
        if tts_query == '':
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('tts_no_prompt', self.bot_language)
            )
            return

        logging.info(f'New speech generation request received from user {update.message.from_user.name} '
                     f'(id: {update.message.from_user.id})')

        await self._generate_tts(update, context, tts_query)

    @send_action(constants.ChatAction.UPLOAD_VIDEO)
    async def _generate_tts(self, update: Update, context: ContextTypes.DEFAULT_TYPE, tts_query: str) -> None:
        try:
            speech_file, text_length = await self.openai.generate_speech(text=tts_query)

            await update.effective_message.reply_voice(
                reply_to_message_id=get_reply_to_message_id(self.config, update),
                voice=speech_file
            )
            speech_file.close()
            # add image request to users usage tracker
            user_id = update.message.from_user.id
            self.usage[user_id].add_tts_request(text_length, self.config['tts_model'], self.config['tts_prices'])
            # add guest chat request to guest usage tracker
            if str(user_id) not in self.config['allowed_user_ids'].split(',') and 'guests' in self.usage:
                self.usage["guests"].add_tts_request(text_length, self.config['tts_model'], self.config['tts_prices'])

        except Exception as e:
            logging.exception(e)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                reply_to_message_id=get_reply_to_message_id(self.config, update),
                text=f"{localized_text('tts_fail', self.bot_language)}: {str(e)}",
                parse_mode=constants.ParseMode.MARKDOWN
            )

    async def transcribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Transcribe audio messages.
        """
        if not self.config['enable_transcription'] or not await self.check_allowed_and_within_budget(update, context):
            return

        if is_group_chat(update) and self.config['ignore_group_transcriptions']:
            logging.info('Transcription coming from group chat, ignoring...')
            return

        await self._execute_trancribe(update, context)

    @send_action(constants.ChatAction.TYPING)
    async def _execute_trancribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        filename = update.message.effective_attachment.file_unique_id
        filename_mp3 = f'{filename}.mp3'
        try:
            media_file = await context.bot.get_file(update.message.effective_attachment.file_id)
            await media_file.download_to_drive(filename)
        except Exception as e:
            logging.exception(e)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                reply_to_message_id=get_reply_to_message_id(self.config, update),
                text=(
                    f"{localized_text('media_download_fail', self.bot_language)[0]}: "
                    f"{str(e)}. {localized_text('media_download_fail', self.bot_language)[1]}"
                ),
                parse_mode=constants.ParseMode.MARKDOWN
            )
            return

        try:
            audio_track = AudioSegment.from_file(filename)
            audio_track.export(filename_mp3, format="mp3")
            logging.info(f'New transcribe request received from user {update.message.from_user.name} '
                         f'(id: {update.message.from_user.id})')

        except Exception as e:
            logging.exception(e)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                reply_to_message_id=get_reply_to_message_id(self.config, update),
                text=localized_text('media_type_fail', self.bot_language)
            )
            if os.path.exists(filename):
                os.remove(filename)
            return

        user_id = update.message.from_user.id
        if user_id not in self.usage:
            self.usage[user_id] = UsageTracker(user_id, update.message.from_user.name)

        try:
            transcript = await self.openai.transcribe(filename_mp3)

            transcription_price = self.config['transcription_price']
            self.usage[user_id].add_transcription_seconds(audio_track.duration_seconds, transcription_price)

            allowed_user_ids = self.config['allowed_user_ids'].split(',')
            if str(user_id) not in allowed_user_ids and 'guests' in self.usage:
                self.usage["guests"].add_transcription_seconds(audio_track.duration_seconds, transcription_price)

            # check if transcript starts with any of the prefixes
            response_to_transcription = any(transcript.lower().startswith(prefix.lower()) if prefix else False
                                            for prefix in self.config['voice_reply_prompts'])

            if self.config['voice_reply_transcript'] and not response_to_transcription:

                # Split into chunks of 4096 characters (Telegram's message limit)
                transcript_output = f"_{localized_text('transcript', self.bot_language)}:_\n\"{transcript}\""
                chunks = split_into_chunks(transcript_output)

                for index, transcript_chunk in enumerate(chunks):
                    await update.effective_message.reply_text(
                        message_thread_id=get_thread_id(update),
                        reply_to_message_id=get_reply_to_message_id(self.config, update) if index == 0 else None,
                        text=transcript_chunk,
                        parse_mode=constants.ParseMode.MARKDOWN
                    )
            else:
                # Get the response of the transcript
                response, total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=transcript)

                self.usage[user_id].add_chat_tokens(total_tokens, self.config['token_price'])
                if str(user_id) not in allowed_user_ids and 'guests' in self.usage:
                    self.usage["guests"].add_chat_tokens(total_tokens, self.config['token_price'])

                # Split into chunks of 4096 characters (Telegram's message limit)
                transcript_output = (
                    f"_{localized_text('transcript', self.bot_language)}:_\n\"{transcript}\"\n\n"
                    f"_{localized_text('answer', self.bot_language)}:_\n{response}"
                )
                chunks = split_into_chunks(transcript_output)

                for index, transcript_chunk in enumerate(chunks):
                    await update.effective_message.reply_text(
                        message_thread_id=get_thread_id(update),
                        reply_to_message_id=get_reply_to_message_id(self.config, update) if index == 0 else None,
                        text=transcript_chunk,
                        parse_mode=constants.ParseMode.MARKDOWN
                    )

        except Exception as e:
            logging.exception(e)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                reply_to_message_id=get_reply_to_message_id(self.config, update),
                text=f"{localized_text('transcribe_fail', self.bot_language)}: {str(e)}",
                parse_mode=constants.ParseMode.MARKDOWN
            )
        finally:
            if os.path.exists(filename_mp3):
                os.remove(filename_mp3)
            if os.path.exists(filename):
                os.remove(filename)

    async def vision(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Interpret image using vision model.
        """
        if not self.config['enable_vision'] or not await self.check_allowed_and_within_budget(update, context):
            return

        prompt = update.message.caption

        if is_group_chat(update):
            if self.config['ignore_group_vision']:
                logging.info('Vision coming from group chat, ignoring...')
                return
            else:
                trigger_keyword = self.config['group_trigger_keyword']
                if (prompt is None and trigger_keyword != '') or \
                   (prompt is not None and not prompt.lower().startswith(trigger_keyword.lower())):
                    logging.info('Vision coming from group chat with wrong keyword, ignoring...')
                    return

        await self._execute_vision(update, context, prompt)

    @send_action(constants.ChatAction.TYPING)
    async def _execute_vision(self, update: Update, context: ContextTypes.DEFAULT_TYPE, prompt) -> None:
        chat_id = update.effective_chat.id
        image = update.message.effective_attachment[-1]
        try:
            media_file = await context.bot.get_file(image.file_id)
            temp_file = io.BytesIO(await media_file.download_as_bytearray())
        except Exception as e:
            logging.exception(e)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                reply_to_message_id=get_reply_to_message_id(self.config, update),
                text=(
                    f"{localized_text('media_download_fail', self.bot_language)[0]}: "
                    f"{str(e)}. {localized_text('media_download_fail', self.bot_language)[1]}"
                ),
                parse_mode=constants.ParseMode.MARKDOWN
            )
            return

        # convert jpg from telegram to png as understood by openai

        temp_file_png = io.BytesIO()

        try:
            original_image = Image.open(temp_file)

            original_image.save(temp_file_png, format='PNG')
            logging.info(f'New vision request received from user {update.message.from_user.name} '
                         f'(id: {update.message.from_user.id})')

        except Exception as e:
            logging.exception(e)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                reply_to_message_id=get_reply_to_message_id(self.config, update),
                text=localized_text('media_type_fail', self.bot_language)
            )

        user_id = update.message.from_user.id
        if user_id not in self.usage:
            self.usage[user_id] = UsageTracker(user_id, update.message.from_user.name)

        if self.config['stream']:

            stream_response = self.openai.interpret_image_stream(chat_id=chat_id, fileobj=temp_file_png, prompt=prompt)
            i = 0
            prev = ''
            sent_message = None
            backoff = 0
            stream_chunk = 0

            async for content, tokens in stream_response:
                if is_direct_result(content):
                    return await handle_direct_result(self.config, update, content)

                if len(content.strip()) == 0:
                    continue

                stream_chunks = split_into_chunks(content)
                if len(stream_chunks) > 1:
                    content = stream_chunks[-1]
                    if stream_chunk != len(stream_chunks) - 1:
                        stream_chunk += 1
                        try:
                            await edit_message_with_retry(context, chat_id, str(sent_message.message_id),
                                                          stream_chunks[-2])
                        except:
                            pass
                        try:
                            sent_message = await update.effective_message.reply_text(
                                message_thread_id=get_thread_id(update),
                                text=content if len(content) > 0 else "..."
                            )
                        except:
                            pass
                        continue

                cutoff = get_stream_cutoff_values(update, content)
                cutoff += backoff

                if i == 0:
                    try:
                        if sent_message is not None:
                            await context.bot.delete_message(chat_id=sent_message.chat_id,
                                                             message_id=sent_message.message_id)
                        sent_message = await update.effective_message.reply_text(
                            message_thread_id=get_thread_id(update),
                            reply_to_message_id=get_reply_to_message_id(self.config, update),
                            text=content,
                        )
                    except:
                        continue

                elif abs(len(content) - len(prev)) > cutoff or tokens != 'not_finished':
                    prev = content

                    try:
                        use_markdown = tokens != 'not_finished'
                        await edit_message_with_retry(context, chat_id, str(sent_message.message_id),
                                                      text=content, markdown=use_markdown)

                    except RetryAfter as e:
                        backoff += 5
                        await asyncio.sleep(e.retry_after)
                        continue

                    except TimedOut:
                        backoff += 5
                        await asyncio.sleep(0.5)
                        continue

                    except Exception:
                        backoff += 5
                        continue

                    await asyncio.sleep(0.01)

                i += 1
                if tokens != 'not_finished':
                    total_tokens = int(tokens)


        else:

            try:
                interpretation, total_tokens = await self.openai.interpret_image(chat_id, temp_file_png, prompt=prompt)

                try:
                    await update.effective_message.reply_text(
                        message_thread_id=get_thread_id(update),
                        reply_to_message_id=get_reply_to_message_id(self.config, update),
                        text=interpretation,
                        parse_mode=constants.ParseMode.MARKDOWN
                    )
                except BadRequest:
                    try:
                        await update.effective_message.reply_text(
                            message_thread_id=get_thread_id(update),
                            reply_to_message_id=get_reply_to_message_id(self.config, update),
                            text=interpretation
                        )
                    except Exception as e:
                        logging.exception(e)
                        await update.effective_message.reply_text(
                            message_thread_id=get_thread_id(update),
                            reply_to_message_id=get_reply_to_message_id(self.config, update),
                            text=f"{localized_text('vision_fail', self.bot_language)}: {str(e)}",
                            parse_mode=constants.ParseMode.MARKDOWN
                        )
            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=f"{localized_text('vision_fail', self.bot_language)}: {str(e)}",
                    parse_mode=constants.ParseMode.MARKDOWN
                )
        vision_token_price = self.config['vision_token_price']
        self.usage[user_id].add_vision_tokens(total_tokens, vision_token_price)

        allowed_user_ids = self.config['allowed_user_ids'].split(',')
        if str(user_id) not in allowed_user_ids and 'guests' in self.usage:
            self.usage["guests"].add_vision_tokens(total_tokens, vision_token_price)

    async def prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        React to incoming messages and respond accordingly.
        """
        if update.edited_message or not update.message or update.message.via_bot:
            return

        if not await self.check_allowed_and_within_budget(update, context):
            return

        logging.info(
            f'New message received from user {update.message.from_user.name} (id: {update.message.from_user.id})')
        chat_id = update.effective_chat.id
        user_id = update.message.from_user.id
        prompt = message_text(update.message)
        self.last_message[chat_id] = prompt

        if is_group_chat(update):
            trigger_keyword = self.config['group_trigger_keyword']

            if prompt.lower().startswith(trigger_keyword.lower()) or update.message.text.lower().startswith('/chat'):
                if prompt.lower().startswith(trigger_keyword.lower()):
                    prompt = prompt[len(trigger_keyword):].strip()

                if update.message.reply_to_message and \
                        update.message.reply_to_message.text and \
                        update.message.reply_to_message.from_user.id != context.bot.id:
                    prompt = f'"{update.message.reply_to_message.text}" {prompt}'
            else:
                if update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id:
                    logging.info('Message is a reply to the bot, allowing...')
                else:
                    logging.warning('Message does not start with trigger keyword, ignoring...')
                    return

        try:
            total_tokens = 0

            if self.config['stream']:
                await update.effective_message.reply_chat_action(
                    action=constants.ChatAction.TYPING,
                    message_thread_id=get_thread_id(update)
                )

                stream_response = self.openai.get_chat_response_stream(chat_id=chat_id, query=prompt)
                i = 0
                prev = ''
                sent_message = None
                backoff = 0
                stream_chunk = 0

                async for content, tokens in stream_response:
                    if is_direct_result(content):
                        return await handle_direct_result(self.config, update, content)

                    if len(content.strip()) == 0:
                        continue

                    stream_chunks = split_into_chunks(content)
                    if len(stream_chunks) > 1:
                        content = stream_chunks[-1]
                        if stream_chunk != len(stream_chunks) - 1:
                            stream_chunk += 1
                            try:
                                await edit_message_with_retry(context, chat_id, str(sent_message.message_id),
                                                              stream_chunks[-2])
                            except:
                                pass
                            try:
                                sent_message = await update.effective_message.reply_text(
                                    message_thread_id=get_thread_id(update),
                                    text=content if len(content) > 0 else "..."
                                )
                            except:
                                pass
                            continue

                    cutoff = get_stream_cutoff_values(update, content)
                    cutoff += backoff

                    if i == 0:
                        try:
                            if sent_message is not None:
                                await context.bot.delete_message(chat_id=sent_message.chat_id,
                                                                 message_id=sent_message.message_id)
                            sent_message = await update.effective_message.reply_text(
                                message_thread_id=get_thread_id(update),
                                reply_to_message_id=get_reply_to_message_id(self.config, update),
                                text=content,
                            )
                        except:
                            continue

                    elif abs(len(content) - len(prev)) > cutoff or tokens != 'not_finished':
                        prev = content

                        try:
                            use_markdown = tokens != 'not_finished'
                            await edit_message_with_retry(context, chat_id, str(sent_message.message_id),
                                                          text=content, markdown=use_markdown)

                        except RetryAfter as e:
                            backoff += 5
                            await asyncio.sleep(e.retry_after)
                            continue

                        except TimedOut:
                            backoff += 5
                            await asyncio.sleep(0.5)
                            continue

                        except Exception:
                            backoff += 5
                            continue

                        await asyncio.sleep(0.01)

                    i += 1
                    if tokens != 'not_finished':
                        total_tokens = int(tokens)

            else:
                total_tokens = await self._reply_prompt(update, context, chat_id, prompt)

            add_chat_request_to_usage_tracker(self.usage, self.config, user_id, total_tokens)

        except Exception as e:
            logging.exception(e)
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                reply_to_message_id=get_reply_to_message_id(self.config, update),
                text=f"{localized_text('chat_fail', self.bot_language)} {str(e)}",
                parse_mode=constants.ParseMode.MARKDOWN
            )

    @send_action(constants.ChatAction.TYPING)
    async def _reply_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id, prompt):
        response, total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=prompt)

        if is_direct_result(response):
            return await handle_direct_result(self.config, update, response)

        # Split into chunks of 4096 characters (Telegram's message limit)
        chunks = split_into_chunks(response)

        for index, chunk in enumerate(chunks):
            try:
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update) if index == 0 else None,
                    text=chunk,
                    parse_mode=constants.ParseMode.MARKDOWN
                )
            except Exception:
                try:
                    await update.effective_message.reply_text(
                        message_thread_id=get_thread_id(update),
                        reply_to_message_id=get_reply_to_message_id(self.config, update) if index == 0 else None,
                        text=chunk
                    )
                except Exception as exception:
                    raise exception

        return total_tokens

    async def inline_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Handle the inline query. This is run when you type: @botusername <query>
        """
        query = update.inline_query.query
        if len(query) < 3:
            return
        if not await self.check_allowed_and_within_budget(update, context, is_inline=True):
            return

        callback_data_suffix = "gpt:"
        result_id = str(uuid4())
        self.inline_queries_cache[result_id] = query
        callback_data = f'{callback_data_suffix}{result_id}'

        await self.send_inline_query_result(update, result_id, message_content=query, callback_data=callback_data)

    async def send_inline_query_result(self, update: Update, result_id, message_content, callback_data=""):
        """
        Send inline query result
        """
        try:
            reply_markup = None
            if callback_data:
                reply_markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton(text=f'🤖 {localized_text("answer_with_chatgpt", self.bot_language)}',
                                         callback_data=callback_data)
                ]])

            inline_query_result = InlineQueryResultArticle(
                id=result_id,
                title=localized_text("ask_chatgpt", self.bot_language),
                input_message_content=InputTextMessageContent(message_content),
                description=message_content,
                thumbnail_url='https://user-images.githubusercontent.com/11541888/223106202-7576ff11-2c8e-408d-94ea-b02a7a32149a.png',
                reply_markup=reply_markup
            )

            await update.inline_query.answer([inline_query_result], cache_time=0)
        except Exception as e:
            logging.error(f'An error occurred while generating the result card for inline query {e}')

    async def handle_callback_inline_query(self, update: Update, context: CallbackContext):
        """
        Handle the callback query from the inline query result
        """
        callback_data = update.callback_query.data
        user_id = update.callback_query.from_user.id
        inline_message_id = update.callback_query.inline_message_id
        name = update.callback_query.from_user.name
        callback_data_suffix = "gpt:"
        query = ""
        answer_tr = localized_text("answer", self.bot_language)
        loading_tr = localized_text("loading", self.bot_language)

        try:
            if callback_data.startswith(callback_data_suffix):
                unique_id = callback_data.split(':')[1]
                total_tokens = 0

                # Retrieve the prompt from the cache
                query = self.inline_queries_cache.get(unique_id)
                if query:
                    self.inline_queries_cache.pop(unique_id)
                else:
                    error_message = (
                        f'{localized_text("error", self.bot_language)}. '
                        f'{localized_text("try_again", self.bot_language)}'
                    )
                    await edit_message_with_retry(context, chat_id=None, message_id=inline_message_id,
                                                  text=f'{query}\n\n_{answer_tr}:_\n{error_message}',
                                                  is_inline=True)
                    return

                unavailable_message = localized_text("function_unavailable_in_inline_mode", self.bot_language)
                if self.config['stream']:
                    stream_response = self.openai.get_chat_response_stream(chat_id=user_id, query=query)
                    i = 0
                    prev = ''
                    backoff = 0
                    async for content, tokens in stream_response:
                        if is_direct_result(content):
                            cleanup_intermediate_files(content)
                            await edit_message_with_retry(context, chat_id=None,
                                                          message_id=inline_message_id,
                                                          text=f'{query}\n\n_{answer_tr}:_\n{unavailable_message}',
                                                          is_inline=True)
                            return

                        if len(content.strip()) == 0:
                            continue

                        cutoff = get_stream_cutoff_values(update, content)
                        cutoff += backoff

                        if i == 0:
                            try:
                                await edit_message_with_retry(context, chat_id=None,
                                                              message_id=inline_message_id,
                                                              text=f'{query}\n\n{answer_tr}:\n{content}',
                                                              is_inline=True)
                            except:
                                continue

                        elif abs(len(content) - len(prev)) > cutoff or tokens != 'not_finished':
                            prev = content
                            try:
                                use_markdown = tokens != 'not_finished'
                                divider = '_' if use_markdown else ''
                                text = f'{query}\n\n{divider}{answer_tr}:{divider}\n{content}'

                                # We only want to send the first 4096 characters. No chunking allowed in inline mode.
                                text = text[:4096]

                                await edit_message_with_retry(context, chat_id=None, message_id=inline_message_id,
                                                              text=text, markdown=use_markdown, is_inline=True)

                            except RetryAfter as e:
                                backoff += 5
                                await asyncio.sleep(e.retry_after)
                                continue
                            except TimedOut:
                                backoff += 5
                                await asyncio.sleep(0.5)
                                continue
                            except Exception:
                                backoff += 5
                                continue

                            await asyncio.sleep(0.01)

                        i += 1
                        if tokens != 'not_finished':
                            total_tokens = int(tokens)

                else:
                    total_tokens = await self._send_inline_query_response(update, context, is_inline=True)

                add_chat_request_to_usage_tracker(self.usage, self.config, user_id, total_tokens)

        except Exception as e:
            logging.error(f'Failed to respond to an inline query via button callback: {e}')
            logging.exception(e)
            localized_answer = localized_text('chat_fail', self.bot_language)
            await edit_message_with_retry(context, chat_id=None, message_id=inline_message_id,
                                          text=f"{query}\n\n_{answer_tr}:_\n{localized_answer} {str(e)}",
                                          is_inline=True)

    @send_action(constants.ChatAction.TYPING)
    async def _send_inline_query_response(self, update: Update, context: CallbackContext, is_inline: bool = True):
        user_id = update.callback_query.from_user.id
        inline_message_id = update.callback_query.inline_message_id
        name = update.callback_query.from_user.name
        query = ""
        answer_tr = localized_text("answer", self.bot_language)
        loading_tr = localized_text("loading", self.bot_language)
        unavailable_message = localized_text("function_unavailable_in_inline_mode", self.bot_language)
        # Edit the current message to indicate that the answer is being processed
        await context.bot.edit_message_text(inline_message_id=inline_message_id,
                                            text=f'{query}\n\n_{answer_tr}:_\n{loading_tr}',
                                            parse_mode=constants.ParseMode.MARKDOWN)

        logging.info(f'Generating response for inline query by {name}')
        response, total_tokens = await self.openai.get_chat_response(chat_id=user_id, query=query)

        if is_direct_result(response):
            cleanup_intermediate_files(response)
            await edit_message_with_retry(context, chat_id=None,
                                          message_id=inline_message_id,
                                          text=f'{query}\n\n_{answer_tr}:_\n{unavailable_message}',
                                          is_inline=is_inline)
            return

        text_content = f'{query}\n\n_{answer_tr}:_\n{response}'

        # We only want to send the first 4096 characters. No chunking allowed in inline mode.
        text_content = text_content[:4096]

        # Edit the original message with the generated content
        await edit_message_with_retry(context, chat_id=None, message_id=inline_message_id,
                                      text=text_content, is_inline=True)

        return total_tokens

    async def check_allowed_and_within_budget(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                              is_inline=False) -> bool:
        """
        Checks if the user is allowed to use the bot and if they are within their budget
        :param update: Telegram update object
        :param context: Telegram context object
        :param is_inline: Boolean flag for inline queries
        :return: Boolean indicating if the user is allowed to use the bot
        """
        name = update.inline_query.from_user.name if is_inline else update.message.from_user.name
        user_id = update.inline_query.from_user.id if is_inline else update.message.from_user.id

        if not await is_allowed(self.config, update, context, is_inline=is_inline):
            logging.warning(f'User {name} (id: {user_id}) is not allowed to use the bot')
            await self.send_disallowed_message(update, context, is_inline)
            return False
        if not is_within_budget(self.config, self.usage, update, is_inline=is_inline):
            logging.warning(f'User {name} (id: {user_id}) reached their usage limit')
            await self.send_budget_reached_message(update, context, is_inline)
            return False

        return True

    async def _send_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                            msg: str, is_inline: bool = False, parse_mode: constants.ParseMode = None) -> None:
        """
        Sends message to the user.
        """
        if msg is None:
            # msg = f"⛔ Вам запрещено использовать данного бота"
            msg = localized_text("disallowed", self.bot_language)

        if not is_inline:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=msg,
                parse_mode=parse_mode,
                disable_web_page_preview=True
            )
        else:
            result_id = str(uuid4())
            await self.send_inline_query_result(update, result_id, message_content=msg)

    async def send_disallowed_message(self, update: Update, _: ContextTypes.DEFAULT_TYPE, is_inline=False):
        """
        Sends the disallowed message to the user.
        """
        if not is_inline:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=self.disallowed_message,
                disable_web_page_preview=True
            )
        else:
            result_id = str(uuid4())
            await self.send_inline_query_result(update, result_id, message_content=self.disallowed_message)

    async def send_budget_reached_message(self, update: Update, _: ContextTypes.DEFAULT_TYPE, is_inline=False):
        """
        Sends the budget reached message to the user.
        """
        if not is_inline:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=self.budget_limit_message
            )
        else:
            result_id = str(uuid4())
            await self.send_inline_query_result(update, result_id, message_content=self.budget_limit_message)

    @moder_restricted
    async def moder_actions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        moder = update.effective_user
        query = update.callback_query
        action = query.data.split()[0]
        key = query.data.split()[-1]
        user = context.bot_data.get(key)

        await query.answer()

        if key is not None and query.message in context.bot_data['service_msgs'][key][moder.id]:
            context.bot_data['service_msgs'][key][moder.id].remove(query.message)

        match action:
            case "Approve":
                action_text = 'approved'
                self.user_ids = user
                context.bot_data['users'].add(user)
            case "Deny":
                action_text = 'banned'
                self.banned_ids = user
                context.bot_data['banned'].add(user)
                await context.bot.send_message(
                    chat_id=user.id,
                    # text="Админ послал тебя на три буквы, не пиши сюда больше",
                    text=localized_text("user_was_denied", self.bot_language),
                    disable_web_page_preview=True
                )
            # case "Ban":
            #     print("Ban Action")
            #     return
            # case "Unban":
            #     print("Unban Action")
            #     return
            case _:
                # print("Default Action")
                return

        await query.edit_message_text(
            # text=f"User {user.mention_html()} was {action_text}!",
            text=localized_text("user_status", self.bot_language),
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
        await application.bot.set_my_commands(self.group_commands, scope=BotCommandScopeAllGroupChats())
        await application.bot.set_my_commands(self.commands)
        bot_user = await application.bot.get_me()
        log_str = f"Initializing @{bot_user.username}"

        if self.chat_id:
            self.channel = await application.bot.get_chat(self.chat_id)
            log_str = f" with @{self.channel.username} subscription check"

        budget_period = self.config['budget_period']
        for user_type, value in self.config['budget'].items():
            log_str += f"\n\t{user_type.capitalize()} {budget_period} budget is set to {value} $"

        application.bot_data.setdefault('admins', set())
        application.bot_data.setdefault('moders', set())
        application.bot_data.setdefault('users', set())
        application.bot_data.setdefault('banned', set())
        application.bot_data['service_msgs'] = {}
        application.bot_data["waitlist"] = {}
        for user in application.bot_data['users']:
            self.user_ids = user
        for user in application.bot_data['banned']:
            self.banned_ids = user
        
        logging.info(f"{log_str}...")

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

    async def _update(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Check user"""
        if not self.chat_id:
            pass

        if not update.effective_user:
            raise ApplicationHandlerStop

        user = update.effective_user

        if user.id in self.admin_ids and user not in context.bot_data['admins']:
            context.bot_data['admins'].add(user)
            return
        if user.id in self.moder_ids and user not in context.bot_data['moders']:
            context.bot_data['moders'].add(user)
            return
        if user.id in self.user_ids and user not in context.bot_data['users']:
            context.bot_data['users'].add(user)
            return
        if user.id not in self.all_ids:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text="Дождитесь разрешения модератора",  # TODO: localize
                disable_web_page_preview=True
            )
            key = str(uuid4())  # Generate ID and separate value from command
            context.bot_data[key] = user  # Store user in bot_data

            keyboard = [
                [InlineKeyboardButton("Approve", callback_data=f"Approve {key}")],
                [InlineKeyboardButton("Deny", callback_data=f"Deny {key}")]
            ]

            reply_markup = InlineKeyboardMarkup(keyboard)

            for moder in self.moder_ids:
                try:
                    mod_msg = await context.bot.send_message(
                        chat_id=moder,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                        text=f"Новый пользователь {user.mention_html()}! Что с ним делать?"
                    )
                    context.bot_data['mod_msgs'].setdefault(key, {}).setdefault(moder, []).append(mod_msg)
                except:
                    continue

            raise ApplicationHandlerStop

    @check_permissions
    async def check_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Ask bot moderators fow actions on new users
        """
        user = update.effective_user
        if user not in context.bot_data["waitlist"].values():
            # user_msg = "⚠️Дождитесь разрешения модератора"
            user_msg = localized_text("wait_for_approve", self.bot_language)

            key = str(uuid4())  # Generate ID and separate value from command
            context.bot_data["waitlist"][key] = user  # Store user in bot_data

            for moder in self.moder_ids:
                try:
                    msg = await context.bot.send_message(
                        chat_id=moder,
                        reply_markup=await moder_action_keyboard(key, self.bot_language),
                        parse_mode=constants.ParseMode.HTML,
                        # text=f"Новый пользователь {user.mention_html()}! Что с ним делать?"
                        text=f"{localized_text('new_user', self.bot_language)[0]}"
                             f"{user.mention_html()}!"
                             f"{localized_text('new_user', self.bot_language)[1]}"
                    )
                    context.bot_data["service_msgs"].setdefault(key, {}).setdefault(moder, []).append(msg)
                except:
                    continue
        else:
            # user_msg = "⚠️Ждем решения модератора..."
            user_msg = localized_text("waiting_moderator", self.bot_language)

        await self._send_message(update, context, msg=user_msg)
        raise ApplicationHandlerStop

    def run(self):
        """
        Runs the bot indefinitely until the user presses Ctrl+C
        """
        pathlib.Path("data").mkdir(exist_ok=True)
        persistence = PicklePersistence(
            filepath="data/mia_rs_chat_bot_data"
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

        application.add_handler(TypeHandler(Update, callback=self.check_user), group=-2)
        application.add_handler(TypeHandler(Update, callback=self.check_budget), group=-1)
        # application.add_handler(TypeHandler(Update, callback=self._update), group=-1)
        application.add_handler(CommandHandler('reset', self.reset))
        application.add_handler(CommandHandler('help', self.help))
        application.add_handler(CommandHandler('image', self.image))
        application.add_handler(CommandHandler('tts', self.tts))
        application.add_handler(CommandHandler('start', self.help))
        application.add_handler(CommandHandler('stats', self.stats))
        application.add_handler(CommandHandler('resend', self.resend))
        application.add_handler(CommandHandler(
            'chat', self.prompt, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP)
        )
        application.add_handler(MessageHandler(
            filters.PHOTO | filters.Document.IMAGE,
            self.vision))
        application.add_handler(MessageHandler(
            filters.AUDIO | filters.VOICE | filters.Document.AUDIO |
            filters.VIDEO | filters.VIDEO_NOTE | filters.Document.VIDEO,
            self.transcribe))
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self.prompt))
        application.add_handler(InlineQueryHandler(self.inline_query, chat_types=[
            constants.ChatType.GROUP, constants.ChatType.SUPERGROUP, constants.ChatType.PRIVATE
        ]))
        application.add_handler(CallbackQueryHandler(self.moder_actions, pattern=moder_action_pattern))
        application.add_handler(CallbackQueryHandler(self.handle_callback_inline_query))

        application.add_error_handler(error_handler)

        application.run_polling()
