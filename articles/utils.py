import time
import json
import logging
from http import HTTPStatus
import requests
from requests.exceptions import HTTPError, RequestException


# Configure logging (do this once at the module level)
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)



chinese_week_days = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']

def get_translation_dict():
    with open("articles/groups_dict.json", "r") as f:
        groups_dict = json.load(f)
    with open("articles/categories_dict.json", "r") as f:
        categories_dict = json.load(f)

    return groups_dict | categories_dict

def request_get(url, retries=3, retry_delay=1, max_retry_delay=60):
    """
    Performs a GET request with retry logic for specific HTTP status codes and connection errors.

    Args:
        url: The URL to fetch.
        retries: The maximum number of retries.
        retry_delay: The initial retry delay in seconds.
        max_retry_delay: The maximum retry delay in seconds to prevent infinite waiting.

    Returns:
        The requests.Response object if successful, None otherwise.  It's generally better to return
        the response and handle status codes/content outside this function for more flexibility.

    Raises:
        RequestException: If the request fails after all retries and the error is not a retryable one.
    """

    retry_codes = [
        HTTPStatus.TOO_MANY_REQUESTS,
        HTTPStatus.INTERNAL_SERVER_ERROR,
        HTTPStatus.BAD_GATEWAY,
        HTTPStatus.SERVICE_UNAVAILABLE,
        HTTPStatus.GATEWAY_TIMEOUT,
    ]

    for attempt in range(retries):
        try:
            response = requests.get(url)
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            logger.info(f"Successfully fetched URL: {url}")
            return response  # Return the response object

        except HTTPError as exc:
            code = exc.response.status_code if exc.response else None # Handle cases where exc.response might be None
            logger.warning(f"HTTP Error: {code} for URL: {url}. Attempt {attempt + 1}/{retries}")

            if code in retry_codes:
                current_delay = min(retry_delay * (2**attempt), max_retry_delay)
                logger.info(f"Retrying in {current_delay} seconds...")
                time.sleep(current_delay)
                continue

            # Re-raise the exception if it's not a retryable error
            raise  # This will exit the loop and raise the original HTTPError

        except RequestException as exc:  # Catch other request exceptions
            logger.error(f"Request Exception: {exc} for URL: {url}. Attempt {attempt + 1}/{retries}")
            current_delay = min(retry_delay * (2**attempt), max_retry_delay)
            logger.info(f"Retrying in {current_delay} seconds...")
            time.sleep(current_delay)
            continue

    else:  # This block executes if the loop completes without a successful request
        logger.error(f"Failed to fetch URL: {url} after {retries} attempts.")
        return None # Return None to indicate failure
