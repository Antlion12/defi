#!/usr/bin/python3

# This library implements a LinkTracker object to query and alert on significant
# price movements in LINK.
#
# Requires Python version >= 3.6.
# This library queries the Zapper API.

import utils
import json
import io
import csv
import asyncio
from typing import Tuple
from typing import Optional
from typing import List
from utils import fetch_url
from utils import display_time
from pathlib import Path
from datetime import timezone
from datetime import timedelta
from datetime import datetime
from absl import logging


LINK_NAME = 'chainlink'
ETH_NAME = 'ethereum'
COINGECKO_PRICE_FMT = 'https://api.coingecko.com/api/v3/coins/{token_name}/market_chart?vs_currency=usd&days=1&interval=minute'

SAVEFILE_FIELDS = ['time', 'link_prev', 'link_now', 'link_change',
                   'eth_prev', 'eth_now', 'eth_change', 'link_vs_eth']
ALERT_THRESHOLD = 0.03


class Prices(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            if key == 'time':
                self.time = value
            elif key == 'link_prev':
                self.link_prev = value
            elif key == 'link_now':
                self.link_now = value
            elif key == 'link_change':
                self.link_change = value
            elif key == 'eth_prev':
                self.eth_prev = value
            elif key == 'eth_now':
                self.eth_now = value
            elif key == 'eth_change':
                self.eth_change = value
            elif key == 'link_vs_eth':
                self.link_vs_eth = value
            elif key == 'csv_row':
                self.from_csv_row(value)
                break
            else:
                logging.fatal('Ineligible key: ' + key)

    def from_csv_row(self, dict_in: dict):
        self.time = utils.parse_storage_time(dict_in['time'])
        self.link_prev = float(dict_in['link_prev'])
        self.link_now = float(dict_in['link_now'])
        self.link_change = float(dict_in['link_change'])
        self.eth_prev = float(dict_in['eth_prev'])
        self.eth_now = float(dict_in['eth_now'])
        self.eth_change = float(dict_in['eth_change'])
        self.link_vs_eth = float(dict_in['link_vs_eth'])

    def to_csv_row(self) -> dict:
        result = {}
        result['time'] = utils.format_storage_time(self.time)
        result['link_prev'] = self.link_prev
        result['link_now'] = self.link_now
        result['link_change'] = self.link_change
        result['eth_prev'] = self.eth_prev
        result['eth_now'] = self.eth_now
        result['eth_change'] = self.eth_change
        result['link_vs_eth'] = self.link_vs_eth
        return result


def _get_savefile() -> str:
    filename = f'linkpump'
    return f'{filename}.csv'


def _query_prev_prices(savefile: str) -> Optional[Prices]:
    last_row = None
    with open(savefile, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            last_row = row

    return Prices(csv_row=last_row) if last_row else None


def _write_prices(prices: Prices, savefile: str):
    with open(savefile, 'a', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, SAVEFILE_FIELDS)
        writer.writerow(prices.to_csv_row())


async def _get_prices() -> Prices:
    link_response = await fetch_url(COINGECKO_PRICE_FMT.format(token_name=LINK_NAME))

    link_response = json.loads(link_response)
    link_prev = link_response['prices'][0][1]
    link_now = link_response['prices'][-1][1]
    link_change = (link_now - link_prev) / link_prev

    eth_response = await fetch_url(COINGECKO_PRICE_FMT.format(token_name=ETH_NAME))
    eth_response = json.loads(eth_response)
    eth_prev = eth_response['prices'][0][1]
    eth_now = eth_response['prices'][-1][1]
    eth_change = (eth_now - eth_prev) / eth_prev

    link_vs_eth = link_change - eth_change

    return Prices(time=datetime.now(timezone.utc),
                  link_prev=link_prev,
                  link_now=link_now,
                  link_change=link_change,
                  eth_prev=eth_prev,
                  eth_now=eth_now,
                  eth_change=eth_change,
                  link_vs_eth=link_vs_eth)


def _prepare_message(prices: Prices, last_alert_time: datetime) -> Tuple[bool, str]:
    output = io.StringIO()
    print(f'LINK vs ETH: {prices.link_vs_eth * 100:+.2f}%', file=output)
    print('```', file=output)
    print(f'--24 HR change--', file=output)
    print(f'LINK: ${prices.link_prev:9.3f} -> ${prices.link_now:9.3f} ({prices.link_change*100:+.2f}%)', file=output)
    print(
        f'ETH : ${prices.eth_prev:9.3f} -> ${prices.eth_now:9.3f} ({prices.eth_change*100:+.2f}%)', file=output)
    print(f'Last checked: {display_time(prices.time)} UTC', file=output)
    print('```', file=output)

    message = output.getvalue()
    # At least 3 hours elapsed since last alert.
    has_alert = ((prices.link_vs_eth >= ALERT_THRESHOLD) and
                 (prices.link_change >= 0))
    if has_alert:
        message = '🚨⛓️ LINK IS PUMPING. Will we get a dumping? ' + message

    return has_alert, message


class LinkTracker(object):
    def __init__(self,
                 identifier: str,
                 tag: str,
                 subscribe_command: str,
                 last_alert_time: Optional[str],
                 channels: Optional[List[int]]):
        # Identifier and tag info.
        self._identifier = identifier
        self._tag = tag
        # Subscribe command for the bot invoking this tracker.
        self._subscribe_command = subscribe_command
        # Path for saving the data for this tracker.
        self._savefile = _get_savefile()
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
        return self._identifier

    def get_identifier(self) -> str:
        return self.get_name()

    def get_tag(self) -> str:
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

        prices = _query_prev_prices(savefile)

        if not prices:
            return False, f'No prices available for {self.get_name()}.'

        _, message = _prepare_message(prices, self._last_alert_time)

        # Update timestamps and messages.
        self._last_update_time = prices.time
        self._last_message = message
        if self._last_alert_time == utils.MIN_TIME:
            self.sync_last_alert_time()

        return False, message

    # Queries the LINK's and ETH's price changes over the last 24 hours, then
    # stores the result as a message. The _last_update_time is updated to the
    # current time. The _last_alert_time is updated to the _last_update_time if
    # this update raised an alert.
    async def update(self) -> Tuple[bool, str]:
        savefile = self._savefile

        # Get current prices.
        prices = await _get_prices()

        has_alert, message = _prepare_message(prices, self._last_alert_time)

        # Update timestamps and messages.
        self._last_update_time = prices.time
        self._last_message = message
        if has_alert:
            self.sync_last_alert_time()

        _write_prices(prices, savefile)

        return has_alert, message
