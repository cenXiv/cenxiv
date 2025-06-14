import time
import json
import logging
from http import HTTPStatus
import requests
from requests.exceptions import HTTPError, RequestException
import openai
import django
from django.conf import settings
from latextranslate import process_latex, translate
from .models import Article, Author, Category, Link
from .translators import translator


# Configure logging (do this once at the module level)
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


tl = settings.TRANSLATOR

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

def translate_latex_paragraph(text, tl):
    # tt, ro = process_latex.replace_latex_objects(text.replace('\n', ' '))
    # # ltt = translate.convert_to_latex(tt)
    # # ttt = translator(tl)(ltt)
    # # return translate.convert_from_latex(process_latex.recover_latex_objects(ttt, ro)[0])
    # ttt = translator(tl)(tt)
    # return process_latex.recover_latex_objects(ttt, ro)[0]

    text_translator = translate.TextTranslator('google', 'zh', 'en')
    latex_translator = translate.LatexTranslator(text_translator)
    return latex_translator.translate_full_latex(text, make_complete=False, nocache=True).strip()

def translate_and_save_article(result, ok=False):
    if ok:
        return result, True

    # arxiv_id, version = result.entry_id.split('/')[-1].split('v')
    arxiv_id, version = result.entry_id.split(r'/abs/')[-1].rsplit('v', 1)
    try:
        article = Article.objects.get(source_archive='arxiv', entry_id=arxiv_id, entry_version=version)
        return article, True
    except Article.DoesNotExist:
        try:
            # text_translator = translate.TextTranslator(tl, 'en', 'zh-CN')
            # latex_translator = translate.LatexTranslator(text_translator, debug=False, threads=0)

            # title_cn = latex_translator.translate_full_latex(result.title, make_complete=False).strip()
            # abstract_cn = latex_translator.translate_full_latex(result.summary, make_complete=False).strip()
            try:
                title_cn = translate_latex_paragraph(result.title, tl)
            except openai.BadRequestError as e:
                if e.status_code == 400: # data may contain inappropriate content
                    title_cn = 'TO_BE_TRANSLATED: ' + result.title
                else:
                    raise
            try:
                abstract_cn = translate_latex_paragraph(result.summary, tl)
            except openai.BadRequestError as e:
                if e.status_code == 400: # data may contain inappropriate content
                    abstract_cn = 'TO_BE_TRANSLATED: ' + result.summary
                else:
                    raise
            comment_cn = None
            journal_ref_cn = None
            if result.comment:
                try:
                    comment_cn = translator(tl)(result.comment.replace('\n', ' '))
                except openai.BadRequestError as e:
                    if e.status_code == 400: # data may contain inappropriate content
                        comment_cn = 'TO_BE_TRANSLATED: ' + result.comment
                    else:
                        raise
            if result.journal_ref:
                try:
                    journal_ref_cn = translator(tl)(result.journal_ref.replace('\n', ' '))
                except openai.BadRequestError as e:
                    if e.status_code == 400: # data may contain inappropriate content
                        journal_ref_cn = 'TO_BE_TRANSLATED: ' + result.journal_ref
                    else:
                        raise
            # title_cn = '中文标题'
            # abstract_cn = '中文摘要'
            logger.info(f'Successfully translated arxiv:{arxiv_id}v{version}.')
        except Exception as e:
            logger.warning(f'Failed to translate arxiv:{arxiv_id}v{version} due to {e}, will retry latter.')
            return result, False

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
        try:
            article.save()
        except django.db.utils.IntegrityError:
            # already exist in db
            pass

        for author in result.authors:
            author_ = Author(name=author.name, article=article)
            try:
                author_.save()
            except django.db.utils.IntegrityError:
                # already exist in db
                pass
        for category in result.categories:
            category_ = Category(name=category, article=article)
            try:
                category_.save()
            except django.db.utils.IntegrityError:
                # already exist in db
                pass
        for link in result.links:
            link_ = Link(url=link.href, article=article)
            try:
                link_.save()
            except django.db.utils.IntegrityError:
                # already exist in db
                pass

        return article, True
