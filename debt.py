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
# Creates a savefile CSV called <address>[-<tag>].csv to save results of recent
# queries.

from absl import app
from absl import flags
from absl import logging
from datetime import datetime
from datetime import timedelta
from pathlib import Path
import csv
import io
import requests
import json

FLAGS = flags.FLAGS
flags.DEFINE_string("address", None, "Your wallet address.")
flags.DEFINE_string("tag", None, "Human readable name for wallet.")
flags.mark_flag_as_required("address")

API_KEY = "96e0cc51-a62e-42ca-acee-910ea7d2a241"
ZAPPER_BALANCE_FMT = "https://api.zapper.fi/v1/balances?api_key={api_key}&addresses[]={address}"
MAX_ATTEMPTS = 3
SAVEFILE_FIELDS = ["time", "address", "tag", "total_debt", "individual_debts"]
LARGE_CHANGE_THRESHOLD = 0.05

def fetch_url(url):
    attempts = 0
    while attempts <= MAX_ATTEMPTS:
        attempts += 1
        try:
            response = requests.get(url, headers={"accept": "*/*"})
        except ChunkedEncodingError as e:
            print("Retrying due to ChunkedEncodingError")
    return response.text


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


class DebtPosition(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            if key == "time":
                self.time = value
            elif key == "address":
                self.address = value
            elif key == "tag":
                self.tag = value
            elif key == "total_debt":
                self.total_debt = value
            elif key == "individual_debts":
                self.individual_debts = value
            elif key == "csv_row":
                self.from_csv_row(value)
                break
            else:
                logging.fatal("Ineligible key: " + key)

    def from_csv_row(self, dict_in):
        self.time = datetime.strptime(dict_in["time"], "%Y-%m-%d %H:%M:%S")
        self.address = dict_in["address"]
        self.tag = dict_in["tag"]
        self.total_debt = float(dict_in["total_debt"])
        self.individual_debts = json.loads(dict_in["individual_debts"])

    def to_csv_row(self):
        result = {}
        result["time"] = self.time.strftime("%Y-%m-%d %H:%M:%S")
        result["address"] = self.address
        result["tag"] = self.tag
        result["total_debt"] = self.total_debt
        result["individual_debts"] = json.dumps(self.individual_debts)
        return result


def compute_total_debt(individual_debts):
    return sum(debt for _, debt in individual_debts.items())


# Fetches balances for the address, looks for debts, stores the debts in a
# dictionary.
def query_new_debts(address, tag):
    response = fetch_url(
        ZAPPER_BALANCE_FMT.format(api_key=API_KEY,
                                  address=address)
    )
    logging.debug("Query Balances Response:\n" + response)

    debts = {}
    for line in response.splitlines():
        if not line.startswith("data: "):
            continue
        _, content = line.split(" ", 1)
        if content == "start" or content == "end":
            continue

        app_balance = json.loads(content)
        parse_app_balance(app_balance, address, debts)

    return DebtPosition(time=datetime.utcnow(),
                        address=address,
                        tag=tag,
                        total_debt=compute_total_debt(debts),
                        individual_debts=debts)


def print_debts(debts, output_file):
    total_debt = 0
    for name, value in sorted(debts.individual_debts.items(), key=lambda x: -x[1]):
        print(f"""{value:17,.2f} -- {name}""", file=output_file)
        total_debt += value

    print("-----------------", file=output_file)
    print(f"""{total_debt:17,.2f} USD -- Total Debt""", file=output_file)
    return 


def get_savefile(address, tag):
    filename = "-".join([address, tag]) if tag else address
    return f"""{filename}.csv"""


def read_previous_debts(savefile):
    last_row = None
    with open(savefile, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            last_row = row

    return DebtPosition(csv_row=last_row) if last_row else None


# Returns a non-empty string if the debt position should raise an alert.
# Otherwise, returns an empty string.
def get_alert_message(prev_debts, debts):
    if not prev_debts:
        return "This is a new debt position."

    change = debts.total_debt - prev_debts.total_debt
    relative_change = change / (prev_debts.total_debt + 1e-6)
    time_diff = debts.time - prev_debts.time

    if abs(relative_change) >= LARGE_CHANGE_THRESHOLD:
        return f"""ALERT: Significant change in debt."""
    elif time_diff >= timedelta(minutes=10):
        return f"""{time_diff} hours have elapsed since last update."""


def print_debt_comparison(prev_debts, debts, output_file):
    if not prev_debts:
        return

    assert prev_debts.address == debts.address
    assert prev_debts.tag == debts.tag
    assert prev_debts.time <= debts.time

    change = debts.total_debt - prev_debts.total_debt
    relative_change = change / (prev_debts.total_debt + 1e-6)
    time_diff = debts.time - prev_debts.time
    print(f"""Change: {change:+,.2f} USD ({relative_change * 100:+.4f}%)""", end="", file=output_file)
    print(f""" compared to {prev_debts.time} UTC ({time_diff} hours ago).""", file=output_file)


def write_debts(debts, savefile):
    with open(savefile, 'a', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, SAVEFILE_FIELDS)
        writer.writerow(debts.to_csv_row())


def main(argv):
    address = FLAGS.address.lower()
    tag = FLAGS.tag
    savefile = get_savefile(address, tag)

    # Creates a new savefile if needed.
    if not Path(savefile).is_file():
        with open(savefile, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, SAVEFILE_FIELDS)
            writer.writeheader()

    debts = query_new_debts(address, tag)
    prev_debts = read_previous_debts(savefile)

    alert_message = get_alert_message(prev_debts, debts)
    if alert_message:
        output = io.StringIO()
        print(f"""Debt Positions for {address}{' ({})'.format(tag) if tag else ''} at {debts.time} UTC""", file=output)
        print(f"-----", file=output)
        print_debts(debts, output)
        print("", file=output)
        print_debt_comparison(prev_debts, debts, output)
        print(alert_message, file=output)
        print("", file=output)
        message = output.getvalue()
        print(message)
    else:
        message = f"""Finished querying debt for {address}{' ({})'.format(tag) if tag else ''} at {debts.time} UTC."""
        print(message)

    write_debts(debts, savefile)


if __name__ == "__main__":
    app.run(main)
