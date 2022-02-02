#!/usr/bin/python3

# This script queries the debt positions for a given wallet address and raises
# alerts.
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
# Requires Python version >= 3.6.
# This script queries the DeBank API.
# Creates a savefile CSV called <address>[-<tag>].csv to save results of recent
# queries.

from absl import app
from absl import flags
from debt_lib import DebtTracker
import asyncio

FLAGS = flags.FLAGS
flags.DEFINE_string('address', None, 'Your wallet address.')
flags.DEFINE_string('tag', None, 'Human readable name for wallet.')
flags.mark_flag_as_required('address')


async def run_tracker(tracker):
    await tracker.update()
    print(tracker.get_last_message())


def main(argv):
    address = FLAGS.address.lower()
    tag = FLAGS.tag
    tracker = DebtTracker(address, tag, 'commandline', last_alert_time=None)
    asyncio.run(run_tracker(tracker))


if __name__ == '__main__':
    app.run(main)
