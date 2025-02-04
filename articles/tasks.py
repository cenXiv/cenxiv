import os
import logging
from celery import shared_task
from latextranslate import translate_arxiv
from django.conf import settings


logger = logging.getLogger(__name__)

@shared_task
def download_and_compile_arxiv(arxiv_idv):
    """Task to download an arxiv article src and compile it to pdf."""
    arxiv_id, version = arxiv_idv.split('v')
    cn_pdf_file = f'{settings.CENXIV_FILE_PATH}/arxiv{arxiv_id}/v{version}/cn_pdf/{arxiv_idv}.pdf'
    if os.path.isfile(cn_pdf_file):
        logger.info(f'Chinese PDF file {cn_pdf_file} exists, do nothing')
    else:
        logger.info(f'Begain to download and compile arxiv:{arxiv_idv}')
        translate_arxiv.main([arxiv_idv, '-o', settings.CENXIV_FILE_PATH])