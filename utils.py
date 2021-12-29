# This library provides utility functions for this project.

import httpx

MAX_ATTEMPTS = 3


# Makes a GET request to a URL and stores the result as a text string.
async def fetch_url(url: str) -> str:
    attempts = 0
    result = ''
    print(f'Fetching url: {url}')
    while attempts <= MAX_ATTEMPTS:
        attempts += 1
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers={'accept': '*/*'}, timeout=60)
            result = str(response.text)
            break
        except httpx.RemoteProtocolError as e:
            print(f'Retrying due to exception: {e}')
    return result
