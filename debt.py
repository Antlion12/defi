#!/usr/bin/python3

# This script queries the debt positions for a given wallet address.
#
# Usage:
# > ./debt.py --address=<YOUR WALLET ADDRESS>
#
# Example (TetraNode's wallet):
# > ./debt.py --address=0x9c5083dd4838e120dbeac44c052179692aa5dac5
#
# Example (DeFiGod's wallet):
# > ./debt.py --address=0x3f3e305c4ad49271ebda489dd43d2c8f027d2d41
#
# This script queries the Zapper API.

from absl import app
from absl import flags
from absl import logging
from datetime import datetime
import requests
import json
import pprint

FLAGS = flags.FLAGS
flags.DEFINE_string("address", None, "Your wallet address.")
flags.mark_flag_as_required("address")

API_KEY = "96e0cc51-a62e-42ca-acee-910ea7d2a241"
ZAPPER_SUPPORTED_PROTOS_FMT = "https://api.zapper.fi/v1/protocols/balances/supported?api_key={api_key}&addresses%5B%5D={address}"
ZAPPER_APP_BALANCE_FMT = "https://api.zapper.fi/v1/protocols/{app}/balances?network={network}&api_key={api_key}&addresses[]={address}"


# Returns list of apps associated with a wallet's supported protocols.
class App(object):
    def __init__(self, network_in, app_in):
        self.network = network_in
        self.app = app_in

def parse_apps(address):
    # Queries Zapper supported protocols API.
    response = requests.get(
        ZAPPER_SUPPORTED_PROTOS_FMT.format(api_key=API_KEY, address=address),
        headers={"accept": "*/*"}
    )
    response.raise_for_status()
    protocols = response.json()

    # Saves network/app pairs in the results.
    app_ids = []
    for protocol in protocols:
        logging.debug("Printing protocol")
        logging.debug(json.dumps(protocol, indent=4))
        for app in protocol["apps"]:
            if app["appId"] != "tokens":
                app_ids.append(App(protocol["network"], app["appId"]))

    return app_ids


# Constructs a key for the debts dictionary.
def make_key(asset, token=None):
    assert asset

    # Collect fields for constructing key.
    terms = []
    if token:
        terms.append(token["network"])
    else:
        terms.append(asset["network"])
    terms.append(asset["appName"])
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


def parse_debts(address, apps):
    debts = {}

    for app in apps:
        # Queries Zapper for the balances in this app.
        response = requests.get(
            ZAPPER_APP_BALANCE_FMT.format(api_key=API_KEY,
                                          address=address,
                                          network=app.network,          
                                          app=app.app),
            headers={"accept": "*/*"}
        )
        response.raise_for_status()

        for product in response.json()[address]["products"]:
            for asset in product["assets"]:
                logging.debug(f"""Found asset: {json.dumps(asset, indent=4)}""")

                if "tokens" not in asset:
                    if asset["balanceUSD"] < 0:
                        logging.debug(f"""Found debt: {json.dumps(asset, indent=4)}""")
                        # Found a debt position among asset tokens.
                        key = make_key(asset)
                        debts[key] = asset["balanceUSD"]
                else:
                    for token in asset["tokens"]:
                        if token["balanceUSD"] < 0:
                            logging.debug(f"""Found debt: {json.dumps(token, indent=4)}""")
                            # Found a debt position among asset tokens.
                            key = make_key(asset, token)
                            debts[key] = token["balanceUSD"]

    return debts


def print_debts(debts_dict):
    total_debt = 0
    for name, value in debts_dict.items():
        print(f"""{name}: {value:,} USD""")
        total_debt += value

    print("---")
    print(f"""Total Debt: {total_debt:,} USD""")


def main(argv):
    address = FLAGS.address.lower()
    print(f"""Debt Positions for {address} at {datetime.utcnow()} UTC""")
    print("-----")
    print("Parsing apps...")
    apps = parse_apps(address)
    print("Parsing debts...")
    debts = parse_debts(address, apps)
    print_debts(debts)
    print("\n")

if __name__ == "__main__":
    app.run(main)
