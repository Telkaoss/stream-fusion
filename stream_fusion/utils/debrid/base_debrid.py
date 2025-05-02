from collections import deque
import json
import time
import asyncio

import aiohttp
import requests

from stream_fusion.logging_config import logger
from stream_fusion.settings import settings


class BaseDebrid:
    def __init__(self, config, session: aiohttp.ClientSession = None):
        self.config = config
        self.logger = logger
        # Use provided session or create a new one
        self.__session = session if session else self._create_session()

        # Rate limiters
        self.global_limit = 250
        self.global_period = 60
        self.torrent_limit = 1
        self.torrent_period = 1

        self.global_requests = deque()
        self.torrent_requests = deque()

    def _create_session(self):
        session = aiohttp.ClientSession()
        if settings.proxy_url:
            self.logger.info(f"BaseDebrid: Using proxy: {settings.proxy_url}")
            session.connector = aiohttp.TCPConnector(proxy=str(settings.proxy_url))
        return session

    def _rate_limit(self, requests_queue, limit, period):
        current_time = time.time()

        while requests_queue and requests_queue[0] <= current_time - period:
            requests_queue.popleft()

        if len(requests_queue) >= limit:
            sleep_time = requests_queue[0] - (current_time - period)
            if sleep_time > 0:
                time.sleep(sleep_time)

        requests_queue.append(time.time())

    def _global_rate_limit(self):
        self._rate_limit(self.global_requests, self.global_limit, self.global_period)

    def _torrent_rate_limit(self):
        self._rate_limit(self.torrent_requests, self.torrent_limit, self.torrent_period)

    async def json_response(self, url, method="get", data=None, headers=None, files=None):
        self._global_rate_limit() # Assuming these are quick checks
        if "torrents" in url:
            self._torrent_rate_limit() # Assuming these are quick checks

        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                # Use aiohttp session for async requests
                async with self.__session.request(
                    method, url, data=data, headers=headers # Files might need different handling with aiohttp
                    # If 'files' are used, it typically involves FormData which needs setup
                    # For simplicity, assuming 'files' is not commonly used or handled correctly elsewhere
                ) as response:
                    response.raise_for_status() # Check for HTTP errors (4xx, 5xx)

                    try:
                        # Await the json parsing
                        json_data = await response.json()
                        return json_data
                    except aiohttp.ContentTypeError as json_err: # Catch aiohttp's specific error
                        self.logger.error(f"BaseDebrid: Invalid JSON response: {json_err}")
                        resp_text = await response.text()
                        self.logger.debug(
                            f"BaseDebrid: Response content: {resp_text[:200]}..."
                        )
                        if attempt < max_attempts - 1:
                            wait_time = 2**attempt + 1
                            self.logger.info(
                                f"BaseDebrid: Retrying in {wait_time} seconds..."
                            )
                            await asyncio.sleep(wait_time) # Use asyncio.sleep
                        else:
                            return None

            except aiohttp.ClientResponseError as e: # Catch aiohttp HTTP errors
                status_code = e.status
                if status_code == 429:
                    wait_time = 2**attempt + 1
                    self.logger.warning(
                        f"BaseDebrid: Rate limit exceeded. Attempt {attempt + 1}/{max_attempts}. Waiting for {wait_time} seconds."
                    )
                    await asyncio.sleep(wait_time) # Use asyncio.sleep
                elif 400 <= status_code < 500:
                    self.logger.error(
                        f"BaseDebrid: Client error occurred: {e}. Status code: {status_code}"
                    )
                    return None # Stop retrying on client errors (except 429)
                elif 500 <= status_code < 600:
                    self.logger.error(
                        f"BaseDebrid: Server error occurred: {e}. Status code: {status_code}"
                    )
                    if attempt < max_attempts - 1:
                        wait_time = 2**attempt + 1
                        self.logger.info(
                            f"BaseDebrid: Retrying in {wait_time} seconds..."
                        )
                        await asyncio.sleep(wait_time) # Use asyncio.sleep
                    else:
                        return None # Stop after max attempts for server errors
                else:
                    self.logger.error(
                        f"BaseDebrid: Unexpected HTTP error occurred: {e}. Status code: {status_code}"
                    )
                    return None
            except aiohttp.ClientConnectionError as e: # Catch connection errors
                self.logger.error(f"BaseDebrid: Connection error occurred: {e}")
                if attempt < max_attempts - 1:
                    wait_time = 2**attempt + 1
                    self.logger.info(f"BaseDebrid: Retrying in {wait_time} seconds...")
                    await asyncio.sleep(wait_time) # Use asyncio.sleep
                else:
                    return None
            except asyncio.TimeoutError as e: # Catch timeouts (if configured in session)
                self.logger.error(f"BaseDebrid: Request timed out: {e}")
                if attempt < max_attempts - 1:
                    wait_time = 2**attempt + 1
                    self.logger.info(f"BaseDebrid: Retrying in {wait_time} seconds...")
                    await asyncio.sleep(wait_time) # Use asyncio.sleep
                else:
                    return None
            except aiohttp.ClientError as e: # Catch other aiohttp client errors
                self.logger.error(f"BaseDebrid: An unexpected aiohttp error occurred: {e}")
                return None
            except Exception as e: # Catch any other unexpected error
                self.logger.error(f"BaseDebrid: An unexpected general error occurred: {e}", exc_info=True)
                return None

        self.logger.error(
            "BaseDebrid: Max attempts reached. Unable to complete request."
        )
        return None

    async def wait_for_ready_status(self, check_status_func, timeout=30, interval=5):
        """Waits for a torrent to be ready by periodically calling check_status_func."""
        self.logger.info(f"BaseDebrid: Waiting for {timeout} seconds for caching.")
        start_time = time.time()
        while time.time() - start_time < timeout:
            # Assume check_status_func might become async if it involves IO
            status_ready = await check_status_func()
            if status_ready:
                self.logger.info("BaseDebrid: File is ready!")
                return True
            await asyncio.sleep(interval)
        self.logger.info(f"BaseDebrid: Waiting timed out.")
        return False

    async def download_torrent_file(self, download_url):
        """Downloads a torrent file from a URL asynchronously."""
        try:
            async with self.__session.get(download_url) as response:
                response.raise_for_status() # Check for HTTP errors
                # Read the content asynchronously
                content = await response.read()
                return content
        except aiohttp.ClientError as e:
            self.logger.error(f"BaseDebrid: Failed to download torrent file from {download_url}: {e}")
            # Optionally re-raise or return None/empty bytes based on desired error handling
            return None
        except Exception as e:
            self.logger.error(f"BaseDebrid: Unexpected error downloading torrent file: {e}", exc_info=True)
            return None

    def get_stream_link(self, query, ip=None):
        raise NotImplementedError
    
    def add_magnet_or_torrent(self, magnet, torrent_download=None, ip=None):
        raise NotImplementedError

    def add_magnet(self, magnet, ip=None):
        raise NotImplementedError

    def get_availability_bulk(self, hashes_or_magnets, ip=None):
        raise NotImplementedError
