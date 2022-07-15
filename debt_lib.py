# This library implements a DebtTracker object to query and alert on changes in
# debt positions for a specified wallet address.
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
from utils import fetch_url
import asyncio
import csv
import io
import json
import re
import utils

DEBANK_PROTOCOLS_FMT = 'https://openapi.debank.com/v1/user/complex_protocol_list?id={address}'

SAVEFILE_FIELDS = ['time', 'address', 'tag',
                   'total_assets', 'total_debt', 'individual_debts']
LARGE_OVERALL_CHANGE = 1000000  # 1 million USD
LARGE_INDIVIDUAL_CHANGE = 100000  # 100,000 USD
LARGE_LTV_CHANGE = 0.05
MIN_AMOUNT = 100
MESSAGE_DELIMITER = '================='


class DebtPosition(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            if key == 'time':
                self.time = value
            elif key == 'address':
                self.address = value
            elif key == 'tag':
                self.tag = value
            elif key == 'total_assets':
                self.total_assets = value
            elif key == 'total_debt':
                self.total_debt = value
            elif key == 'individual_debts':
                self.individual_debts = value
            elif key == 'csv_row':
                self.from_csv_row(value)
                break
            else:
                logging.fatal('Ineligible key: ' + key)

    def from_csv_row(self, dict_in: dict):
        self.time = utils.parse_storage_time(dict_in['time'])
        self.address = dict_in['address']
        self.tag = dict_in['tag']
        self.total_assets = float(dict_in['total_assets'])
        self.total_debt = float(dict_in['total_debt'])
        self.individual_debts = json.loads(dict_in['individual_debts'])

    def to_csv_row(self) -> dict:
        result = {}
        result['time'] = utils.format_storage_time(self.time)
        result['address'] = self.address
        result['tag'] = self.tag
        result['total_assets'] = self.total_assets
        result['total_debt'] = self.total_debt
        result['individual_debts'] = json.dumps(self.individual_debts)
        return result


def _compute_total_debt(individual_debts: dict) -> float:
    return sum(debt['usd'] for _, debt in individual_debts.items())


# Parses protocol balance json for debts.
# Args:
#   protocol: Json for the protocol's balance.
#   debt_position: Modifiable DebtPosition that will be updated with the results
#       of parsing this protocol.
def _parse_protocol_balance(protocol: dict, debt_position: DebtPosition):
    for item in protocol['portfolio_item_list']:
        if 'borrow_token_list' not in item['detail']:
            # Skip non-debt positions.
            continue

        debt_usd = item['stats']['debt_usd_value']
        asset_usd = item['stats']['asset_usd_value']
        if not asset_usd:
            # Skip positions without assets as collateral.
            continue

        supply_token_symbols = [token['symbol']
                                for token in item['detail']['supply_token_list']]
        for borrow_token in item['detail']['borrow_token_list']:
            key = f'''{protocol['name']} ({protocol['chain']}), supply {"/".join(supply_token_symbols)}, borrow {borrow_token['symbol']}'''
            price = borrow_token['price']
            amount = borrow_token['amount']
            individual_debt_usd = amount * price
            debt_position.individual_debts[key] = {
                'usd': individual_debt_usd,
                'tokens': amount,
                'symbol': borrow_token['symbol'],
                'price': price,
                'token_ltv': individual_debt_usd / (asset_usd + 1e-6),
                'position_ltv': debt_usd / (asset_usd + 1e-6),
                'asset_usd': asset_usd
            }
        debt_position.total_assets += asset_usd
        debt_position.total_debt += debt_usd


async def _query_new_debts_debank(address: str, tag: Optional[str]) -> DebtPosition:
    protocols_response = await fetch_url(
        DEBANK_PROTOCOLS_FMT.format(address=address)
    )
    protocols = json.loads(protocols_response)

    debt_position = DebtPosition(time=datetime.now(timezone.utc),
                                 address=address,
                                 tag=tag,
                                 total_assets=0,
                                 total_debt=0,
                                 individual_debts={})
    for protocol in protocols:
        logging.debug(f'{json.dumps(protocols, indent=4)}')
        _parse_protocol_balance(protocol, debt_position)

    return debt_position


def _print_debts(debts: DebtPosition, output: io.StringIO):
    for name, value in sorted(debts.individual_debts.items(), key=lambda x: -x[1]['usd']):
        if value['usd'] < MIN_AMOUNT or value['asset_usd'] < MIN_AMOUNT:
            # Skip positions with trivial assets or debts.
            continue
        display_name = re.sub(r', borrow.*', '', name)
        print(f'''{value['tokens']:14,.2f} {value['symbol']:<7s} ({value['position_ltv'] * 100:5.1f}% LTV) - {display_name}''', file=output)

    print('-----------------', file=output)
    total_ltv = debts.total_debt / (debts.total_assets + 1e-6)
    print(f'{debts.total_debt:14,.2f} USD Total Debt ({total_ltv * 100:5.1f}% LTV)\n', file=output)
    return


def _print_short_debts(debts: DebtPosition, output: io.StringIO):
    print(f'Total Debt: {debts.total_debt:,.2f} USD\n', file=output)
    return


def _get_savefile(address: str, tag: Optional[str]) -> str:
    filename = '-'.join([address, tag]) if tag else address
    return f'{filename}.csv'


def _query_prev_debts(savefile: str) -> Optional[DebtPosition]:
    last_row = None
    with open(savefile, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            last_row = row

    return DebtPosition(csv_row=last_row) if last_row else None


# Iterates through individual debt positions, making sure each position is
# unique (so if a position exists in both previous and current debts, then it is
# only returned once).
#
# Returns:
#   name: str of individual debt position name.
#   prev_debt: dict of the individual debt position's previous values.
#   curr_debt: dict of the individual debt position's current values.
def _iterate_individual_debts(prev_debts: DebtPosition, curr_debts: DebtPosition):
    # Loop through debts in the current debts position.
    for curr_name, curr_value in sorted(curr_debts.individual_debts.items(), key=lambda x: -x[1]['usd']):
        if curr_name in prev_debts.individual_debts:
            prev_value = prev_debts.individual_debts[curr_name]
        else:
            prev_value = None
        yield curr_name, prev_value, curr_value

    # Check debts in prev_debts that don't exist in debts.
    for prev_name, prev_value in sorted(prev_debts.individual_debts.items(), key=lambda x: -x[1]['usd']):
        if prev_name in curr_debts.individual_debts:
            continue
        yield prev_name, prev_value, None


# Summarizes the differences between the previous and current values of a debt
# position.
#
# Fields:
#   * change_usd: Change in USD value for the position.
#   * change_tokens: Change in token count for the position.
#   * prev_ltv: Previous LTV of the individual position.
#   * curr_ltv: Current LTV of the individual position.
#   * symbol: Name of the token being borrowed.
#   * display_name: Name of the position for display purposes
#       (excludes borrowed token name).
class DebtDiff(object):
    def __init__(self,
                 change_usd,
                 change_tokens,
                 prev_ltv,
                 curr_ltv,
                 symbol,
                 display_name):
        self.change_usd = change_usd
        self.change_tokens = change_tokens
        self.prev_ltv = prev_ltv
        self.curr_ltv = curr_ltv
        self.symbol = symbol
        self.display_name = display_name


# This iterator yields summary stats on the _differences_ between individual
# debt positions in the prev_debts and debts DebtPositions.
#
# Yields:
#   * DebtDiff object.
def _iterate_individual_diffs(prev_debts: DebtPosition,
                              debts: DebtPosition):
    for name, prev_debt, curr_debt in _iterate_individual_debts(prev_debts, debts):
        prev_tokens = prev_debt['tokens'] if prev_debt else 0
        curr_tokens = curr_debt['tokens'] if curr_debt else 0
        price = curr_debt['price'] if curr_debt else prev_debt['price']
        prev_debt_usd = price * prev_tokens
        curr_debt_usd = price * curr_tokens
        symbol = curr_debt['symbol'] if curr_debt else prev_debt['symbol']
        prev_ltv = prev_debt['position_ltv'] if prev_debt else 0
        curr_ltv = curr_debt['position_ltv'] if curr_debt else 0
        prev_asset_usd = prev_debt['asset_usd'] if prev_debt else 0
        curr_asset_usd = curr_debt['asset_usd'] if curr_debt else 0

        # Skip comparing this individual position if the USD value of the assets
        # (or the debts) in both prev_debts and debts.
        if ((prev_debt_usd < MIN_AMOUNT and
             curr_debt_usd < MIN_AMOUNT) or
            (prev_asset_usd < MIN_AMOUNT and
             curr_asset_usd < MIN_AMOUNT)):
            continue

        change_usd = curr_debt_usd - prev_debt_usd
        change_tokens = curr_tokens - prev_tokens
        # Because it's possible to borrow different kinds of tokens from a given
        # collateral supply, we want the display name to list just the platform
        # and the collateral (no need to list the tokens borrowed, since that
        # will already be displayed elsewhere in the output).
        display_name = re.sub(r', borrow.*', '', name)

        # Yield the values for this iterator.
        yield DebtDiff(
            change_usd=change_usd,
            change_tokens=change_tokens,
            prev_ltv=prev_ltv,
            curr_ltv=curr_ltv,
            symbol=symbol,
            display_name=display_name)


def _get_debt_change(prev_debts: DebtPosition, debts: DebtPosition) -> float:
    total_change_usd = 0
    for diff in _iterate_individual_diffs(prev_debts, debts):
        total_change_usd += diff.change_usd
    return total_change_usd


def _get_relative_debt_change(prev_debts: DebtPosition,
                              debts: DebtPosition) -> float:
    return _get_debt_change(prev_debts, debts) / (prev_debts.total_debt + 1e-6)


# Tests for whether an alert has occured.
#
# Returns:
#   bool: Whether an alert should be raised.
#   str: Contents of the alert message.
def _get_alert_message(prev_debts: DebtPosition, debts: DebtPosition, ignorable_debts: List[str]) -> Tuple[bool, str]:
    if not prev_debts:
        return True, 'Starting a new debt log.'

    overall_change = _get_debt_change(prev_debts, debts)
    overall_relative_change = _get_relative_debt_change(prev_debts, debts)

    # Check for large total increases or decreases.
    if overall_change >= LARGE_OVERALL_CHANGE:
        return True, f'💳🤝💵 Significant INCREASE in debt ({overall_change:+,.2f} USD, {overall_relative_change * 100:+.1f}%). Bullish.'
    if overall_change <= -LARGE_OVERALL_CHANGE:
        return True, f'🚨🚨🚨🚨🚨 ALERT: Significant REDUCTION in debt ({overall_change:+,.2f} USD, {overall_relative_change * 100:+.1f}%). We gonna get rekt?'

    # Check for singificant individual changes.
    large_individual_debt_increase = False
    large_individual_debt_decrease = False
    large_ltv_increase = False
    large_ltv_decrease = False
    for diff in _iterate_individual_diffs(prev_debts, debts):
        if diff.display_name in ignorable_debts:
            print(
                f'Ignored alert check for this position: {diff.display_name}')
            continue

        if diff.change_usd >= LARGE_INDIVIDUAL_CHANGE:
            large_individual_debt_increase = True
        if diff.change_usd <= -LARGE_INDIVIDUAL_CHANGE:
            large_individual_debt_decrease = True
        if diff.curr_ltv - diff.prev_ltv >= LARGE_LTV_CHANGE:
            large_ltv_increase = True
        if diff.curr_ltv - diff.prev_ltv <= -LARGE_LTV_CHANGE:
            large_ltv_decrease = True

    if large_individual_debt_increase and not large_individual_debt_decrease:
        return True, f'💵 Significant increase in individual debt position.'
    elif large_individual_debt_decrease and not large_individual_debt_increase:
        return True, f'🚨 Significant reduction in individual debt position.'
    elif large_individual_debt_increase and large_individual_debt_decrease:
        return True, f'👀 Significant churn in individual debt positions.'

    # If no alerts triggered up to this point, check for LTV changes.
    if large_ltv_increase and not large_ltv_decrease:
        return True, f'👀 Significant LTV increase in individual positions.'
    elif large_ltv_decrease and not large_ltv_increase:
        return True, f'👀 Significant LTV decrease in individual positions.'
    elif large_ltv_increase and large_ltv_decrease:
        return True, f'👀 Significant LTV churn in individual positions.'

    return False, ''


def _print_debt_comparison(prev_debts: DebtPosition, debts: DebtPosition, ignorable_debts: List[str], output: io.StringIO):
    if not prev_debts:
        return

    assert prev_debts.address == debts.address
    assert prev_debts.tag == debts.tag
    assert prev_debts.time <= debts.time

    overall_change = _get_debt_change(prev_debts, debts)
    overall_relative_change = _get_relative_debt_change(prev_debts, debts)
    time_diff = debts.time - prev_debts.time
    print(
        f'Change: {overall_change:+,.2f} USD ({overall_relative_change * 100:+.1f}%)', end='', file=output)
    print(
        f' compared to {utils.display_time(prev_debts.time)} UTC ({utils.format_timedelta(time_diff)} ago).', file=output)

    # Loop through individual debt positions.
    printed_notable_change_header = False
    for diff in _iterate_individual_diffs(prev_debts, debts):
        if diff.display_name in ignorable_debts:
            print(
                f'Ignored debt comparison for this position: {diff.display_name}')
            continue
        if (abs(diff.change_usd) >= LARGE_INDIVIDUAL_CHANGE or
                abs(diff.curr_ltv - diff.prev_ltv) >= LARGE_LTV_CHANGE):
            if not printed_notable_change_header:
                print('\nNotable changes:', file=output)
                printed_notable_change_header = True
            print(f'''{diff.change_tokens:+14,.2f} {diff.symbol:<7s} (LTV: {diff.prev_ltv * 100:5.1f}% --> {diff.curr_ltv * 100:5.1f}%) - {diff.display_name}''', file=output)


def _write_debts(debts: DebtPosition, savefile: str):
    with open(savefile, 'a', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, SAVEFILE_FIELDS)
        writer.writerow(debts.to_csv_row())


def _print_title(wallet_name: str, timestamp: datetime, output: io.StringIO):
    print(
        f'Debt Positions for {wallet_name} at {utils.display_time(timestamp)} UTC', file=output)


class DebtTracker(object):
    def __init__(self,
                 address: str,
                 tag: str,
                 subscribe_command: str,
                 last_alert_time: Optional[str],
                 channels: Optional[List[int]],
                 ignorable_debts: Optional[List[str]]):
        # Address of the wallet being tracked.
        self._address = address
        # A human-readable tag to associate with the address.
        self._tag = tag
        # Subscribe command for the bot invoking this tracker.
        self._subscribe_command = subscribe_command
        # Path for saving the data for this tracker.
        self._savefile = _get_savefile(address, tag)
        # A datetime representing the last time the state of the tracker was
        # updated. This is set after running the get_last_update() call.
        self._last_update_time = utils.MIN_TIME
        # A list of channel IDs subscribed to this tracker.
        self._channels = channels if channels else []
        # List of debt positions for which we ignore alerts.
        self._ignorable_debts = ignorable_debts if ignorable_debts else []

        # The last time the tracker raised an alert. This is set internally by
        # the sync_last_alert_time() call, as well as externally during
        # construction.
        if last_alert_time:
            self._last_alert_time = utils.parse_storage_time(last_alert_time)
        else:
            self._last_alert_time = utils.MIN_TIME

        # The contents of the latest message that was produced from running
        # update() or _get_last_update().
        self._last_message = f'Tracker for {self.get_name()} just initialized.'

        # Creates a new savefile if needed.
        if not Path(self._savefile).is_file():
            with open(self._savefile, 'w', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, SAVEFILE_FIELDS)
                writer.writeheader()

        # Load the latest saved debt data.
        self._get_last_update()

    def get_name(self) -> str:
        name = self._address
        if self._tag:
            name += f' (**{self._tag}**)'
        return name

    def get_address(self) -> str:
        return self._address

    def get_identifier(self) -> str:
        return self.get_address()

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

    def get_ignorable_debts(self) -> List[str]:
        return self._ignorable_debts

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

        debts = _query_prev_debts(savefile)

        if not debts:
            return False, f'No debts recorded yet for {self.get_name()}.'

        output = io.StringIO()
        _print_title(self.get_name(), debts.time, output)
        print('```', file=output)
        _print_debts(debts, output)
        print('```' + MESSAGE_DELIMITER, file=output)
        message = output.getvalue()

        # Update timestamps and messages.
        self._last_update_time = debts.time
        self._last_message = message
        if self._last_alert_time == utils.MIN_TIME:
            self.sync_last_alert_time()

        return False, message

    # Performs a new query for debt positions and stores the results as a long
    # message. The return value is the shortened version of the message. The
    # _last_update_time is updated to the current debt position time. The
    # _last_alert_time is updated to the _last_update_time if this debt query
    # raised an alert.
    async def update(self) -> Tuple[bool, str]:
        address = self._address
        tag = self._tag
        savefile = self._savefile
        ignorable_debts = self._ignorable_debts

        debts = await _query_new_debts_debank(address, tag)
        prev_debts = _query_prev_debts(savefile)
        has_alert, alert_message = _get_alert_message(
            prev_debts, debts, ignorable_debts)

        # Create long message.
        output = io.StringIO()
        if has_alert:
            print(alert_message, file=output)
        _print_title(self.get_name(), debts.time, output)
        print('```', file=output)
        _print_debts(debts, output)
        _print_debt_comparison(prev_debts, debts, ignorable_debts, output)
        print('```' + MESSAGE_DELIMITER, file=output)
        message = output.getvalue()

        # Create short message.
        short_output = io.StringIO()
        if has_alert:
            print(alert_message, file=short_output)
        _print_title(self.get_name(), debts.time, short_output)
        print('```', file=short_output)
        _print_short_debts(debts, short_output)
        _print_debt_comparison(
            prev_debts, debts, ignorable_debts, short_output)
        print('```' + MESSAGE_DELIMITER, file=short_output, end='')
        print(f'(Type `!{self._subscribe_command}` to get full breakdown.)',
              file=short_output, end='')
        print(MESSAGE_DELIMITER, file=short_output)
        short_message = short_output.getvalue()

        # Update timestamps and messages.
        self._last_update_time = debts.time
        self._last_message = message
        if has_alert:
            self.sync_last_alert_time()

        _write_debts(debts, savefile)

        return has_alert, short_message
