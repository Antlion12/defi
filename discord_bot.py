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
# choice. This subscription information will be stored in the config for future
# runs of the bot.

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
from utils import format_timedelta

import asyncio
import discord
import json
import queue

FLAGS = flags.FLAGS
flags.DEFINE_string('config', 'config.json',
                    'JSON file containing bot config.')

TIME_FMT = '%Y-%m-%d %H:%M:%S'

# Max amount of time to wait between broadcasting updates.
WAIT_PERIOD_MINUTES = 8 * 60


class Config(object):
    def __init__(self, config_file: str):
        self._config_file = config_file

        # Default values.
        # Discord bot token.
        self.token = ''
        # Mapping from channel name (guild#channel) to channel id (an int).
        self.channels = {}
        # Maximum time between alerts (expressed in minutes).
        self.max_wait_period = timedelta(minutes=WAIT_PERIOD_MINUTES)

        # Load from disk.
        self.load_config()

    def load_config(self):
        assert Path(self._config_file).is_file()
        with open(self._config_file) as f:
            config_json = json.loads(f.read())
        # Ensures the "token" and "channels" fields are defined for config.
        if 'token' in config_json:
            self.token = config_json['token']
        if 'channels' in config_json:
            # One quirk of json is that int keys are stored as strings. When
            # loading from disk we must conver the str keys back to int keys.
            for channel_id_string, channel_name in config_json['channels'].items():
                self.channels[int(channel_id_string)] = channel_name
        if 'max_wait_period' in config_json:
            self.max_wait_period = timedelta(
                minutes=config_json['max_wait_period'])

        # Discord bot's token must be defined.
        assert self.token

        print(f'Subscribed to these channels: {self.channels}')
        print(
            f'Max wait period {format_timedelta(self.max_wait_period)} between alerts.')

    def save_config(self):
        config_dict = {
            'token': self.token,
            'channels': self.channels,
            'max_wait_period': self.max_wait_period.seconds // 60
        }
        with open(self._config_file, 'w') as f:
            f.write(json.dumps(config_dict, indent=4))

    def subscribe_channel(self, channel_id: int, channel_name: str):
        self.channels[channel_id] = channel_name
        self.save_config()

    def is_subscribed(self, channel_id: int) -> bool:
        return channel_id in self.channels

    def get_subscribed_channels(self) -> List[int]:
        return self.channels.keys()

    def get_channel_name(self, channel_id: int) -> str:
        return self.channels[channel_id]


class Tracker(object):
    def __init__(self,
                 debt_tracker: DebtTracker,
                 last_alert_time: datetime,
                 message: str):
        self.debt_tracker = debt_tracker
        self.last_alert_time = last_alert_time
        self.message = message


# An Alert specifies to which channel to send a message.
class Alert(object):
    def __init__(self,
                 channel_id: int,
                 tracker: Tracker,
                 message: str,
                 urgent: bool,
                 wait_period_expired: bool):
        self.channel_id = channel_id
        self.tracker = tracker
        self.message = message
        self.urgent = urgent
        self.wait_period_expired = wait_period_expired


class AntlionDeFiBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Set of trackers to update on each loop of the background task.
        self._trackers = []

        # Initialize alerts queue.
        self._alerts_queue = queue.Queue()

        # Set of channels to send alerts to.
        assert 'config' in kwargs
        self._config = Config(kwargs['config'])

        # Start update task.
        self.update_task.start()
        self.alert_task.start()

    def add_tracker(self, debt_tracker: DebtTracker):
        # Initialize last alert time to be min time UTC.
        last_alert_time = datetime(MINYEAR, 1, 1, tzinfo=timezone.utc)
        # Get last recorded message from DebtTracker.
        _, message = debt_tracker.get_current()

        self._trackers.append(Tracker(debt_tracker, last_alert_time, message))

        print('Current debt position for tracker:')
        print(self._trackers[-1].message)

    def get_token(self) -> str:
        return self._config.token

    def schedule_alert(self,
                       channel_id: int,
                       tracker: Tracker,
                       message: str,
                       urgent: bool,
                       wait_period_expired: bool):
        self._alerts_queue.put(
            Alert(channel_id, tracker, message, urgent, wait_period_expired))

    async def on_ready(self):
        print(f'Logged in as {self.user.name}#{self.user.discriminator}')

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return

        # Handles subscription commands (which add this channel to the set of
        # channels that will be notified in future alerts).
        if message.content.startswith('!defibot'):
            channel_id = message.channel.id
            channel_name = f'{message.channel.guild.name}#{message.channel.name}'

            if self._config.is_subscribed(channel_id):
                print(f'User {message.author} requested update.')
                await message.channel.send(f'{message.author.mention} requested an update. Coming right up...')
            else:
                # This is a new subscription.
                self._config.subscribe_channel(channel_id, channel_name)

                await message.channel.send('gm')
                await message.channel.send('You have subscribed to updates from the Antlion DeFi Bot.')
                print(f'Subscribed to {channel_name} ({channel_id})')

            # Schedule alert for this channel containing current messages.
            for tracker in self._trackers:
                self.schedule_alert(channel_id, tracker,
                                    tracker.message, False, False)

    # This loop periodically updates the DebtTracker with the latest debt
    # information for the specified user. An alert is scheduled if the update
    # returned has_alert == True, or if the maximum wait period has elapsed
    # between datetime.now() and tracker.last_alert_time.
    @tasks.loop(seconds=300)
    async def update_task(self):
        print(
            'Running update task at {datetime.now(timezone.utc).strftime(TIME_FMT)} UTC.')
        for tracker in self._trackers:
            try:
                has_alert, message = await tracker.debt_tracker.update()
            except Exception as e:
                print(f'Exception occured while fetching URL: {e}')
                continue

            tracker.message = message
            print(
                f'Updated tracker for {tracker.debt_tracker.get_name()} with the following message:')
            print(message)

            wait_period_expired = ((datetime.now(timezone.utc) - tracker.last_alert_time)
                                   >= self._config.max_wait_period)
            if has_alert or wait_period_expired:
                # Raise alerts only if we have subscribed to channels.
                for channel_id in self._config.get_subscribed_channels():
                    self.schedule_alert(
                        channel_id, tracker, message, has_alert, wait_period_expired)

    @update_task.before_loop
    async def before_update_task(self):
        # Wait for bot to log in.
        await self.wait_until_ready()

    # This loop periodically checks the alert queue for alerts to send.
    @tasks.loop(seconds=10)
    async def alert_task(self):
        queue = self._alerts_queue

        print(
            f'Running alert task with {queue.qsize()} alerts at {datetime.now(timezone.utc).strftime(TIME_FMT)} UTC.')
        for tracker in self._trackers:
            print(
                f'Last alert time for {tracker.debt_tracker.get_name()}: {tracker.last_alert_time.strftime(TIME_FMT)} UTC')

        while not queue.empty():
            alert = queue.get()
            channel = self.get_channel(alert.channel_id)
            message_prefix = ''
            if alert.urgent:
                alert_role = discord.utils.get(
                    channel.guild.roles, name='Degen')
                if alert_role:
                    message_prefix = (f'{alert_role.mention} ')
            await channel.send(message_prefix + alert.message)
            print(
                f'Sent alert to {self._config.get_channel_name(alert.channel_id)} with the following message:')
            print(alert.message)
            queue.task_done()
            # If an alert was sent, update last alert time for the tracker.
            if alert.urgent or alert.wait_period_expired:
                alert.tracker.last_alert_time = datetime.now(timezone.utc)

    @alert_task.before_loop
    async def before_alert_task(self):
        await self.wait_until_ready()


def main(argv):
    client = AntlionDeFiBot(config=FLAGS.config)
    client.add_tracker(DebtTracker(
        address='0x3f3e305c4ad49271ebda489dd43d2c8f027d2d41', tag='DeFiGod'))
    client.add_tracker(DebtTracker(
        address='0x9c5083dd4838e120dbeac44c052179692aa5dac5', tag='TetraNode'))
    client.run(client.get_token())


if __name__ == '__main__':
    app.run(main)
