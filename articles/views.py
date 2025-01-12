import re
import requests
from http import HTTPStatus
from typing import Optional, Tuple, Dict, Any, Union
from bs4 import BeautifulSoup

from django.urls import reverse
from django.http import Http404
from django.core.exceptions import BadRequest
from django.shortcuts import render, redirect
from django.utils.translation import get_language
from django.http import (
    HttpResponse,
    HttpResponseRedirect,
    HttpResponseBadRequest,
    HttpResponseNotFound,
)
from django.http.response import HttpResponsePermanentRedirect
from django.views.decorators.http import require_http_methods
from django.utils.translation import gettext_lazy as _

from arxiv.identifier import (
    Identifier,
    IdentifierException,
    IdentifierIsArchiveException,
)
from arxiv.taxonomy.definitions import GROUPS, CATEGORIES
from arxiv.integration.fastly.headers import add_surrogate_key

from .controllers import abs_page
from .controllers import archive_page, list_page, catchup_page, year as year_controller
from .controllers import check_supplied_identifier
from .utils import get_translation_dict


def home(request):
    if get_language() == 'zh-hans':
        translation_dict = get_translation_dict()
    else:
        translation_dict = {}

    context = {
        'groups': GROUPS,
        'categories': CATEGORIES,
        'translation_dict': translation_dict
    }
    return render(request, 'articles/home.html', context)

def archive(request, archive_id: Optional[str] = None):
    """Landing page for an archive."""
    if get_language() == 'zh-hans':
        translation_dict = get_translation_dict()
    else:
        translation_dict = {}

    response, code, headers = archive_page.get_archive(archive_id)
    response['translation_dict'] = translation_dict

    if code == HTTPStatus.OK or code == HTTPStatus.NOT_FOUND:
        return render(request, response['template'], response, status=code)
    elif code == HTTPStatus.MOVED_PERMANENTLY:
        return HttpResponsePermanentRedirect(headers['Location'])
    elif code == HTTPStatus.NOT_MODIFIED:
        return HttpResponse('', status=code)

    return HttpResponse(response, status=code)

def search_archive(request, archive_id):
    """Redirect to arXiv search page for the given archive."""
    return redirect(f'https://arxiv.org/search/{archive_id}')

def search(request):
    """Redirect to arXiv search page."""
    url = 'https://arxiv.org/search'
    i = 0
    for key, val in request.GET.items():
        if i == 0:
            url += f'?{key}={val}'
        else:
            url += f'&{key}={val}'
        i += 1
    return redirect(url)

def search_advanced(request):
    """Redirect to arXiv advanced search page."""
    return redirect(f'https://arxiv.org/search/advanced')

def help(request):
    """Redirect to arXiv help page."""
    return redirect(f'https://info.arxiv.org/help')

def help_archive_description(request, archive_id):
    return redirect(f'https://info.arxiv.org/help/{archive_id}/index.html')

def year_default(request, archive: str):
    """Year's stats for an archive."""
    response, code, headers = year_controller.year_page(archive, None)

    if code == HTTPStatus.TEMPORARY_REDIRECT:
        return HttpResponse('', status=code, headers=headers)
    elif code == HTTPStatus.MOVED_PERMANENTLY:
        return HttpResponsePermanentRedirect(headers["Location"])

    if get_language() == 'zh-hans':
        translation_dict = get_translation_dict()
    else:
        translation_dict = {}

    response['translation_dict'] = translation_dict
    return render(request, "articles/year.html", response, status=code)

def year_view(request, archive: str, year: int):
    """Year's stats for an archive."""
    response, code, headers = year_controller.year_page(archive, year)

    if code == HTTPStatus.TEMPORARY_REDIRECT:
        return HttpResponse('', status=code, headers=headers)
    elif code == HTTPStatus.MOVED_PERMANENTLY:
        return HttpResponsePermanentRedirect(headers["Location"])

    if get_language() == 'zh-hans':
        translation_dict = get_translation_dict()
    else:
        translation_dict = {}

    response['translation_dict'] = translation_dict
    return render(request, "articles/year.html", response, status=code)

