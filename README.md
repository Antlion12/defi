# Antlion DeFi Repository
A simple collection of DeFi related scripts (written by a DeFi neophyte).

`discord_bot.py` is the library for the Discord bot (you can use the `launch_bot.sh` bash script to launch the bot). The Discord bot's event loop periodically updates the state of its trackers and sends alerts to the subscribed servers if there are significant changes (or if it's been a while since the last update). Available trackers:
* DebtTracker (`debt_lib.py`) - Tracks changes in debt positions for a wallet (total changes, changes in individual positions, LTV changes, etc).
* LinkTracker (`link_lib.py`) - Tracks changes in LINK's price relative to ETH.
* NameTracker (`name_lib.py`) - A fun community management tracker. Helpful for tracking misfits who change their names frequently.

`debank_exporter.py` is a standalone script that exports a wallet's token and protocol balances to CSV.

Portfolio and price data is fetched using the DeBank or Zapper APIs. HTTP fetches are performed asynchronously using the `httpx` library.

Before running these scripts, you will want to run `install_package.sh` to add some dependencies.
