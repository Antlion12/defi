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
from debt_lib import DebtTracker
from discord.ext import tasks
from pathlib import Path

import discord
import json

FLAGS = flags.FLAGS
flags.DEFINE_string("config", "config.json", "JSON file containing bot config.")


class AntlionDeFiBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Set of trackers to update on each loop of the background task.
        self._trackers = []

        # Set of channels to send alerts to.
        assert "config" in kwargs
        self._config_file = kwargs["config"]
        self.load_config()

        self._force_update = False

        # Start background task.
        self.my_background_task.start()

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

    def get_subscribed_channels(self) -> dict:
        return self._config["channels"].items()

    def get_token(self) -> str:
        return self._config["token"]

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
            else:
                # This is a new subscription.
                self.subscribe_to_channel(channel_name, channel_id)
                self.save_config()

                await message.channel.send("gm")
                await message.channel.send("You have subscribed to updates from the Antlion DeFi Bot.")
                print(f"""Subscribed to {channel_name} ({channel_id})""")
            self._force_update = True
            await self.my_background_task()

    @tasks.loop(seconds=300)
    async def my_background_task(self):
        print("Running background task.")
        if not self.get_subscribed_channels():
            print("No subscribed channels. Skipping update.")
            return

        # Run each tracker. and message the channel if the update raises an
        # alert.
        for tracker in self._trackers:
            has_alert, message = tracker.update()
            print(message)
            if not has_alert and not self._force_update:
                continue
            self._force_update = False

            # We have an alert to send.
            for channel_name, channel_id in self.get_subscribed_channels():
                channel = self.get_channel(channel_id)
                await channel.send(message)
                print(f"""Sent alert to {channel_name}.""")


    @my_background_task.before_loop
    async def before_my_task(self):
        await self.wait_until_ready()  # Wait for bot to log in.

    def add_tracker(self, tracker: DebtTracker):
        self._trackers.append(tracker)


def main(argv):
    client = AntlionDeFiBot(config=FLAGS.config)
    client.add_tracker(DebtTracker(
        address="0x3f3e305c4ad49271ebda489dd43d2c8f027d2d41", tag="DeFiGod"))
    client.add_tracker(DebtTracker(
        address="0x9c5083dd4838e120dbeac44c052179692aa5dac5", tag="TetraNode"))
    client.run(client.get_token())


if __name__ == "__main__":
    app.run(main)
