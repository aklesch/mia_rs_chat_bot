import os.path
import pathlib
import json
from datetime import date


class UsageTracker:
    """
    UsageTracker class
    Enables tracking of daily/monthly usage per user.
    User files are stored as JSON in /usage_logs directory.
    JSON example:
    {
        "user_name": "@user_name",
        "current_cost": {
            "day": 0.45,
            "month": 3.23,
            "all_time": 3.23,
            "last_update": "2023-03-14"},
        "usage_history": {
            "chat_tokens": {
                "2023-03-13": 520,
                "2023-03-14": 1532
            },
            "transcription_seconds": {
                "2023-03-13": 125,
                "2023-03-14": 64
            },
            "number_images": {
                "2023-03-12": [0, 2, 3],
                "2023-03-13": [1, 2, 3],
                "2023-03-14": [0, 1, 2]
            }
        }
    }
    """

    def __init__(self, user_id, user_name, logs_dir="usage_logs"):
        """
        Initializes UsageTracker for a user with current date.
        Loads usage data from usage log file.
        :param user_id: Telegram ID of the user
        :param user_name: Telegram user name
        :param logs_dir: path to directory of usage logs, defaults to "usage_logs"
        """
        self.user_id = user_id
        self.logs_dir = logs_dir
        # path to usage file of given user
        self.user_file = f"{logs_dir}/{user_id}.json"

        if os.path.isfile(self.user_file):
            with open(self.user_file, "r") as file:
                self.usage = json.load(file)
            if 'vision_tokens' not in self.usage['usage_history']:
                self.usage['usage_history']['vision_tokens'] = {}
            if 'tts_characters' not in self.usage['usage_history']:
                self.usage['usage_history']['tts_characters'] = {}
        else:
            # ensure directory exists
            pathlib.Path(logs_dir).mkdir(exist_ok=True)
            # create new dictionary for this user
            self.usage = {
                "user_name": user_name,
                "current_cost": {"day": 0.0, "month": 0.0, "all_time": 0.0, "last_update": str(date.today())},
                "usage_history": {"chat_tokens": {}, "transcription_seconds": {}, "number_images": {}, "tts_characters": {}, "vision_tokens":{}}
            }

    def write_to_file(self) -> None:
        """
        Writes usage data to user log file
        """
        with open(self.user_file, "w") as outfile:
            json.dump(self.usage, outfile)

    # token usage functions:
    def add_chat_tokens(self, tokens, tokens_price=0.002) -> None:
        """Adds used tokens from a request to a users usage history and updates current cost
        :param tokens: total tokens used in last request
        :param tokens_price: price per 1000 tokens, defaults to 0.002
        """
        today = str(date.today())
        chat_cost = round(float(tokens) * tokens_price / 1000, 6)
        self.add_current_costs(chat_cost)
        usage = self.usage["usage_history"]["chat_tokens"]
        usage[today] = usage.get(today, 0) + float(tokens)
        self.write_to_file()

    def get_chat_tokens(self) -> tuple[float, float]:
        """Returns the amount of tokens used today and this month
        :return: total number of tokens used per day and per month
        """
        today = str(date.today())
        usage = self.usage["usage_history"]["chat_tokens"]
        tokens_today = usage.get(today, 0)
        tokens_this_month = 0
        for day, tokens in usage.items():
            if day.startswith(today[:7]):
                tokens_this_month += tokens

        return tokens_today, tokens_this_month

    def add_image_request(self, image_size, image_prices="0.016,0.018,0.02") -> None:
        """Add image request to users usage history and update current costs.
        :param image_size: requested image size
        :param image_prices: prices for images of sizes ["256x256", "512x512", "1024x1024"],
                             defaults to [0.016, 0.018, 0.02]
        """
        sizes = ["256x256", "512x512", "1024x1024"]
        requested_size = sizes.index(image_size)
        image_cost = float(image_prices.split(',')[requested_size])
        today = str(date.today())
        self.add_current_costs(image_cost)
        usage = self.usage["usage_history"]["number_images"].setdefault(today, [0, 0, 0])
        usage[requested_size] += 1
        self.write_to_file()

    def get_images_count(self) -> tuple[list[int], list[int]]:
        """Get number of images requested for today and this month.
        :return: total number of images requested per day and per month
        """
        today = str(date.today())
        usage = self.usage["usage_history"]["number_images"]
        images_today = usage.get(today, [0, 0, 0])
        images_this_month = [0, 0, 0]
        for day, images in usage.items():
            if day.startswith(today[:7]):
                images_this_month = [sum(x) for x in zip(images_this_month, images)]

        return images_today, images_this_month

    # vision usage functions
    def add_vision_tokens(self, tokens, vision_token_price=0.01) -> None:
        """
         Adds requested vision tokens to a users usage history and updates current cost.
        :param tokens: total tokens used in last request
        :param vision_token_price: price per 1K tokens transcription, defaults to 0.01
        """
        today = str(date.today())
        vision_cost = round(float(tokens) * vision_token_price / 1000, 6)
        self.add_current_costs(vision_cost)
        usage = self.usage["usage_history"]["vision_tokens"]
        usage[today] = usage.get(today, 0) + float(tokens)
        self.write_to_file()

    def get_vision_tokens(self) -> tuple[float, float]:
        """Get vision tokens for today and this month.
        :return: total amount of vision tokens per day and per month
        """
        today = str(date.today())
        usage = self.usage["usage_history"]["vision_tokens"]
        vision_tokens_today = usage.get(today, 0)
        vision_tokens_this_month = 0
        for day, tokens in usage.items():
            if day.startswith(today[:7]):
                vision_tokens_this_month += tokens

        return vision_tokens_today, vision_tokens_this_month

    def add_tts_chars(self, text_length, tts_model, tts_prices="0.015,0.030") -> None:
        """Add tts request to users usage history and update current costs.
        :param text_length: length of text to generate speach from
        :param tts_model: tts model to generate speach with,
        :param tts_prices: prices for tts models, defaults to [0.015, 0.030]
        """
        tts_models = ['tts-1', 'tts-1-hd']
        requested_tts_model = tts_models.index(tts_model)
        price = float(tts_prices.split(',')[requested_tts_model])
        today = str(date.today())
        tts_cost = round(float(text_length) * price / 1000, 6)
        self.add_current_costs(tts_cost)
        usage = self.usage['usage_history']['tts_characters'].setdefault(tts_model, {})
        usage[today] = usage.get(today, 0) + float(text_length)
        self.write_to_file()

    def get_tts_chars(self) -> tuple[int, int]:
        """Get length of speech generated for today and this month.
        :return: total amount of characters converted to speech per day and per month
        """
        today = date.today()
        usage = self.usage['usage_history']['tts_characters']
        chars_today = 0
        for model in usage:
            chars_today += usage.get(model, {}).get(today, 0)

        chars_this_month = 0
        for model in usage:
            for day, chars in usage.get(model, {}).items():
                if day.startswith(today[:7]):
                    chars_this_month += chars

        # for tts_model in tts_models:
        #     if tts_model in self.usage["usage_history"]["tts_characters"] and \
        #         str(today) in self.usage["usage_history"]["tts_characters"][tts_model]:
        #         characters_day += self.usage["usage_history"]["tts_characters"][tts_model][str(today)]
        #
        # month = str(today)[:7]  # year-month as string
        # characters_month = 0
        # for tts_model in tts_models:
        #     if tts_model in self.usage["usage_history"]["tts_characters"]:
        #         for today, characters in self.usage["usage_history"]["tts_characters"][tts_model].items():
        #             if today.startswith(month):
        #                 characters_month += characters
        return int(chars_today), int(chars_this_month)

    def add_stt_seconds(self, seconds, minute_price=0.006):
        """Adds requested transcription (stt) seconds to a users usage history and updates current cost.
        :param seconds: total seconds used in last request
        :param minute_price: price per minute transcription, defaults to 0.006
        """
        today = str(date.today())
        stt_cost = round(seconds * minute_price / 60, 6)
        self.add_current_costs(stt_cost)
        usage = self.usage['usage_history']["transcription_seconds"]
        usage[today] = usage.get(today, 0) + seconds
        self.write_to_file()

    def get_stt_duration(self) -> tuple[str, str]:
        """Get minutes and seconds of audio transcribed for today and this month.
        :return: total amount of time transcribed per day and per month (2 values)
        """

        def hh_mm_ss(seconds: float):
            minutes, seconds = divmod(seconds, 60)
            hours, minutes = divmod(minutes, 60)
            return f"{hours:02.0f}:{minutes:02.0f}:{seconds:02.2f}"

        today = str(date.today())
        usage_tts = self.usage["usage_history"]["transcription_seconds"]

        if today in usage_tts:
            seconds_day = usage_tts[today]
        else:
            seconds_day = 0

        seconds_month = 0
        for day, seconds in usage_tts.items():
            if day.startswith(today[:7]):
                seconds_month += seconds

        return hh_mm_ss(seconds_day), hh_mm_ss(seconds_month)

    def add_current_costs(self, request_cost) -> None:
        """
        Add current cost to all_time, day and month cost and update last_update date.
        """
        today = str(date.today())
        last_update = self.usage["current_cost"]["last_update"]
        usage = self.usage["current_cost"]

        if today == last_update:
            usage["day"] += request_cost
            usage["month"] += request_cost
        elif today.startswith(last_update[:7]):
            usage["day"] = request_cost
            usage["month"] += request_cost
        else:
            usage["day"] = request_cost
            usage["month"] = request_cost

        usage["all_time"] = usage.get("all_time", 0) + request_cost
        usage["last_update"] = today

    def get_current_cost(self) -> dict[str, float]:
        """Get total USD amount of all requests of the current day and month
        :return: cost of current day and month
        """
        today = str(date.today())
        usage = self.usage["current_cost"]
        last_update = usage["last_update"]
        cost_day = 0.0
        cost_month = 0.0
        if today == last_update:
            cost_day = usage["day"]
            cost_month = usage["month"]
        elif today.startswith(last_update[:7]):
            cost_month = usage["month"]

        cost_all_time = usage.get("all_time", 0.0)
        return {"cost_today": cost_day, "cost_month": cost_month, "cost_all_time": cost_all_time}

    def get_all_time_cost(self, tokens_price=0.002, image_prices="0.016,0.018,0.02", minute_price=0.006, vision_token_price=0.01, tts_prices="0.015,0.030") -> float:
        """Get total USD amount of all requests in history
        :param tokens_price: price per 1000 tokens, defaults to 0.002
        :param image_prices: prices for images of sizes ["256x256", "512x512", "1024x1024"],
            defaults to [0.016, 0.018, 0.02]
        :param minute_price: price per minute transcription, defaults to 0.006
        :param vision_token_price: price per 1K vision token interpretation, defaults to 0.01
        :param tts_prices: price per 1K characters tts per model ['tts-1', 'tts-1-hd'], defaults to [0.015, 0.030]
        :return: total cost of all requests
        """
        usage = self.usage['usage_history']
        chat_tokens = sum(usage['chat_tokens'].values())
        token_cost = round(chat_tokens * tokens_price / 1000, 6)

        images = [sum(values) for values in zip(*usage['number_images'].values())]
        image_prices = [float(x) for x in image_prices.split(',')]
        images_cost = round(sum([count * price for count, price in zip(images, image_prices)]), 6)

        stt_seconds = sum(usage['transcription_seconds'].values())
        stt_cost = round(stt_seconds * minute_price / 60, 6)

        vision_tokens = sum(usage['vision_tokens'].values())
        vision_cost = round(vision_tokens * vision_token_price / 1000, 6)

        tts_chars = [sum(tts_model.values()) for tts_model in usage['tts_characters'].values()]
        tts_prices = [float(x) for x in tts_prices.split(',')]
        tts_cost = round(sum([count * price / 1000 for count, price in zip(tts_chars, tts_prices)]), 6)

        all_time_cost = token_cost + stt_cost + images_cost + vision_cost + tts_cost
        return all_time_cost
