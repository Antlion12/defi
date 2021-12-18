#!/usr/bin/python3

# This script allows you to track the growth of your Popsicle Finance PLP
# position in token-denominated terms (which is useful because relying on USD
# balance alone obscures token growth because of price fluctuation).
#
# Usage:
# > ./popsicle.py --address=<YOUR WALLET ADDRESS>
#
# This script queries the Zapper API.

from absl import app
from absl import flags
import requests
import json

FLAGS = flags.FLAGS
flags.DEFINE_string("address", None, "Your wallet address.")
flags.mark_flag_as_required("address")

ZAPPER_POPSICLE_BALANCE_URL = "https://api.zapper.fi/v1/protocols/popsicle/balances?network=ethereum&api_key=96e0cc51-a62e-42ca-acee-910ea7d2a241&addresses[]="

class PLPToken(object):
    def __init__(self, symbol_in, count_in, usd_value_in, fraction_in):
        self.symbol = symbol_in
        self.count = count_in
        self.usd_value = usd_value_in
        self.fraction = fraction_in

def print_plp_balance(plp):
    # Gather basic stats for this PLP.
    plp_usd_value = plp["balanceUSD"]
    tokens = []
    for token in plp["tokens"]:
        token_symbol = token["symbol"]
        token_count = token["balance"]
        token_usd_value = token["balanceUSD"]
        token_fraction = token_usd_value / plp_usd_value

        tokens.append(PLPToken(token_symbol, token_count, token_usd_value, token_fraction))

    # Print stats.
    print(f"""PLP: {"/".join(token.symbol for token in tokens)}""")
    print("-----")
    for token in tokens:
        print(f"""{token.count} {token.symbol}""", end=" ")
        print(f"""(${token.usd_value:.2f})""", end=" ")
        print(f"""{token.fraction * 100:.2f}%""")
    print("-----")
    print("Total position denominated in token terms:")
    for token in tokens:
        print(f"""* {token.count / token.fraction} {token.symbol}""")
    print("-----")
    for token in tokens:
        print(f"""1 PLP token = {token.count / token.fraction / plp["balance"]} {token.symbol}""")

    print("\n\n")


def main(argv):
    address = FLAGS.address.lower()
    result = requests.get(ZAPPER_POPSICLE_BALANCE_URL + address)
    result.raise_for_status()

    popsicle_assets = result.json()[address]["products"][0]["assets"]

    for plp in popsicle_assets:
        print_plp_balance(plp)

if __name__ == "__main__":
    app.run(main)
