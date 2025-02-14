import re
import os
import logging
from decouple import config
from celery import shared_task
from latextranslate import translate_arxiv
from django.core.cache import cache
from django.conf import settings


logger = logging.getLogger(__name__)

LOCK_TIMEOUT = config('CENXIV_COMPILE_LOCK_TIMEOUT', default=20 * 60, cast=int) # seconds

@shared_task
def download_and_compile_arxiv(arxiv_idv):
    """Task to download an arxiv article src and compile it to pdf."""
    match = re.match(r"(.*)v(\d+)", arxiv_idv)
    if not match:
        logger.error(f"Invalid arxiv_idv format: {arxiv_idv}")
        return

    arxiv_id, version = match.groups()
    cn_pdf_file = f'{settings.CENXIV_FILE_PATH}/arxiv{arxiv_id}/v{version}/cn_pdf/{arxiv_idv}.pdf'
    if os.path.isfile(cn_pdf_file):
        logger.info(f'Chinese PDF file {cn_pdf_file} exists, do nothing')
        return

    # 分布式锁机制
    lock_key = f'cenxiv:compile_lock:{arxiv_idv}'
    # 尝试获取锁（原子操作）
    if not cache.add(lock_key, 'locked', timeout=LOCK_TIMEOUT):
        logger.info(f'Task for downloading and compiling arxiv:{arxiv_idv} is already being processed.')
        return

    logger.info(f'Begain to download and compile arxiv:{arxiv_idv}')
    translate_arxiv.main([arxiv_idv, '-o', settings.CENXIV_FILE_PATH])