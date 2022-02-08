#!/usr/bin/python3

# This script takes a wallet address and exports its balances to a CSV.
#
# Usage:
# > ./debank_exporter.py --address<YOUR WALLET ADDRESS>
#
# Requires Python version >= 3.6.
# This script queries the DeBank API.

from absl import app
from absl import flags
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

FLAGS = flags.FLAGS
flags.DEFINE_string('address', None, 'Your wallet address.')
flags.DEFINE_string('tag', None, 'Human readable name for wallet.')
flags.mark_flag_as_required('address')

DEBANK_TOKENLIST_FMT = 'https://openapi.debank.com/v1/user/token_list?id={address}&is_all=false'
DEBANK_PROTOCOLS_FMT = 'https://openapi.debank.com/v1/user/complex_protocol_list?id={address}'
SAVEFILE_FIELDS = ['address',
                   'chain',
                   'name',
                   'position',
                   'balance_type',
                   'asset_type',
                   'symbol',
                   'amount',
                   'price',
                   'usd_value']
BASENAME = 'debank-export'


class Balance(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            if key == 'address':
                self.address = value
            elif key == 'chain':
                self.chain = value
            elif key == 'name':
                self.name = value
            elif key == 'position':
                self.position = value
            elif key == 'balance_type':
                self.balance_type = value
            elif key == 'asset_type':
                self.asset_type = value
            elif key == 'symbol':
                self.symbol = value
            elif key == 'amount':
                self.amount = value
            elif key == 'price':
                self.price = value
            elif key == 'usd_value':
                self.usd_value = value
            elif key == 'csv_row':
                self.from_csv_row(value)
                break
            else:
                logging.fatal('Ineligible key: ' + key)

    def from_csv_row(self, dict_in: dict):
        self.address = dict_in['address']
        self.chain = dict_in['chain']
        self.name = dict_in['name']
        self.position = dict_in['position']
        self.balance_type = dict_in['balance_type']
        self.asset_type = dict_in['asset_type']
        self.symbol = dict_in['symbol']
        self.amount = dict_in['amount']
        self.price = dict_in['price']
        self.usd_value = dict_in['usd_value']

    def to_csv_row(self) -> dict:
        result = {}
        result['address'] = self.address
        result['chain'] = self.chain
        result['name'] = self.name
        result['position'] = self.position
        result['balance_type'] = self.balance_type
        result['asset_type'] = self.asset_type
        result['symbol'] = self.symbol
        result['amount'] = self.amount
        result['price'] = self.price
        result['usd_value'] = self.usd_value
        return result


def _get_tagged_address(address: str, tag: Optional[str]) -> str:
    result = address
    if tag:
        result += f' ({tag})'
    return result


def _parse_wallet_balance(token_list: dict, address: str, tag: Optional[str], balances: List[Balance]):
    position = 0
    for token in token_list:
        position += 1
        amount = token['amount']
        price = token['price']
        usd_value = amount * price
        balance = Balance(address=_get_tagged_address(address, tag),
                          chain=token['chain'],
                          name='wallet',
                          position=position,
                          balance_type='wallet',
                          asset_type='token',
                          symbol=token['symbol'],
                          amount=amount,
                          price=price,
                          usd_value=usd_value)
        balances.append(balance)


# Parses protocol balance json.
# Args:
#   protocol: Json for the protocol's balance.
#   balances: Mutable list of Balance objects to be appended to.
def _parse_protocol_balance(protocol: dict, address: str, tag: Optional[str], balances: List[Balance]):
    position = 0
    for item in protocol['portfolio_item_list']:
        position += 1

        for prefix in ['supply', 'reward', 'borrow']:
            if f'{prefix}_token_list' not in item['detail']:
                continue
            for token in item['detail'][f'{prefix}_token_list']:
                amount = token['amount']
                price = token['price']
                usd_value = amount * price
                if prefix == 'borrow':
                    usd_value = -usd_value
                balance = Balance(address=_get_tagged_address(address, tag),
                                  chain=protocol['chain'],
                                  name=protocol['name'],
                                  position=position,
                                  balance_type=prefix,
                                  asset_type=item['name'],
                                  symbol=token['symbol'],
                                  amount=amount,
                                  price=price,
                                  usd_value=usd_value)
                balances.append(balance)


async def _query_debank(address: str, tag: Optional[str]) -> List[Balance]:
    balances = []

    token_list_response = await fetch_url(
        DEBANK_TOKENLIST_FMT.format(address=address)
    )
    token_list = json.loads(token_list_response)
    logging.debug(f'{json.dumps(token_list, indent=4)}')
    _parse_wallet_balance(token_list, address, tag, balances)

    protocols_response = await fetch_url(
        DEBANK_PROTOCOLS_FMT.format(address=address)
    )
    protocols = json.loads(protocols_response)
    for protocol in protocols:
        logging.debug(f'{json.dumps(protocol, indent=4)}')
        _parse_protocol_balance(protocol, address, tag, balances)

    return balances


def _get_savefile(address: str, tag: Optional[str]) -> str:
    terms = [BASENAME, address]
    if tag:
        terms.append(tag)
    filename = '-'.join(terms)
    return f'{filename}.csv'


def _write_csv(balances: List[Balance], savefile: str):
    with open(savefile, 'a', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, SAVEFILE_FIELDS)
        for balance in balances:
            writer.writerow(balance.to_csv_row())


async def run_exporter(address: str, tag: Optional[str]):
    # Creates or overwrites the savefile.
    savefile = _get_savefile(address, tag)
    with open(savefile, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, SAVEFILE_FIELDS)
        writer.writeheader()

    balances = await _query_debank(address, tag)
    _write_csv(balances, savefile)


def main(argv):
    address = FLAGS.address.lower()
    tag = FLAGS.tag
    asyncio.run(run_exporter(address, tag))


if __name__ == '__main__':
    app.run(main)
