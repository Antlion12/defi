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
from collections import defaultdict
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from debt_lib import DebtTracker
from discord.ext import tasks
from link_lib import LinkTracker
from name_lib import NameTracker
from pathlib import Path
from typing import List
from typing import Optional
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
USAGE_DEBTTRACKER = 'Enter `!{command}` to check current debt positions.'
USAGE_NAMETRACKER = 'Enter `!{command}` to check name tracker.'
USAGE_LINKTRACKER = 'Enter `!{command}` to check LINK tracker.'
MAX_MESSAGE_LENGTH = 2000

DEFAULT_SUBSCRIBE_COMMANDS = {
    'defibot': 'DebtTracker',
    'kangabot': 'NameTracker',
    'linkbot': 'LinkTracker'
}


class Config(object):
    def __init__(self, config_file: str, client: discord.Client):
        self._config_file = config_file
        self._client = client

        # Default values.
        # Discord bot token.
        self.token = ''
        # Mapping from channel name (guild#channel) to channel id (an int).
        self.channels = {}
        # Maximum time between alerts (expressed in minutes).
        self.max_wait_period = timedelta(minutes=WAIT_PERIOD_MINUTES)
        # List of trackers.
        self.trackers = []
        # Map of subscribe commands to their respective tracker types.
        self.subscribe_commands = {}
        # Convenience map for mapping subscribe commands to their respective
        # trackers.
        self.command_to_trackers = {}

        # Load from disk.
        self.load_config()

    def load_config(self):
        assert Path(self._config_file).is_file()
        with open(self._config_file) as f:
            config_json = json.loads(f.read())

        # Fetches the bot's token.
        self.token = config_json.get('token')
        # Discord bot's token must be defined.
        assert self.token

        # Loads a map of subscribe commands to their respective tracker types.
        self.subscribe_commands = config_json.get('subscribe_commands')
        if not self.subscribe_commands:
            self.subscribe_commands = DEFAULT_SUBSCRIBE_COMMANDS
        assert self.subscribe_commands

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
        for command in self.subscribe_commands:
            self.command_to_trackers[command] = []
        if 'trackers' in config_json:
            for tracker_json in config_json['trackers']:
                self.trackers.append(self.parse_tracker(tracker_json))
            for tracker in self.trackers:
                self.command_to_trackers[tracker.get_subscribe_command()].append(
                    tracker)

        print(f'Subscribed to these channels: {self.channels}')
        print(
            f'Max wait period {utils.format_timedelta(self.max_wait_period)} between alerts.')
        for tracker in self.trackers:
            print(
                f'Tracking {tracker.get_name()}. Last update: {tracker.get_last_update_time()}. Last alert: {tracker.get_last_alert_time()}. Command: {tracker.get_subscribe_command()}.')

    def parse_tracker(self, tracker_json: dict) -> DebtTracker:
        tracker_type = tracker_json['type']
        if tracker_type == DebtTracker.__name__:
            address = tracker_json['address']
            tag = tracker_json.get('tag')
            last_alert_time = tracker_json.get('last_alert_time')
            subscribe_command = tracker_json.get('subscribe_command')
            channels = tracker_json.get('channels')
            ignorable_debts = tracker_json.get('ignorable_debts')
            return DebtTracker(address, tag, subscribe_command,
                               last_alert_time, channels, ignorable_debts)
        elif tracker_type == NameTracker.__name__:
            user_id = tracker_json['user_id']
            tag = tracker_json['tag']
            last_alert_time = tracker_json.get('last_alert_time')
            subscribe_command = tracker_json.get('subscribe_command')
            channels = tracker_json.get('channels')
            return NameTracker(client=self._client,
                               user_id=user_id,
                               tag=tag,
                               subscribe_command=subscribe_command,
                               last_alert_time=last_alert_time,
                               channels=channels)
        elif tracker_type == LinkTracker.__name__:
            identifier = tracker_json.get('identifier')
            tag = tracker_json.get('tag')
            last_alert_time = tracker_json.get('last_alert_time')
            subscribe_command = tracker_json.get('subscribe_command')
            channels = tracker_json.get('channels')
            return LinkTracker(identifier=identifier,
                               tag=tag,
                               subscribe_command=subscribe_command,
                               last_alert_time=last_alert_time,
                               channels=channels)
        else:
            log.fatal(f'Invalid tracker type: {tracker_type}')

    # Adds or updates the tracker for address/tag with the channel_id. Returns
    # the tracker object associated with this update.
    async def add_and_return_tracker(self, identifier: str, tag: Optional[str], command: str, channel_id: int):
        # Find the matching tracker for address/tag (and if it doesn't exist,
        # create one).
        tracker = None
        for curr_tracker in self.trackers:
            if (curr_tracker.get_identifier() == identifier and
                    curr_tracker.get_tag() == tag):
                tracker = curr_tracker
        if not tracker:
            if command not in self.subscribe_commands:
                log.fatal(f'Command {command} not among subscribe commands')

            if self.subscribe_commands[command] == DebtTracker.__name__:
                tracker = DebtTracker(address=identifier,
                                      tag=tag,
                                      subscribe_command=command,
                                      last_alert_time=None,
                                      channels=None,
                                      ignorable_debts=None)
            elif self.subscribe_commands[command] == NameTracker.__name__:
                tracker = NameTracker(client=self._client,
                                      user_id=identifier,
                                      tag=tag,
                                      subscribe_command=command,
                                      last_alert_time=None,
                                      channels=None)
            elif self.subscribe_commands[command] == LinkTracker.__name__:
                tracker = LinkTracker(identifier=identifier,
                                      tag=tag,
                                      subscribe_command=command,
                                      last_alert_time=None,
                                      channels=None)
            else:
                log.fatal(
                    f'For command {command}, invalid tracker type: {self.subscribe_commands[command]}')

            await tracker.update()  # Query new debts for the first time.
            self.trackers.append(tracker)

        # Add the channel info to the tracker if it doesn't already exist.
        if not tracker.has_channel(channel_id):
            tracker.add_channel(channel_id)

        self.save_config()
        return tracker

    def save_config(self):
        config_dict = {
            'subscribe_commands': self.subscribe_commands,
            'token': self.token,
            'channels': self.channels,
            'max_wait_period': self.max_wait_period.seconds // 60,
            'trackers': []
        }
        for t in self.trackers:
            tracker_json = {}

            tracker_type = type(t).__name__
            tracker_json['type'] = tracker_type

            if tracker_type == DebtTracker.__name__:
                tracker_json['address'] = t.get_address()
                tag = t.get_tag()
                if tag:
                    tracker_json['tag'] = tag
                tracker_json['last_alert_time'] = utils.format_storage_time(
                    t.get_last_alert_time())
                tracker_json['subscribe_command'] = t.get_subscribe_command()
                tracker_json['channels'] = t.get_channels()
                tracker_json['ignorable_debts'] = t.get_ignorable_debts()
            elif tracker_type == NameTracker.__name__:
                tracker_json['user_id'] = t.get_user_id()
                tracker_json['tag'] = t.get_tag()
                tracker_json['last_alert_time'] = utils.format_storage_time(
                    t.get_last_alert_time())
                tracker_json['subscribe_command'] = t.get_subscribe_command()
                tracker_json['channels'] = t.get_channels()
            elif tracker_type == LinkTracker.__name__:
                tracker_json['identifier'] = t.get_identifier()
                tracker_json['tag'] = t.get_tag()
                tracker_json['last_alert_time'] = utils.format_storage_time(
                    t.get_last_alert_time())
                tracker_json['subscribe_command'] = t.get_subscribe_command()
                tracker_json['channels'] = t.get_channels()
            else:
                print(f'Could not save unsupported tracker: {tracker_type}')
                continue

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
        self._config = Config(kwargs['config'], client=self)

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

    async def schedule_alerts_for_channel(self, channel: discord.TextChannel,
                                          command: str, trackers: list):
        tracker_count = 0
        for tracker in trackers:
            print(f'Comparing {tracker} with command {command}')
            if (tracker.has_channel(channel.id) and command == tracker.get_subscribe_command()):
                tracker_count += 1
                self.schedule_alert(channel.id, tracker.get_last_message(),
                                    urgent=False,
                                    wait_period_expired=False)
        if tracker_count == 0:
            await channel.send(f'This channel does not have any trackers for {command}.')

    def trackers_for_channel(self, channel_id: int) -> int:
        tracker_names = []
        tracker_output = ''
        for tracker in self._config.trackers:
            if tracker.has_channel(channel_id):
                tracker_output += f'\n    * {tracker.get_name()}'
        if not tracker_output:
            tracker_output = 'None'
        return tracker_output

    async def send_usages(self, channel: discord.TextChannel):
        for command, tracker_type in self._config.subscribe_commands.items():
            if tracker_type == DebtTracker.__name__:
                await channel.send(USAGE_DEBTTRACKER.format(command=command))
            elif tracker_type == NameTracker.__name__:
                await channel.send(USAGE_NAMETRACKER.format(command=command))
            elif tracker_type == LinkTracker.__name__:
                await channel.send(USAGE_LINKTRACKER.format(command=command))
        await channel.send('You may also wait for automatic updates.')

    async def on_ready(self):
        print(f'Logged in as {self.user.name}#{self.user.discriminator}')
        # Schedule alert for this channel containing current messages.
        for channel_id in self._config.get_subscribed_channels():
            channel = self.get_channel(channel_id)
            await channel.send('hello sers. I have returned.')
            await channel.send(f'Current trackers for this channel: {self.trackers_for_channel(channel_id)}')
            await self.send_usages(channel)

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return

        channel_id = message.channel.id
        channel_name = f'{message.channel.guild.name}#{message.channel.name}'
        message_tokens = message.content.split()
        for command, trackers in self._config.command_to_trackers.items():
            if message_tokens[0] != f'!{command}':
                continue

            # Handles subscription commands (which add this channel to the set of
            # channels that will be notified in future alerts).
            if self._config.is_subscribed(channel_id) and len(message_tokens) > 1:
                # Add/update a tracker for the given identifier and tag using this
                # channel.
                identifier = message_tokens[1]
                tag = ' '.join(message_tokens[2:]) if len(
                    message_tokens) >= 3 else None
                print(
                    f'User {message.author} requested update on {identifier} ({tag}).')
                await message.channel.send(f'{message.author.mention} requested a tracker for {identifier} ({tag}). Coming right up...')

                tracker = await self._config.add_and_return_tracker(identifier, tag, command, channel_id)
                self.schedule_alert(channel_id, tracker.get_last_message(),
                                    urgent=False,
                                    wait_period_expired=False)
            elif self._config.is_subscribed(channel_id):
                # Already subscribed. Requesting update.
                print(f'User {message.author} requested update.')
                await message.channel.send(f'{message.author.mention} requested an update. Coming right up...')
                await self.schedule_alerts_for_channel(message.channel, command, trackers)
            elif len(message_tokens) == 1:
                # This is a new subscription.
                self._config.subscribe_channel(channel_id, channel_name)

                await message.channel.send('gm')
                await message.channel.send('You have subscribed to updates from the Antlion DeFi Bot.')
                await self.send_usages(message.channel)
                await message.channel.send('For now, I will share the current trackers in this channel.')

                print(f'Subscribed to {channel_name} ({channel_id})')
                await self.schedule_alerts_for_channel(message.channel, command, trackers)

    # This loop periodically updates trackers. An alert is scheduled if the
    # update returned has_alert == True, or if the maximum wait period has
    # elapsed between datetime.now() and the tracker's last alert time.
    @tasks.loop(seconds=300)
    async def update_task(self):
        print(
            f'Running update task at {utils.display_time(datetime.now(timezone.utc))} UTC.')
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
                    if tracker.has_channel(channel_id):
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
        if buffer:
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
            print(
                f'Sending alert to {self._config.get_channel_name(alert.channel_id)} with the following message:')
            print(alert.message)
            await self.send_long_message(channel, alert.message)
            queue.task_done()

    @alert_task.before_loop
    async def before_alert_task(self):
        await self.wait_until_ready()


def main(argv):
    intents = discord.Intents.default()
    intents.members = True
    client = AntlionDeFiBot(config=FLAGS.config, intents=intents)
    client.run(client.get_token())


if __name__ == '__main__':
    app.run(main)
