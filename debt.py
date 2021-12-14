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
#
# TODO: rather than fetching protocol's debt summary, query individual debt
# positions for a protocol to capture balances that don't show up in the summary
# (e.g., Abracadabra debt positions don't show up in the summary).

from absl import app
from absl import flags
import requests
import json
import pprint

FLAGS = flags.FLAGS
flags.DEFINE_string("address", None, "Your wallet address.")
flags.mark_flag_as_required("address")

ZAPPER_BALANCE_URL = "https://api.zapper.fi/v1/balances?api_key=96e0cc51-a62e-42ca-acee-910ea7d2a241&addresses%5B%5D="

def parse_debts(results_text, address):
    debts = {}
    for line in results_text.splitlines():
        if not line.startswith("data: "):
            continue
        _, content = line.split(" ", 1)
        if content == "start" or content == "end":
            continue

        app_balance = json.loads(content)
        network_id = app_balance["network"]
        app_id = app_balance["appId"]

        # Get debt for this app.
        balance_components = app_balance["balances"][address]["meta"]
        debt_usd = 0
        for component in balance_components:
            if component["label"] == "Debt":
                debt_usd = component["value"]

        if debt_usd:
            debts[f"""{network_id}/{app_id}"""] = debt_usd

    total_debt = 0
    for name, value in debts.items():
        print(f"""{name}: {value} USD""")
        total_debt += value

    print("---")
    print(f"""Total Debt: {total_debt} USD""")


def main(argv):
    print("Making request...")
    address = FLAGS.address.lower()
    result = requests.get(ZAPPER_BALANCE_URL + address, headers={"accept": "*/*"})
    result.raise_for_status()
    print("Done")
    print("")

    parse_debts(result.text, address)

if __name__ == "__main__":
    app.run(main)
