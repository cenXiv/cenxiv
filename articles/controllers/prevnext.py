"""Handle requests to support sequential navigation between arXiv IDs."""

import logging
from typing import Any, Dict, Tuple
from http import HTTPStatus as status

from django.urls import reverse
from django.utils.html import escape
from django.core.exceptions import BadRequest
# from django.http import Http404

from arxiv.taxonomy.definitions import ARCHIVES, CATEGORIES_ACTIVE
from arxiv.identifier import Identifier, IdentifierException


logger = logging.getLogger(__name__)

Response = Tuple[Dict[str, Any], int, Dict[str, Any]]

def get_prevnext(id: str, function: str, context: str) -> Response:
    """
    Get the next or previous arXiv ID in the browse context.

    The 'site' parameter from the classic prevnext is no longer supported.

    Parameters
    ----------
    id
        arxiv id
    function
        prev or next
    context
        which archive or category to browse

    Returns
    -------
    dict
        Result response data.
    int
        HTTP status code.
    dict
        Headers to add to the response.

    Raises
    ------
    BadRequest
        Raised when request parameters are missing, invalid, or when an ID
        redirect cannot be returned even when the request parameters are valid.

    """
    if id is None or not id:
        raise BadRequest('Missing article identifier')
    if function not in ['prev', 'next']:
        raise BadRequest('Missing or invalid function request, should be prev or next')
    if context is None or not context:
        raise BadRequest('Missing context')
    if not (context in CATEGORIES_ACTIVE
            or context in ARCHIVES or context == 'all'):
        raise BadRequest('Invalid context')

    try:
        arxiv_id = Identifier(id)
    except IdentifierException as ex:
        raise BadRequest(escape(f"Invalid article identifier {id}"))

    # seq_id = get_sequential_id(paper_id=arxiv_id,
    #                            is_next=function == 'next',
    #                            context=context)
    # if not seq_id:
    #     raise Http404(
    #         escape(f'No {function} article found for '
    #                f'{arxiv_id.id} in {context}'))

    # redirect_url = reverse('articles:abstract', kwargs={'arxiv_id': arxiv_id.idv, 'context': context})
    redirect_url = reverse('articles:abstract', kwargs={'arxiv_id': arxiv_id.idv}) + f'?context={context}'
    return {}, status.MOVED_PERMANENTLY, {'Location': redirect_url}
