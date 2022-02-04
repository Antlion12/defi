# This library implements a NameTracker object to query and alert on changes in
# names for a specified Discord user.
#
# Requires Python version >= 3.6.
# This library queries the DeBank API.
# Creates a savefile CSV called <address>[-<tag>].csv to save results of recent
# queries.

from absl import logging
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import List
from typing import Optional
from typing import Tuple
import asyncio
import csv
import discord
import utils

SAVEFILE_FIELDS = ['time', 'user_id', 'tag', 'name']


class Name(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            if key == 'time':
                self.time = value
            elif key == 'user_id':
                self.user_id = value
            elif key == 'tag':
                self.tag = value
            elif key == 'name':
                self.name = value
            elif key == 'csv_row':
                self.from_csv_row(value)
                break
            else:
                logging.fatal('Ineligible key: ' + key)

    def from_csv_row(self, dict_in: dict):
        self.time = utils.parse_storage_time(dict_in['time'])
        self.user_id = dict_in['user_id']
        self.tag = dict_in['tag']
        self.name = dict_in['name']

    def to_csv_row(self) -> dict:
        result = {}
        result['time'] = utils.format_storage_time(self.time)
        result['user_id'] = self.user_id
        result['tag'] = self.tag
        result['name'] = self.name
        return result


def _get_savefile(user_id: int, tag: str) -> str:
    filename = f'nametracker-{user_id}-{tag}'
    return f'{filename}.csv'


def _query_prev_name(savefile: str) -> Optional[Name]:
    last_row = None
    with open(savefile, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            last_row = row

    return Name(csv_row=last_row) if last_row else None


def _write_name(name: Name, savefile: str):
    with open(savefile, 'a', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, SAVEFILE_FIELDS)
        writer.writerow(name.to_csv_row())


class NameTracker(object):
    def __init__(self,
                 client: discord.Client,
                 user_id: int,
                 tag: str,
                 subscribe_command: str,
                 last_alert_time: Optional[str],
                 channels: Optional[List[int]]):
        # Discord client for querying user info.
        self._client = client
        # Discord ID of the user being tracked.
        self._user_id = user_id
        # A human-readable tag to associate with the user.
        self._tag = tag
        # Subscribe command for the bot invoking this tracker.
        self._subscribe_command = subscribe_command
        # Path for saving the data for this tracker.
        self._savefile = _get_savefile(user_id, tag)
        # A datetime representing the last time the state of the tracker was
        # updated. This is set after running the get_last_update() call.
        self._last_update_time = utils.MIN_TIME
        # A list of channel IDs subscribed to this tracker.
        self._channels = channels if channels else []

        # The last time the tracker raised an alert. This is set internally by
        # the sync_last_alert_time() call, as well as externally during
        # construction.
        if last_alert_time:
            self._last_alert_time = utils.parse_storage_time(last_alert_time)
        else:
            self._last_alert_time = utils.MIN_TIME

        # The contents of the latest message that was produced from running
        # update() or _get_last_update().
        self._last_message = f'{self.get_name()} tracker just initialized.'

        # Creates a new savefile if needed.
        if not Path(self._savefile).is_file():
            with open(self._savefile, 'w', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, SAVEFILE_FIELDS)
                writer.writeheader()

        # Load the latest saved debt data.
        self._get_last_update()

    def get_name(self) -> str:
        return f'{self._tag}'

    def get_user_id(self) -> str:
        return self._user_id

    def get_identifier(self) -> str:
        return self.get_user_id()

    def get_tag(self) -> Optional[str]:
        return self._tag

    def get_last_update_time(self) -> datetime:
        return self._last_update_time

    def get_last_alert_time(self) -> datetime:
        return self._last_alert_time

    def get_last_message(self) -> str:
        return self._last_message

    def get_channels(self) -> List[int]:
        return self._channels

    def has_channel(self, channel_id: int) -> bool:
        return channel_id in self._channels

    def add_channel(self, channel_id: int):
        self._channels.append(channel_id)

    def get_subscribe_command(self) -> str:
        return self._subscribe_command

    # Sets the last alert time to the last update time. This is useful when
    # there is some caller that is using a different criteria for triggering an
    # alert.
    def sync_last_alert_time(self):
        self._last_alert_time = self._last_update_time

    # An internal function that fetches the current state of the tracker without
    # performing new queries.
    def _get_last_update(self) -> Tuple[bool, str]:
        savefile = self._savefile

        name = _query_prev_name(savefile)

        if not name:
            return False, f'No name inferred for {self.get_name()}.'

        message = f'**{self.get_name()}** tracker: Current name is **{name.name}** (as of {utils.display_time(name.time)} UTC).'

        # Update timestamps and messages.
        self._last_update_time = name.time
        self._last_message = message
        if self._last_alert_time == utils.MIN_TIME:
            self.sync_last_alert_time()

        return False, message

    # Queries the user's current name and stores the result as a message. The
    # _last_update_time is updated to the current time. The
    # _last_alert_time is updated to the _last_update_time if this update raised
    # an alert.
    async def update(self) -> Tuple[bool, str]:
        user_id = self._user_id
        tag = self._tag
        savefile = self._savefile

        # Get current username.
        member = None
        for channel_id in self._channels:
            channel = self._client.get_channel(channel_id)
            member = await channel.guild.fetch_member(user_id)
            print(f'Found member: {member}')
            if member:
                break

        if not member:
            print(f'Unable to find user: {user_id}')
            return False, f'Unable to find user {user_id}'
        if member.nick:
            name_string = f'{member.nick} ({member.name}#{member.discriminator})'
        else:
            name_string = f'{member.name}#{member.discriminator}'
        name = Name(time=datetime.now(timezone.utc),
                    user_id=user_id,
                    tag=tag,
                    name=name_string)

        # Get previous username.
        prev_name = _query_prev_name(savefile)

        message = f'**{self.get_name()}** tracker: '
        if not prev_name:
            has_alert = True
            message += f'Current name is **{name.name}**'
        elif prev_name.name != name.name:
            has_alert = True
            message += f'The artist formerly known as **{prev_name.name}** is now known as **{name.name}**'
        else:
            has_alert = False
            message += f'Current name for is still **{name.name}**'
        message += f' (as of {utils.display_time(name.time)} UTC).'

        # Update timestamps and messages.
        self._last_update_time = name.time
        self._last_message = message
        if has_alert:
            self.sync_last_alert_time()

        _write_name(name, savefile)

        return has_alert, message
