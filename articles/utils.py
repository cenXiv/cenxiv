import time
import json
import logging
from http import HTTPStatus
import requests
from requests.exceptions import HTTPError, RequestException
from .models import Article, Author, Category, Link
from .translators import translator


# Configure logging (do this once at the module level)
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)



chinese_week_days = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']

def get_translation_dict():
    with open("articles/groups_dict.json", "r", encoding='utf-8') as f:
        groups_dict = json.load(f)
    with open("articles/categories_dict.json", "r", encoding='utf-8') as f:
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

def translate_and_save_article(result):
    arxiv_id, version = result.entry_id.split('/')[-1].split('v')
    try:
        article = Article.objects.get(source_archive='arxiv', entry_id=arxiv_id, entry_version=version)
        return True
    except Article.DoesNotExist:
        try:
            title_cn = translator('google')(result.title)
            abstract_cn = translator('google')(result.summary)
            comment_cn = None
            journal_ref_cn = None
            if result.comment:
                comment_cn = translator('google')(result.comment)
            if result.journal_ref:
                journal_ref_cn = translator('google')(result.journal_ref)
            # title_cn = '中文标题'
            # abstract_cn = '中文摘要'
            logger.info(f'Successfully translated arxiv:{arxiv_id}v{version}.')
        except Exception:
            logger.warning(f'Failed to translate arxiv:{arxiv_id}v{version}, will retry latter.')
            return False

        article = Article(
            entry_id=arxiv_id,
            entry_version=version,
            title_en=result.title,
            title_cn=title_cn,
            abstract_en=result.summary,
            abstract_cn=abstract_cn,
            published_date=result.published,
            updated_date=result.updated,
            comment_en=result.comment,
            comment_cn=comment_cn,
            journal_ref_en=result.journal_ref,
            journal_ref_cn=journal_ref_cn,
            doi=result.doi,
            primary_category=result.primary_category,
        )
        article.save()
        for author in result.authors:
            author_ = Author(name=author.name, article=article)
            author_.save()
        for category in result.categories:
            category_ = Category(name=category, article=article)
            category_.save()
        for link in result.links:
            link_ = Link(url=link.href, article=article)
            link_.save()

        return True
