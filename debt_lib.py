# This library implements a DebtTracker object to query and alert on changes in
# debt positions for a specified wallet address.
#
# Requires Python version >= 3.6.
# This library queries the Zapper API.
# Creates a savefile CSV called <address>[-<tag>].csv to save results of recent
# queries.

from absl import flags
from absl import logging
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Optional
from typing import Tuple
from utils import fetch_url
import asyncio
import csv
import enum
import io
import json
import utils

API_KEY = '96e0cc51-a62e-42ca-acee-910ea7d2a241'
ZAPPER_BALANCE_FMT = 'https://api.zapper.fi/v1/balances?api_key={api_key}&addresses[]={address}'
ZAPPER_SUPPORTED_PROTOS_FMT = 'https://api.zapper.fi/v1/protocols/balances/supported?api_key={api_key}&addresses[]={address}'
ZAPPER_APP_BALANCE_FMT = 'https://api.zapper.fi/v1/protocols/{app}/balances?network={network}&api_key={api_key}&addresses[]={address}'
SAVEFILE_FIELDS = ['time', 'address', 'tag', 'total_debt', 'individual_debts']
LARGE_RELATIVE_CHANGE = 0.02
LARGE_ABSOLUTE_CHANGE = 1000000


class DebtPosition(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            if key == 'time':
                self.time = value
            elif key == 'address':
                self.address = value
            elif key == 'tag':
                self.tag = value
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
        self.total_debt = float(dict_in['total_debt'])
        self.individual_debts = json.loads(dict_in['individual_debts'])

    def to_csv_row(self) -> dict:
        result = {}
        result['time'] = utils.format_storage_time(self.time)
        result['address'] = self.address
        result['tag'] = self.tag
        result['total_debt'] = self.total_debt
        result['individual_debts'] = json.dumps(self.individual_debts)
        return result


# Constructs a key for the debts dictionary.
def _make_key(asset: dict, token: Optional[dict]) -> str:
    assert asset

    # Collect fields for constructing key.
    terms = []
    if token:
        terms.append(token['network'])
    else:
        terms.append(asset['network'])
    terms.append(asset['appId'])
    if 'label' in asset:
        terms.append(asset['label'])
    elif 'symbol' in asset:
        terms.append(asset['symbol'])

    key = ' / '.join(terms)

    return key

# Parses app_balance json for debts.
# Args:
#   app_balance: Json for the app's balance.
#   address: Wallet address for which we are scraping debt balances.
#   debts: An output dictionary for which we store debt balances (key being the
#       debt description, value being the debt value in tokens).


def _parse_app_balance(app_balance: dict, address: str, debts: DebtPosition):
    for product in app_balance[address]['products']:
        for asset in product['assets']:
            logging.debug(f'Found asset: {json.dumps(asset, indent=4)}')

            if 'tokens' not in asset:
                if asset['balanceUSD'] < 0:
                    logging.debug(
                        f'Found debt: {json.dumps(asset, indent=4)}')
                    # Found a debt position among asset tokens.
                    key = _make_key(asset, token=None)
                    debts[key] = {
                        'usd': -asset['balanceUSD'],
                        'tokens': asset['balance'],
                        'symbol': asset['symbol']
                    }
            else:
                for token in asset['tokens']:
                    if token['balanceUSD'] < 0:
                        logging.debug(
                            f'Found debt: {json.dumps(token, indent=4)}')
                        # Found a debt position among asset tokens.
                        key = _make_key(asset, token)
                        debts[key] = {
                            'usd': -token['balanceUSD'],
                            'tokens': token['balance'],
                            'symbol': token['symbol']
                        }


def _compute_total_debt(individual_debts: dict) -> float:
    return sum(debt['tokens'] for _, debt in individual_debts.items())


# Fetches balances for the address, looks for debts, stores the debts in a
# dictionary.
async def _query_new_debts(address: str, tag: Optional[str]) -> DebtPosition:
    response = await fetch_url(
        ZAPPER_BALANCE_FMT.format(api_key=API_KEY,
                                  address=address)
    )
    logging.debug('Query Balances Response:\n' + response)

    debts = {}
    for line in response.splitlines():
        if not line.startswith('data: '):
            continue
        _, content = line.split(' ', 1)
        if content == 'start' or content == 'end':
            continue

        app_balance = json.loads(content)['balances']
        _parse_app_balance(app_balance, address, debts)

    return DebtPosition(time=datetime.now(timezone.utc),
                        address=address,
                        tag=tag,
                        total_debt=_compute_total_debt(debts),
                        individual_debts=debts)


class App(object):
    def __init__(self, network_in, app_in):
        self.network = network_in
        self.app = app_in


async def _query_new_debts2(address: str, tag: Optional[str]) -> DebtPosition:
    # Get app IDs.
    protocols_response = await fetch_url(
        ZAPPER_SUPPORTED_PROTOS_FMT.format(api_key=API_KEY, address=address)
    )
    protocols = json.loads(protocols_response)

    app_ids = []
    for protocol in protocols:
        for app in protocol['apps']:
            if app['appId'] != 'tokens':
                app_ids.append(App(protocol['network'], app['appId']))

    # Fetch balances per app.
    app_balance_responses = []
    fetch_routines = []
    for app_id in app_ids:
        logging.debug(f'Querying {app_id.network}/{app_id.app}')
        fetch_routines.append(fetch_url(
            ZAPPER_APP_BALANCE_FMT.format(api_key=API_KEY,
                                          address=address,
                                          network=app_id.network,
                                          app=app_id.app)
        ))
    app_balance_responses = await asyncio.gather(*fetch_routines)

    # Parse the debt balances from each app.
    debts = {}
    for app_balance_response in app_balance_responses:
        app_balance = json.loads(app_balance_response)
        _parse_app_balance(app_balance, address, debts)

    return DebtPosition(time=datetime.now(timezone.utc),
                        address=address,
                        tag=tag,
                        total_debt=_compute_total_debt(debts),
                        individual_debts=debts)


def _print_debts(debts: DebtPosition, output):
    for name, value in sorted(debts.individual_debts.items(), key=lambda x: -x[1]['tokens']):
        print(
            f'''{value['tokens']:17,.2f} {value['symbol']:<5s} -- {name}''', file=output)

    print('-----------------', file=output)
    print(f'{debts.total_debt:17,.2f} USD -- Total Debt', file=output)
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


def _get_debt_change(prev_debts: DebtPosition, debts: DebtPosition) -> float:
    return debts.total_debt - prev_debts.total_debt


def _get_relative_debt_change(prev_debts: DebtPosition,
                              debts: DebtPosition) -> float:
    return _get_debt_change(prev_debts, debts) / (prev_debts.total_debt + 1e-6)


# Tests for whether an alert has occured.
#
# Returns:
#   bool: Whether an alert should be raised.
#   str: Contents of the alert message.
def _get_alert_message(prev_debts: DebtPosition,
                       debts: DebtPosition) -> Tuple[bool, str]:
    if not prev_debts:
        return True, 'Starting a new debt log.'

    change = _get_debt_change(prev_debts, debts)
    relative_change = _get_relative_debt_change(prev_debts, debts)
    time_diff = debts.time - prev_debts.time

    if change >= LARGE_ABSOLUTE_CHANGE:
        return True, f'💳🤝💵 Significant INCREASE in debt (at least {LARGE_ABSOLUTE_CHANGE:,} USD). Bullish.'
    elif relative_change >= LARGE_RELATIVE_CHANGE:
        return True, f'💳🤝💵 Significant INCREASE in debt (at least {LARGE_RELATIVE_CHANGE * 100:.2f})%. Bullish.'
    elif change <= -LARGE_ABSOLUTE_CHANGE:
        return True, f'🚨🚨🚨🚨🚨 ALERT: Significant REDUCTION in debt (at least {LARGE_ABSOLUTE_CHANGE:,} USD). We gonna get rekt?'
    elif relative_change <= -LARGE_RELATIVE_CHANGE:
        return True, f'🚨🚨🚨🚨🚨 ALERT: Significant REDUCTION in debt (at least {LARGE_RELATIVE_CHANGE * 100:.2f}%). We gonna get rekt?'

    return False, ''


def _print_debt_comparison(prev_debts: DebtPosition, debts: DebtPosition, output):
    if not prev_debts:
        return

    assert prev_debts.address == debts.address
    assert prev_debts.tag == debts.tag
    assert prev_debts.time <= debts.time

    change = _get_debt_change(prev_debts, debts)
    relative_change = _get_relative_debt_change(prev_debts, debts)
    time_diff = debts.time - prev_debts.time
    print(
        f'Change: {change:+,.2f} USD ({relative_change * 100:+.4f}%)', end='', file=output)
    print(
        f' compared to {utils.display_time(prev_debts.time)} UTC ({utils.format_timedelta(time_diff)} ago).', file=output)


def _write_debts(debts: DebtPosition, savefile: str):
    with open(savefile, 'a', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, SAVEFILE_FIELDS)
        writer.writerow(debts.to_csv_row())


class DebtTracker(object):
    def __init__(self,
                 address: str,
                 tag: Optional[str],
                 last_alert_time: Optional[str]):
        # Address of the wallet being tracked.
        self._address = address
        # A human-readable tag to associate with the address.
        self._tag = tag
        # Path for saving the data for this tracker.
        self._savefile = _get_savefile(address, tag)
        # A datetime representing the last time the state of the tracker was
        # updated. This is set after running the get_last_update() call.
        self._last_update_time = utils.MIN_TIME
        # The last time the tracker raised an alert. This is set internally by
        # the sync_last_alert_time() call, as well as externally during
        # construction.
        if last_alert_time:
            self._last_alert_time = utils.parse_storage_time(last_alert_time)
        else:
            self._last_alert_time = utils.MIN_TIME
        # The contents of the latest message that was produced from running
        # update() or _get_last_update().
        self._last_message = f'Track for {self.get_name()} just initialized.'

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
            name += f' ({self._tag})'
        return name

    def get_address(self) -> str:
        return self._address

    def get_tag(self) -> Optional[str]:
        return self._tag

    def get_last_update_time(self) -> datetime:
        return self._last_update_time

    def get_last_alert_time(self) -> datetime:
        return self._last_alert_time

    def get_last_message(self) -> str:
        return self._last_message

    # Sets the last alert time to the last update time. This is useful when
    # there is some caller that is using a different criteria for triggering an
    # alert.
    def sync_last_alert_time(self):
        self._last_alert_time = self._last_update_time

    # An internal function that fetches the current state of the tracker without
    # performing new queries.
    def _get_last_update(self) -> Tuple[bool, str]:
        address = self._address
        tag = self._tag
        savefile = self._savefile

        debts = _query_prev_debts(savefile)

        if not debts:
            return False, f'No debts recorded yet for {self.get_name()}.'

        output = io.StringIO()
        print(
            f'Debt Positions for {self.get_name()} at {utils.display_time(debts.time)} UTC', file=output)
        print('```', file=output)
        _print_debts(debts, output)
        print('```=================', file=output)
        message = output.getvalue()

        # Update timestamps and messages.
        self._last_update_time = debts.time
        self._last_message = message
        if self._last_alert_time == utils.MIN_TIME:
            self.sync_last_alert_time()

        return False, message

    async def update(self) -> Tuple[bool, str]:
        address = self._address
        tag = self._tag
        savefile = self._savefile

        debts = await _query_new_debts2(address, tag)
        prev_debts = _query_prev_debts(savefile)
        has_alert, alert_message = _get_alert_message(prev_debts, debts)

        output = io.StringIO()
        print(
            f'Debt Positions for {self.get_name()} at {utils.display_time(debts.time)} UTC', file=output)
        if has_alert:
            print(alert_message, file=output)

        print('```', file=output)
        _print_debts(debts, output)
        print('', file=output)
        _print_debt_comparison(prev_debts, debts, output)
        print('```=================', file=output)
        message = output.getvalue()

        # Update timestamps and messages.
        self._last_update_time = debts.time
        self._last_message = message
        if has_alert:
            self.sync_last_alert_time()

        _write_debts(debts, savefile)

        return has_alert, message
