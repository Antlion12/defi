# This library implements a DebtTracker object to query and alert on changes in
# debt positions for a specified wallet address.dd43d2c8f027d2d41 --tag=DeFiGod
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
import csv
import enum
import httpx
import io
import json

API_KEY = '96e0cc51-a62e-42ca-acee-910ea7d2a241'
ZAPPER_BALANCE_FMT = 'https://api.zapper.fi/v1/balances?api_key={api_key}&addresses[]={address}'
MAX_ATTEMPTS = 3
SAVEFILE_FIELDS = ['time', 'address', 'tag', 'total_debt', 'individual_debts']
LARGE_CHANGE_THRESHOLD = 0.05
TIME_STORAGE_FMT = '%Y-%m-%d %H:%M:%S%z'
TIME_DISPLAY_FMT = '%Y-%m-%d %H:%M:%S'


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
        self.time = datetime.strptime(dict_in['time'], TIME_STORAGE_FMT)
        self.address = dict_in['address']
        self.tag = dict_in['tag']
        self.total_debt = float(dict_in['total_debt'])
        self.individual_debts = json.loads(dict_in['individual_debts'])

    def to_csv_row(self) -> dict:
        result = {}
        result['time'] = self.time.strftime(TIME_STORAGE_FMT)
        result['address'] = self.address
        result['tag'] = self.tag
        result['total_debt'] = self.total_debt
        result['individual_debts'] = json.dumps(self.individual_debts)
        return result


async def _fetch_url(url: str) -> str:
    attempts = 0
    result = ''
    print(f'Fetching url: {url}')
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers={'accept': '*/*'}, timeout=60)
    return str(response.text)


# Constructs a key for the debts dictionary.
def _make_key(asset: dict, token: Optional[str]) -> str:
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

    if token:
        if 'label' in token:
            key += f" ({token['label']})"
        elif 'symbol' in token:
            key += f" ({token['symbol']})"

    return key

# Parses app_balance json for debts.
# Args:
#   app_balance: Json for the app's balance.
#   address: Wallet address for which we are scraping debt balances.
#   debts: An output dictionary for which we store debt balances (key being the
#       debt description, value being the debt value in tokens).


def _parse_app_balance(app_balance: dict, address: str, debts: DebtPosition):
    for product in app_balance['balances'][address]['products']:
        for asset in product['assets']:
            logging.debug(f'Found asset: {json.dumps(asset, indent=4)}')

            if 'tokens' not in asset:
                if asset['balanceUSD'] < 0:
                    logging.debug(
                        f'Found debt: {json.dumps(asset, indent=4)}')
                    # Found a debt position among asset tokens.
                    key = _make_key(asset)
                    debts[key] = asset['balance']
            else:
                for token in asset['tokens']:
                    if token['balanceUSD'] < 0:
                        logging.debug(
                            f'Found debt: {json.dumps(token, indent=4)}')
                        # Found a debt position among asset tokens.
                        key = _make_key(asset, token)
                        debts[key] = token['balance']


def _compute_total_debt(individual_debts: dict) -> float:
    return sum(debt for _, debt in individual_debts.items())


# Fetches balances for the address, looks for debts, stores the debts in a
# dictionary.
async def _query_new_debts(address: str, tag: Optional[str]) -> DebtPosition:
    response = await _fetch_url(
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

        app_balance = json.loads(content)
        _parse_app_balance(app_balance, address, debts)

    return DebtPosition(time=datetime.now(timezone.utc),
                        address=address,
                        tag=tag,
                        total_debt=_compute_total_debt(debts),
                        individual_debts=debts)


def _print_debts(debts: DebtPosition, output):
    for name, value in sorted(debts.individual_debts.items(), key=lambda x: -x[1]):
        print(f'{value:17,.2f} -- {name}', file=output)

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

    if relative_change >= LARGE_CHANGE_THRESHOLD:
        return True, f'💳🤝💵 Significant INCREASE in debt. Bullish.'
    elif relative_change <= -LARGE_CHANGE_THRESHOLD:
        return True, f'🚨🚨🚨🚨🚨 ALERT: Significant REDUCTION in debt. We gonna get rekt?'

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
        f' compared to {prev_debts.time.strftime(TIME_DISPLAY_FMT)} UTC ({time_diff} hours ago).', file=output)


def _write_debts(debts: DebtPosition, savefile: str):
    with open(savefile, 'a', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, SAVEFILE_FIELDS)
        writer.writerow(debts.to_csv_row())


class DebtTracker(object):
    def __init__(self, address: str, tag: Optional[str]):
        self._address = address
        self._tag = tag
        self._savefile = _get_savefile(address, tag)

        # Creates a new savefile if needed.
        if not Path(self._savefile).is_file():
            with open(self._savefile, 'w', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, SAVEFILE_FIELDS)
                writer.writeheader()

    def get_name(self) -> str:
        name = self._address
        if self._tag:
            name += f' ({self._tag})'
        return name

    def get_current(self) -> Tuple[bool, str]:
        address = self._address
        tag = self._tag
        savefile = self._savefile

        debts = _query_prev_debts(savefile)

        if not debts:
            return False, f'No debts recorded yet for {self.get_name()}.'

        output = io.StringIO()
        print(
            f'Debt Positions for {self.get_name()} at {debts.time.strftime(TIME_DISPLAY_FMT)} UTC', file=output)
        print('```', file=output)
        _print_debts(debts, output)
        print('```=================', file=output)
        message = output.getvalue()

        return False, message

    async def update(self) -> Tuple[bool, str]:
        address = self._address
        tag = self._tag
        savefile = self._savefile

        debts = await _query_new_debts(address, tag)
        prev_debts = _query_prev_debts(savefile)
        has_alert, alert_message = _get_alert_message(prev_debts, debts)

        output = io.StringIO()
        print(
            f'Debt Positions for {self.get_name()} at {debts.time.strftime(TIME_DISPLAY_FMT)} UTC', file=output)
        if has_alert:
            print(alert_message, file=output)
        print('```', file=output)
        _print_debts(debts, output)
        print('', file=output)
        _print_debt_comparison(prev_debts, debts, output)
        print('```=================', file=output)
        message = output.getvalue()

        _write_debts(debts, savefile)

        return has_alert, message
