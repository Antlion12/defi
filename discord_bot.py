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
import shutil
import traceback
import utils

FLAGS = flags.FLAGS
flags.DEFINE_string('config', 'config.json',
                    'JSON file containing bot config.')

# Max amount of time to wait between broadcasting updates.
WAIT_PERIOD_MINUTES = 8 * 60
USAGE = '''You may check current debt positions by typing in the command: `!{command}`
You may also wait for automatic updates.'''
MAX_MESSAGE_LENGTH = 2000


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
        # List of trackers.
        self.trackers = []

        # Load from disk.
        self.load_config()

    def load_config(self):
        assert Path(self._config_file).is_file()
        with open(self._config_file) as f:
            config_json = json.loads(f.read())

        # Configures bot to respond to '!<subscribe_command>' command.
        # Otherwise, defaults to '!defibot'.
        self.subscribe_command = config_json.get(
            'subscribe_command', 'defibot')

        # Fetches the bot's token.
        self.token = config_json.get('token')

        # Loads channels that the bot is subscribed to.
        if 'channels' in config_json:
            # One quirk of json is that int keys are stored as strings. When
            # loading from disk we must conver the str keys back to int keys.
            for channel_id_string, channel_name in config_json['channels'].items():
                self.channels[int(channel_id_string)] = channel_name

        # Loads the maximum waiting period between updates.
        if 'max_wait_period' in config_json:
            self.max_wait_period = timedelta(
                minutes=config_json['max_wait_period'])

        # Loads the trackers.
        if 'trackers' in config_json:
            for tracker_json in config_json['trackers']:
                self.trackers.append(self.parse_tracker(tracker_json))

        # Discord bot's token must be defined.
        assert self.token

        print(f'Subscribed to these channels: {self.channels}')
        print(
            f'Max wait period {utils.format_timedelta(self.max_wait_period)} between alerts.')
        for tracker in self.trackers:
            print(
                f'Tracking {tracker.get_name()}. Last update: {tracker.get_last_update_time()}. Last alert: {tracker.get_last_alert_time()}.')

    def parse_tracker(self, tracker_json: dict) -> DebtTracker:
        address = tracker_json['address']
        tag = tracker_json.get('tag')
        last_alert_time = tracker_json.get('last_alert_time')
        return DebtTracker(address, tag, last_alert_time)

    def save_config(self):
        config_dict = {
            'subscribe_command': self.subscribe_command,
            'token': self.token,
            'channels': self.channels,
            'max_wait_period': self.max_wait_period.seconds // 60,
            'trackers': []
        }
        for t in self.trackers:
            tracker_json = {}
            tracker_json['address'] = t.get_address()
            tag = t.get_tag()
            if tag:
                tracker_json['tag'] = tag
            last_alert_time = t.get_last_alert_time()
            if last_alert_time:
                tracker_json['last_alert_time'] = utils.format_storage_time(
                    last_alert_time)
            config_dict['trackers'].append(tracker_json)

        # Create a backup first.
        shutil.copyfile(self._config_file, self._config_file + '.backup')
        # Store new config file.
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


# An Alert specifies to which channel to send a message.
class Alert(object):
    def __init__(self,
                 channel_id: int,
                 message: str,
                 urgent: bool,
                 wait_period_expired: bool):
        self.channel_id = channel_id
        self.message = message
        self.urgent = urgent
        self.wait_period_expired = wait_period_expired


class AntlionDeFiBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Initialize alerts queue.
        self._alerts_queue = queue.Queue()

        # Configuration state for the bot.
        assert 'config' in kwargs
        self._config = Config(kwargs['config'])

        # Start update task.
        self.update_task.start()
        self.alert_task.start()

    def get_token(self) -> str:
        return self._config.token

    def schedule_alert(self,
                       channel_id: int,
                       message: str,
                       urgent: bool,
                       wait_period_expired: bool):
        self._alerts_queue.put(
            Alert(channel_id, message, urgent, wait_period_expired))

    async def on_ready(self):
        print(f'Logged in as {self.user.name}#{self.user.discriminator}')
        # Schedule alert for this channel containing current messages.
        for channel_id in self._config.get_subscribed_channels():
            channel = self.get_channel(channel_id)
            await channel.send('hello sers. I have returned.')
            await channel.send(USAGE.format(command=self._config.subscribe_command))

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return

        # Handles subscription commands (which add this channel to the set of
        # channels that will be notified in future alerts).
        if message.content.startswith(f'!{self._config.subscribe_command}'):
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
                await message.channel.send(USAGE.format(command=self._config.subscribe_command))
                await message.channel.send('For now, I will share with you the current debts I am tracking.')

                print(f'Subscribed to {channel_name} ({channel_id})')

            # Schedule alert for this channel containing current messages.
            for tracker in self._config.trackers:
                self.schedule_alert(channel_id, tracker.get_last_message(),
                                    urgent=False,
                                    wait_period_expired=False)

    # This loop periodically updates the DebtTracker with the latest debt
    # information for the specified user. An alert is scheduled if the update
    # returned has_alert == True, or if the maximum wait period has elapsed
    # between datetime.now() and the tracker's last alert time.
    @tasks.loop(seconds=300)
    async def update_task(self):
        print(
            'Running update task at {utils.display_time(datetime.now(timezone.utc))} UTC.')
        for tracker in self._config.trackers:
            try:
                has_alert, message = await tracker.update()
            except Exception as e:
                print(f'Exception occured while fetching URL: {e}')
                traceback.print_exc()
                continue

            print(
                f'Updated tracker for {tracker.get_name()} with the following message:')
            print(message)

            wait_period_expired = (
                (datetime.now(timezone.utc) - tracker.get_last_alert_time())
                >= self._config.max_wait_period
            )
            if has_alert or wait_period_expired:
                # Manually sync the alert time to allow for the
                # 'wait_period_expired' criterion to trigger an alert.
                tracker.sync_last_alert_time()
                # Raise alerts only if we have subscribed to channels.
                for channel_id in self._config.get_subscribed_channels():
                    self.schedule_alert(channel_id, message,
                                        has_alert, wait_period_expired)

            # Saves config state after updating this tracker.
            self._config.save_config()

    @update_task.before_loop
    async def before_update_task(self):
        # Wait for bot to log in.
        await self.wait_until_ready()

    async def send_long_message(self, channel, message):
        if len(message) <= MAX_MESSAGE_LENGTH:
            await channel.send(message)
            return

        buffer = ''
        buffer_length = 0
        in_code_block = False
        for line in message.splitlines():
            if '```' in line:
                in_code_block = not in_code_block
            buffer += line + '\n'
            buffer_length += len(line) + 1
            if buffer_length > MAX_MESSAGE_LENGTH - 500:
                # Print existing buffer and reset the buffer variables. Add
                # trailing ``` for the existing buffer and prepending a leading
                # ``` for the next buffer if in_code_block == True.
                if in_code_block:
                    buffer += '```\n'
                await channel.send(buffer)
                if (in_code_block):
                    buffer = '```\n'
                else:
                    buffer = ''
                buffer_length = len(buffer)
        # Print remaining contents from buffer.
        if in_code_block:
            buffer += '```\n'
        await channel.send(buffer)

    # This loop periodically checks the alert queue for alerts to send.
    @tasks.loop(seconds=10)
    async def alert_task(self):
        queue = self._alerts_queue

        print(
            f'Running alert task with {queue.qsize()} alerts at {utils.display_time(datetime.now(timezone.utc))} UTC.')
        for tracker in self._config.trackers:
            print(
                f'Last alert time for {tracker.get_name()}: {utils.display_time(tracker.get_last_alert_time())} UTC')

        while not queue.empty():
            alert = queue.get()
            channel = self.get_channel(alert.channel_id)
            await self.send_long_message(channel, alert.message)
            print(
                f'Sent alert to {self._config.get_channel_name(alert.channel_id)} with the following message:')
            print(alert.message)
            queue.task_done()

    @alert_task.before_loop
    async def before_alert_task(self):
        await self.wait_until_ready()


def main(argv):
    client = AntlionDeFiBot(config=FLAGS.config)
    client.run(client.get_token())


if __name__ == '__main__':
    app.run(main)
