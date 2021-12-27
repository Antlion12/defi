#!/usr/bin/python3

# This script launches the Antlion DeFi Discord bot.
#
# Your initial JSON config file (default path "config.json") must have the
# following field:
#
# {
#   "token": <bot's discord token>
# }
#
# Users who want to subscribe to the bot can type "!defibot" in their channel of
# choice.
#
# Over time, the config.json will update automatically as subscriptions
# accumulate.

from absl import app
from absl import flags
from datetime import datetime
from datetime import MINYEAR
from datetime import timedelta
from datetime import timezone
from debt_lib import DebtTracker
from discord.ext import tasks
from pathlib import Path
from typing import List
from typing import Tuple

import asyncio
import discord
import json
import queue

FLAGS = flags.FLAGS
flags.DEFINE_string("config", "config.json", "JSON file containing bot config.")


class Tracker(object):
    def __init__(self, tracker: DebtTracker, message: str):
        self.tracker = tracker
        self.message = message


# An Alert specifies to which channel to send a message.
class Alert(object):
    def __init__(self, channel_name: str, message: str):
        self.channel_name = channel_name
        self.message = message


class AntlionDeFiBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Set of trackers to update on each loop of the background task.
        self._trackers = []

        # Initialize alerts queue.
        self._alerts_queue = queue.Queue()

        # Initialize last alert time to be min time UTC.
        self._last_alert_time = datetime(MINYEAR, 1, 1, tzinfo=timezone.utc)

        # Set of channels to send alerts to.
        assert "config" in kwargs
        self._config_file = kwargs["config"]
        self.load_config()

        # Start update task.
        self.update_task.start()
        self.alert_task.start()

    def add_tracker(self, tracker: DebtTracker):
        _, message = tracker.get_current()
        self._trackers.append(Tracker(tracker, message))
        print("Current debt position for tracker:")
        print(self._trackers[-1].message)

    def load_config(self):
        assert Path(self._config_file).is_file()
        with open(self._config_file) as config_file:
            self._config = json.loads(config_file.read())
        # Ensures the "token" and "channels" fields are defined for config.
        assert self._config["token"]
        if "channels" not in self._config:
            self._config["channels"] = {}

    def save_config(self):
        with open(self._config_file, 'w') as config_file:
            config_file.write(json.dumps(self._config, indent=4))

    def subscribe_to_channel(self, channel_name: str, channel_id: int):
        self._config["channels"][channel_name] = channel_id

    def channel_is_subscribed(self, channel_name) -> bool:
        return channel_name in self._config["channels"]

    def get_subscribed_channels(self) -> List[str]:
        return self._config["channels"].keys()

    def get_channel_id_by_name(self, name: str) -> int:
        return self._config["channels"][name]

    def get_token(self) -> str:
        return self._config["token"]

    def schedule_alert(self, channel_name, message):
        self._alerts_queue.put(Alert(channel_name, message))

    async def on_ready(self):
        print(f"""Logged in as {self.user.name}#{self.user.discriminator}""")

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return

        if message.content.startswith("!defibot"):
            channel_name = f"""{message.channel.guild.name}#{message.channel.name}"""
            channel_id = message.channel.id

            if self.channel_is_subscribed(channel_name):
                print(f"""User {message.author} requested update.""")
                await message.channel.send(f"""@{message.author} requested an update. Coming right up....""")
            else:
                # This is a new subscription.
                self.subscribe_to_channel(channel_name, channel_id)
                self.save_config()

                await message.channel.send("gm")
                await message.channel.send("You have subscribed to updates from the Antlion DeFi Bot.")
                print(f"""Subscribed to {channel_name} ({channel_id})""")

            # Schedule alert for this channel containing current messages.
            for tracker in self._trackers:
                self.schedule_alert(channel_name, tracker.message)

    @tasks.loop(seconds=300)
    async def update_task(self):
        print("Running update task.")
        for tracker in self._trackers:
            await asyncio.sleep(0)
            has_alert, message = tracker.tracker.update()
            await asyncio.sleep(0)
            tracker.message = message
            print(f"""Updated tracker for {tracker.tracker.get_name()} with the following message:""")
            print(message)

            wait_period_expired = (datetime.now(timezone.utc) - self._last_alert_time) >= timedelta(hours=4)
            if has_alert or wait_period_expired:
                # Raise alerts only if we have subscribed to channels.
                for channel_name in self.get_subscribed_channels():
                    self.schedule_alert(channel_name, message)

    @update_task.before_loop
    async def before_update_task(self):
        # Wait for bot to log in.
        await self.wait_until_ready()
        await asyncio.sleep(20)

    @tasks.loop(seconds=10)
    async def alert_task(self):
        queue = self._alerts_queue
        print(f"""Running alert task with {queue.qsize()} alerts. Last alert time: {self._last_alert_time.strftime("%Y-%m-%d %H:%M:%S")} UTC""")
        while not queue.empty():
            alert = queue.get()
            channel = self.get_channel(self.get_channel_id_by_name(alert.channel_name))
            await channel.send(alert.message)
            print(f"""Sent alert to {alert.channel_name} with the following message:""")
            print(alert.message)
            queue.task_done()
            # If an alert was sent, update last alert time.
            self._last_alert_time = datetime.now(timezone.utc)

    @alert_task.before_loop
    async def before_alert_task(self):
        await self.wait_until_ready()


def main(argv):
    client = AntlionDeFiBot(config=FLAGS.config)
    client.add_tracker(DebtTracker(
        address="0x3f3e305c4ad49271ebda489dd43d2c8f027d2d41", tag="DeFiGod"))
    client.add_tracker(DebtTracker(
        address="0x9c5083dd4838e120dbeac44c052179692aa5dac5", tag="TetraNode"))
    client.run(client.get_token())


if __name__ == "__main__":
    app.run(main)