@require_http_methods(["GET", "POST"])
def list_articles(request, context: str = '', subcontext: str = '') -> HttpResponse:
    """
    List articles by context, month etc.

    Context might be a context or an archive; Subcontext should be
    'recent', 'new' or a string of format YYMM.
    """
    # Get pagination parameters from query string
    skip = int(request.GET.get('skip', 0))
    show = int(request.GET.get('show', 25))  # Default to 25 items per page

    # Add pagination parameters to the request object for the controller
    request.skip = skip
    request.show = show

    response, code, headers = list_page.get_listing(request, context, subcontext)
    headers = add_surrogate_key(headers, ["list"])

    if code == HTTPStatus.OK:
        if request.method == "HEAD":
            return HttpResponse('', status=code, headers=headers)

        # Add pagination info and context to the response
        if 'articles' in response:
            total_count = len(response['articles'])
            response.update({
                'pagination': {
                    'skip': skip,
                    'show': show,
                    'total': total_count,
                    'has_previous': skip > 0,
                    'has_next': skip + show < total_count,
                    'previous_skip': max(0, skip - show),
                    'next_skip': skip + show,
                },
                'context': context,
                'subcontext': subcontext,
            })

            # Slice the articles list according to pagination parameters
            response['articles'] = response['articles'][skip:skip + show]

        if get_language() == 'zh-hans':
            translation_dict = get_translation_dict()
        else:
            translation_dict = {}

        response['translation_dict'] = translation_dict
        return render(request, response["template"], response, status=code)
    elif code == HTTPStatus.MOVED_PERMANENTLY:
        return HttpResponsePermanentRedirect(headers["Location"])
    elif code == HTTPStatus.NOT_MODIFIED:
        return HttpResponse('', status=code, headers=headers)

    return HttpResponse(response, status=code, headers=headers)

def catchup_form(request):
    """Display the catchup form."""
    if request.method != "GET":
        return HttpResponse('Method not allowed', status=405)

    response, code, headers = catchup_page.get_catchup_form(request)

    if code == HTTPStatus.OK:
        if get_language() == 'zh-hans':
            translation_dict = get_translation_dict()
        else:
            translation_dict = {}

        response['translation_dict'] = translation_dict
        return render(request, "articles/catchup_form.html", response, status=code)

    return HttpResponse(response, status=code, headers=headers)

def catchup(request, subject: str, date: str):
    """Display the catchup page for a given subject and date."""
    if request.method != "GET":
        return HttpResponse('Method not allowed', status=405)

    response, code, headers = catchup_page.get_catchup_page(request, subject, date)
    headers = add_surrogate_key(headers, ["catchup"])

    if code == HTTPStatus.OK:
        if get_language() == 'zh-hans':
            translation_dict = get_translation_dict()
        else:
            translation_dict = {}

        response['translation_dict'] = translation_dict
        return render(request, "articles/catchup.html", response, status=code)

    return HttpResponse(response, status=code, headers=headers)

def author_search(request, article_id: str, author_id: str):
    """Search for articles by a specific author."""

    return redirect(f'https://arxiv.org/search/{archive_id}')

    # if request.method != "GET":
    #     return HttpResponse('Method not allowed', status=405)

    # response, code, headers = get_author_search(article_id, author_id)
    # headers = add_surrogate_key(headers, ["author"])

    # if code == HTTPStatus.OK:
    #     if request.method == "HEAD":
    #         return HttpResponse('', status=code, headers=headers)
    #     return render(request, "articles/author_search.html", response, status=code)
    # elif code == HTTPStatus.MOVED_PERMANENTLY:
    #     return HttpResponsePermanentRedirect(headers["Location"])
    # elif code == HTTPStatus.NOT_MODIFIED:
    #     return HttpResponse('', status=code, headers=headers)

    # return HttpResponse(response, status=code, headers=headers)


