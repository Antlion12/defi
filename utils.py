# This library provides utility functions for this project.

from datetime import timedelta
import httpx

MAX_ATTEMPTS = 3


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
            assert response.status_code == 200
            result = str(response.text)
            return result
        except httpx.RemoteProtocolError as e:
            print(f'Retrying due to exception: {e}')
    raise Exception('URL fetch failed')


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
