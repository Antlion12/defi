# This library provides utility functions for this project.

from datetime import datetime
from datetime import MINYEAR
from datetime import timedelta
from datetime import timezone
import httpx

MAX_ATTEMPTS = 3
TIME_STORAGE_FMT = '%Y-%m-%d %H:%M:%S%z'
TIME_DISPLAY_FMT = '%Y-%m-%d %H:%M:%S'
MIN_TIME = datetime(year=MINYEAR, month=1, day=1, tzinfo=timezone.utc)

# Makes a GET request to a URL and stores the result as a text string.
async def fetch_url(url: str) -> str:
    attempts = 0
    result = ''
    print(f'Fetching url: {url}')
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers={'accept': '*/*'}, timeout=60)
            if response.status_code != 200:
                raise Exception('URL fetch attempt did not return 200')
            result = str(response.text)
            return result
        except Exception as e:
            if attempts >= MAX_ATTEMPTS:
                raise e
            else:
                print(f'Retrying due to exception: {e}')
    raise Exception('Should not reach this part.')


# Formats timedelta into something more readable.
def format_timedelta(delta: timedelta) -> str:
    tokens = []
    days = delta.days
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    seconds = (delta.seconds % 60)

    if days:
        tokens.append(f'{delta.days} days')
    if hours:
        tokens.append(f'{hours} hours')
    if minutes:
        tokens.append(f'{minutes} minutes')
    tokens.append(f'{seconds} seconds')

    return ', '.join(tokens)


# Formats datetime into a display format.
def display_time(time: datetime) -> str:
    return time.strftime(TIME_DISPLAY_FMT)


# Formats the datetime into a storage representation string.
def format_storage_time(time: datetime) -> str:
    return time.strftime(TIME_STORAGE_FMT)


# Parses a datetime from a storage representation string.
def parse_storage_time(time_string: str) -> datetime:
    return datetime.strptime(time_string, TIME_STORAGE_FMT)