def redirect_pdf(request, arxiv_id: str, archive: str = None):
    """Redirect URLs with .pdf to a URL without.

    In past a redirect from /pdf/paperid to /pdf/paperid.pdf was used to cause
    the browser to download the file with the .pdf extension. This is to support
    any user who has bookmarked or saved a PDF URL with the .pdf extension in
    the past so they will get a redirect to the "normal" PDF URL.

    Now content-disposition is used to specify the filename on download.
    """
    if archive:
        arxiv_id = f"{archive}/{arxiv_id}"

    # Construct the URL for the non-pdf endpoint
    url_name = 'articles:pdf_with_archive' if '/' in arxiv_id else 'articles:pdf'
    if '/' in arxiv_id:
        archive, paper_id = arxiv_id.split('/', 1)
        url = reverse(url_name, kwargs={'archive': archive, 'arxiv_id': paper_id})
    else:
        url = reverse(url_name, kwargs={'arxiv_id': arxiv_id})

    return redirect(url, permanent=True)

def bare_abs(request):
    """Check several legacy request parameters."""
    if request.GET:
        if "id" in request.GET:
            return abstract(request, request.GET["id"])
        elif "archive" in request.GET and "papernum" in request.GET:
            return abstract(request, f"{request.GET['archive']}/{request.GET['papernum']}")
        else:
            for param in request.GET:
                # singleton case, where the parameter is the value
                # e.g. /abs?<archive>/\d{7}
                if not request.GET[param] and re.match(
                    r"^[a-z\-]+(\.[A-Z]{2})?\/\d{7}$", param
                ):
                    return abstract(request, param)

    # Return abs-specific 404
    raise Http404("Abstract not found")

def abstract(request, arxiv_id: str = ''):
    """Abstract (abs) page view."""
    response, code, headers = abs_page.get_abs_page(request, arxiv_id)
    headers = add_surrogate_key(headers, ["abs"])

    if code == HTTPStatus.OK:
        if request.GET and "fmt" in request.GET and request.GET["fmt"] == "txt":
            return HttpResponse(
                response["abs_meta"].raw(),
                content_type="text/plain",
                headers=headers
            )

        if get_language() == 'zh-hans':
            translation_dict = get_translation_dict()
        else:
            translation_dict = {}

        response['translation_dict'] = translation_dict
        return render(request, "abs/abs.html", response, status=code)
    elif code == HTTPStatus.MOVED_PERMANENTLY:
        return HttpResponsePermanentRedirect(headers["Location"])
    elif code == HTTPStatus.NOT_MODIFIED:
        return HttpResponse('', status=code, headers=headers)

    raise BadRequest("Unexpected error")

def previous_next(request):
    """Previous/Next navigation used on /abs page."""

    # Validate required parameters
    if not all(param in request.GET for param in ['id', 'function', 'context']):
        raise BadRequest("Missing required parameters")

    try:
        # Construct arXiv prevnext URL
        arxiv_url = 'https://arxiv.org/prevnext'
        params = {
            'id': request.GET.get('id'),
            'function': request.GET.get('function'),
            'context': request.GET.get('context')
        }

        # Make request to arXiv
        response = requests.get(arxiv_url, params=params, allow_redirects=False)

        if response.status_code == 301:
            # Get redirect location from arXiv response
            arxiv_redirect = response.headers['Location']

            # Convert arXiv URL to our URL format
            our_url = arxiv_redirect.replace('https://arxiv.org', '')

            # Return redirect response
            return HttpResponsePermanentRedirect(our_url)

        else:
            raise BadRequest("Unable to get prev/next paper")

    except Exception as e:
        # logger.error(f"Error in previous_next view: {str(e)}")
        raise BadRequest("Error processing prev/next request")

