#!/usr/bin/python3

# This script queries the debt positions for a given wallet address. Requires
# Python version >= 3.6.
#
# Usage:
# > ./debt.py --address=<YOUR WALLET ADDRESS> [--tag=<HUMAN READABLE NAME>]
#
# Example (TetraNode's wallet):
# > ./debt.py --address=0x9c5083dd4838e120dbeac44c052179692aa5dac5 --tag=TetraNode
#
# Example (DeFiGod's wallet):
# > ./debt.py --address=0x3f3e305c4ad49271ebda489dd43d2c8f027d2d41 --tag=DeFiGod
#
# This script queries the Zapper API.

from absl import app
from absl import flags
from absl import logging
from datetime import datetime
import requests
import json

FLAGS = flags.FLAGS
flags.DEFINE_string("address", None, "Your wallet address.")
flags.DEFINE_string("tag", None, "Human readable name for wallet.")
flags.mark_flag_as_required("address")

API_KEY = "96e0cc51-a62e-42ca-acee-910ea7d2a241"
ZAPPER_BALANCE_FMT = "https://api.zapper.fi/v1/balances?api_key={api_key}&addresses[]={address}"
MAX_ATTEMPTS = 3

def fetch_url(url):
    attempts = 0
    while attempts < MAX_ATTEMPTS:
        response = requests.get(url, headers={"accept": "*/*"}, stream=True)
        if response.status_code == 200:
            break
        print("Retrying URL: {url}")
        attempts += 1
    response.raise_for_status()
    return response


# Constructs a key for the debts dictionary.
def make_key(asset, token=None):
    assert asset

    # Collect fields for constructing key.
    terms = []
    if token:
        terms.append(token["network"])
    else:
        terms.append(asset["network"])
    terms.append(asset["appId"])
    if "label" in asset:
        terms.append(asset["label"])
    elif "symbol" in asset:
        terms.append(asset["symbol"])

    key = " / ".join(terms)

    if token:
        if "label" in token:
            key += f""" ({token["label"]})"""
        elif "symbol" in token:
            key += f""" ({token["symbol"]})"""

    return key


# Parses app_balance json for debts.
# Args:
#   app_balance: Json for the app's balance.
#   address: Wallet address for which we are scraping debt balances.
#   debts: An output dictionary for which we store debt balances (key being the
#       debt description, value being the debt value in tokens).
def parse_app_balance(app_balance, address, debts):
    for product in app_balance["balances"][address]["products"]:
        for asset in product["assets"]:
            logging.debug(f"""Found asset: {json.dumps(asset, indent=4)}""")

            if "tokens" not in asset:
                if asset["balanceUSD"] < 0:
                    logging.debug(f"""Found debt: {json.dumps(asset, indent=4)}""")
                    # Found a debt position among asset tokens.
                    key = make_key(asset)
                    debts[key] = asset["balance"]
            else:
                for token in asset["tokens"]:
                    if token["balanceUSD"] < 0:
                        logging.debug(f"""Found debt: {json.dumps(token, indent=4)}""")
                        # Found a debt position among asset tokens.
                        key = make_key(asset, token)
                        debts[key] = token["balance"]


# Fetches balances for the address, looks for debts, stores the debts in a
# dictionary.
def parse_debts(address):
    response = fetch_url(
        ZAPPER_BALANCE_FMT.format(api_key=API_KEY,
                                  address=address)
    )

    debts = {}
    for line in response.iter_lines(decode_unicode=True):
        if not line.startswith("data: "):
            continue
        _, content = line.split(" ", 1)
        if content == "start" or content == "end":
            continue

        app_balance = json.loads(content)
        parse_app_balance(app_balance, address, debts)

    return debts


def print_debts(debts_dict):
    total_debt = 0
    for name, value in sorted(debts_dict.items(), key=lambda x: -x[1]):
        print(f"""{value:17,.2f} -- {name}""")
        total_debt += value

    print("-----------------")
    print(f"""{total_debt:17,.2f} USD -- Total Debt""")


def main(argv):
    address = FLAGS.address.lower()
    tag = f""" ({FLAGS.tag})""" if FLAGS.tag else ""
    print(f"""Debt Positions for {address}{tag} at {datetime.utcnow()} UTC""")
    print("-----")
    debts = parse_debts(address)
    print_debts(debts)
    print("\n")

if __name__ == "__main__":
    app.run(main)
