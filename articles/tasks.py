import logging
from celery import shared_task


logger = logging.getLogger(__name__)

@shared_task
def download_and_compile_arxiv(arxiv_id):
    """Task to download an arxiv article src and compile it to pdf."""
    logger.info(f'Begain to download and compile arxiv:{arxiv_id}')