def bibtex(request, arxiv_id: str):
    """Generate or retrieve the BibTeX entry for an article."""
    try:
        # Construct the URL to fetch the BibTeX entry from arXiv
        arxiv_bibtex_url = f'https://arxiv.org/bibtex/{arxiv_id}'

        # Fetch the BibTeX entry
        response = requests.get(arxiv_bibtex_url)

        if response.status_code == 200:
            # Return the BibTeX entry as plain text
            return HttpResponse(response.text, content_type='text/plain')
        else:
            return HttpResponseNotFound('BibTeX entry not found')

    except requests.RequestException as e:
        return HttpResponseBadRequest(f"Error fetching BibTeX entry: {str(e)}")

def pdf(request, arxiv_id: str, archive: str = None):
    """Handle PDF requests.

    Patterns:
        /pdf/{archive}/{id}v{v}
        /pdf/{id}v{v}

    The dissemination service does not handle versionless
    requests. The version should be figured out in some other service
    and redirected to the CDN.

    Returns:
        400 if the ID is malformed or lacks a version
        404 if the key for the ID does not exist on the bucket
    """
    # Strip any query parameters by redirecting
    if request.GET:
        url_name = 'articles:pdf_with_archive' if archive else 'articles:pdf'
        kwargs = {'arxiv_id': arxiv_id}
        if archive:
            kwargs['archive'] = archive
        return redirect(reverse(url_name, kwargs=kwargs), permanent=True)

    try:
        pdf_url = request.get_full_path()
        pdf_url = pdf_url.replace('/en', '')
        pdf_url = pdf_url.replace('/zh-hans', '')
        arxiv_pdf_url = 'https://arxiv.org' + pdf_url

        # Redirect to arXiv pdf page
        return HttpResponsePermanentRedirect(arxiv_pdf_url)

        # response = requests.get(arxiv_pdf_url, stream=True)

        # # Create a Django response with the PDF content
        # django_response = HttpResponse(response.content, content_type='application/pdf')

        # # Set content disposition and filename
        # filename = f"{arxiv_id.replace('/', '_')}.pdf"
        # django_response['Content-Disposition'] = f'inline; filename="{filename}"'

        # return django_response

    except ValueError as e:
        return HttpResponseBadRequest(str(e))
    except FileNotFoundError:
        return HttpResponseNotFound('PDF not found')

def cn_pdf(request, arxiv_id: str, archive: str = None):
    """Handle requests for the Chinese PDF version of an article.

    Args:
        request: The HTTP request.
        arxiv_id: The arXiv identifier.
        archive: Optional archive prefix.

    Returns:
        HttpResponse with a message indicating the availability of the Chinese PDF.
    """
    return HttpResponse(_('Chinese PDF is coming soon...'))

def html(request, arxiv_id: str, archive: str = None):
    """Get HTML for article.

    Args:
        request: The HTTP request
        arxiv_id: The arXiv identifier
        archive: Optional archive prefix

    Returns:
        HttpResponse with the HTML content from arXiv
    """
    try:
        # Get the full path and clean it
        html_url = request.get_full_path()
        html_url = html_url.replace('/en', '')
        html_url = html_url.replace('/zh-hans', '')

        # Construct arXiv URL
        arxiv_html_url = 'https://arxiv.org' + html_url

        # Redirect to arXiv HTML page
        return HttpResponsePermanentRedirect(arxiv_html_url)

        # # Get the content from arXiv
        # response = requests.get(arxiv_html_url, stream=True)

        # if response.status_code == 404:
        #     return HttpResponseNotFound('Article not found')

        # # Create Django response with the HTML content
        # django_response = HttpResponse(
        #     content=response.content,
        #     content_type=response.headers.get('Content-Type', 'text/html')
        # )

        # # Copy relevant headers from arXiv response
        # for header in ['Content-Language', 'Cache-Control', 'Last-Modified', 'ETag']:
        #     if header in response.headers:
        #         django_response[header] = response.headers[header]

        # return django_response

    except requests.RequestException as e:
        return HttpResponse(
            f'Error fetching article: {str(e)}',
            status=500
        )

def html_with_archive(request, archive: str, arxiv_id: str):
    """Wrapper for HTML view with archive prefix."""
    return html(request, arxiv_id, archive)

def dvi(request, arxiv_id: str, archive: str = None):
    """Get DVI for article.

    For now, redirect to arXiv's DVI page since we don't have
    our own DVI service yet.
    """
    try:
        # Get the full path and clean it
        dvi_url = request.get_full_path()
        dvi_url = dvi_url.replace('/en', '')
        dvi_url = dvi_url.replace('/zh-hans', '')

        # Construct arXiv URL
        arxiv_dvi_url = 'https://arxiv.org' + dvi_url

        # Redirect to arXiv DVI page
        return HttpResponsePermanentRedirect(arxiv_dvi_url)

    except Exception as e:
        return HttpResponseBadRequest(str(e))

def ps(request, arxiv_id: str):
    """Get PS for article.

    For now, redirect to arXiv's PS page since we don't have
    our own PS service yet.
    """
    try:
        # Get the full path and clean it
        ps_url = request.get_full_path()
        ps_url = ps_url.replace('/en', '')
        ps_url = ps_url.replace('/zh-hans', '')

        # Construct arXiv URL
        arxiv_ps_url = 'https://arxiv.org' + ps_url

        # Redirect to arXiv PS page
        return HttpResponsePermanentRedirect(arxiv_ps_url)

    except Exception as e:
        return HttpResponseBadRequest(str(e))

def format_view(request, arxiv_id: str, archive: Optional[str] = None):
    """Get formats for article."""
    arxiv_id = f"{archive}/{arxiv_id}" if archive else arxiv_id

    try:
        arxiv_identifier = Identifier(arxiv_id=arxiv_id)
    except IdentifierException:
        raise BadRequest(f"Bad paper identifier: {arxiv_id}")

    redirect_response = check_supplied_identifier(arxiv_identifier, "dissemination.format")
    if redirect_response:
        return redirect_response

    # Initialize data dictionary
    data = {
        "arxiv_id": arxiv_identifier.id,
        "arxiv_idv": arxiv_identifier.idv,
        "encrypted": False,  # Will be set when abs_meta is implemented
    }

    # Add surrogate keys to headers
    headers = {}
    headers = add_surrogate_key(headers, ["format", f"paper-id-{arxiv_identifier.id}"])
    if arxiv_identifier.has_version:
        headers = add_surrogate_key(headers, [f"paper-id-{arxiv_identifier.idv}"])
    else:
        headers = add_surrogate_key(headers, [f"paper-id-{arxiv_identifier.id}-current"])

    # TODO: Implement abs_meta integration
    # abs_meta = get_doc_service().get_abs(arxiv_id)
    # data["abs_meta"] = abs_meta
    # data["encrypted"] = abs_meta.get_requested_version().source_flag.source_encrypted
    # formats = data["formats"] = abs_meta.get_requested_version().formats()

    url = f'https://arxiv.org/format/{arxiv_identifier.idv}'
    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'lxml')
    dts = soup.find('dl').find_all('dt')
    for dt in dts:
        if 'PDF' in dt.text:
            data['pdf'] = True
        if 'PostScript' in dt.text:
            data['ps'] = True
        if 'DVI' in dt.text:
            data['dvi'] = True
        if 'HTML' in dt.text:
            data['html'] = True
        if 'DOCX' in dt.text:
            data['docx'] = True
        if 'Source' in dt.text:
            data['src'] = True

    response = render(request, "articles/format.html", data, status=200)

    # Set headers on the response
    for key, value in headers.items():
        response[key] = value

    return response

def anc_listing(request, arxiv_id: str):
    """Serves listing of ancillary files for a paper."""
    data = {
        'arxiv_id': arxiv_id,
        'anc_file_list': [],  # This should be populated with actual ancillary files
    }

    # TODO: Implement logic to fetch ancillary files
    # data['anc_file_list'] = get_article_store().get_ancillary_files(arxiv_id)

    headers = {}
    headers = add_surrogate_key(headers, ["anc", f"paper-id-{arxiv_id}"])
    if _check_id_for_version(arxiv_id):
        headers = add_surrogate_key(headers, [f"paper-id-{arxiv_id}"])
    else:
        headers = add_surrogate_key(headers, [f"paper-id-{arxiv_id}-current"])

    if data['anc_file_list']:
        response = render(request, "src/listing.html", data, status=200)
    else:
        response = render(request, "src/listing_none.html", data, status=404)

    for key, value in headers.items():
        response[key] = value

    return response

def anc(request, arxiv_id: str, file_path: str):
    """Serves ancillary files or shows HTML page of ancillary files."""
    try:
        # TODO: Implement logic to fetch the ancillary file
        # file_response = get_extracted_src_file_resp(arxiv_id, f"anc/{file_path}", 'anc')
        # return file_response

        # For now, redirect to arXiv's ancillary file page
        arxiv_anc_url = f'https://arxiv.org/src/{arxiv_id}/anc/{file_path}'
        return HttpResponsePermanentRedirect(arxiv_anc_url)

    except Exception as e:
        return HttpResponseBadRequest(str(e))

def src(request, arxiv_id_str: str, archive: Optional[str] = None):
    """Serves the source of a requested paper."""
    try:
        # TODO: Implement logic to fetch the source file
        # resp = get_src_resp(arxiv_id_str, archive)
        # if _check_id_for_version(arxiv_id_str):
        #     resp.headers = add_surrogate_key(resp.headers, ["src", f"paper-id-{arxiv_id_str}"])
        # else:
        #     resp.headers = add_surrogate_key(resp.headers, ["src", f"paper-id-{arxiv_id_str}-current"])
        # return resp

        # For now, redirect to arXiv's source page
        arxiv_src_url = f'https://arxiv.org/src/{arxiv_id_str}'
        if archive:
            arxiv_src_url = f'https://arxiv.org/src/{archive}/{arxiv_id_str}'
        return HttpResponsePermanentRedirect(arxiv_src_url)

    except Exception as e:
        return HttpResponseBadRequest(str(e))

def _check_id_for_version(arxiv_id_str:str) -> bool:
    """returns true if the url was asking for a specific paper version, false otherwise"""
    try:
        arxiv_id= Identifier(arxiv_id_str)
        if arxiv_id.has_version:
            return True
        else:
            return False
    except IdentifierException:
        return False


def show_endorsers(request, arxiv_id: str):
    """Show endorsers for an article.

    For now, redirect to arXiv's endorser page since we don't have
    our own endorsement system yet.
    """
    arxiv_url = f'https://arxiv.org/auth/show-endorsers/{arxiv_id}'
    return HttpResponseRedirect(arxiv_url)

def help_mathjax(request):
    """Redirect to arXiv's MathJax help page."""
    return redirect('https://info.arxiv.org/help/mathjax.html')

def show_email(request, show_email_hash: str, arxiv_id: str):
    """Show the email for the submitter for an article.

    For now, redirect to arXiv's email display page since we don't have
    our own email display system yet.
    """
    try:
        # Get the full path and clean it
        email_url = request.get_full_path()
        email_url = email_url.replace('/en', '')
        email_url = email_url.replace('/zh-hans', '')

        # Construct arXiv URL
        arxiv_email_url = 'https://arxiv.org' + email_url

        # Redirect to arXiv email page
        return HttpResponsePermanentRedirect(arxiv_email_url)

    except Exception as e:
        return HttpResponseBadRequest(str(e))


def multi(request):
    """Redirect to the arXiv multi search page with query parameters."""
    group = request.GET.get('group', '')
    form = request.GET.get('/form', '')

    # Construct the redirect URL
    redirect_url = f'https://arxiv.org/multi?group={group}&/form={form}'
    return HttpResponseRedirect(redirect_url)