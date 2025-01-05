from django.shortcuts import render, redirect
from django.utils.translation import get_language
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponsePermanentRedirect
from django.views.decorators.http import require_http_methods
from http import HTTPStatus
from typing import Optional, Tuple, Dict, Any, Union
from .models import Article
from .utils import fetch_arxiv_feed, parse_arxiv_feed
from arxiv.taxonomy.definitions import GROUPS, CATEGORIES
from arxiv.integration.fastly.headers import add_surrogate_key
from .controllers import archive_page, list_page, catchup_page, year as year_controller
# from .forms import ByMonthForm
# from .controllers.list_page.author import get_author_search


def home(request):
    context = {
        'groups': GROUPS,
        'categories': CATEGORIES,
    }
    return render(request, 'articles/home.html', context)

def archive(request, archive_id: Optional[str] = None):
    """Landing page for an archive."""
    # response, code, headers = get_archive_response(archive_id)
    response, code, headers = archive_page.get_archive(archive_id)

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

def help_archive_description(request, archive_id):
    # Placeholder: redirects to home page
    return redirect(f'https://info.arxiv.org/help/{archive_id}/index.html')

def article_list(request):
    # 获取当前语言
    current_language = get_language()

    # 获取搜索查询参数
    query = request.GET.get('q', '')
    category = request.GET.get('category', '')

    # Initialize the date filter form
    # date_form = ByMonthForm(request.GET)
    date_filters = {}
    # if date_form.is_valid():
    #     date_filters = date_form.get_date_filter()

    # 从数据库获取文章
    articles = Article.objects.all()

    # 如果数据库为空，从 arXiv 获取数据
    if not articles.exists():
        xml_content = fetch_arxiv_feed()
        parsed_articles = parse_arxiv_feed(xml_content)
        print(f'parsed_articles: {parsed_articles}')

        # 保存到数据库
        for article_data in parsed_articles:
            Article.objects.create(**article_data)

        articles = Article.objects.all()

    # 应用搜索过滤
    if query:
        articles = articles.filter(
            Q(title_en__icontains=query) |
            Q(title_cn__icontains=query) |
            Q(abstract_en__icontains=query) |
            Q(abstract_cn__icontains=query) |
            Q(authors__icontains=query) |
            Q(arxiv_id__icontains=query)
        )

    if category:
        articles = articles.filter(category__icontains=category)

    # Apply date filters
    if date_filters:
        articles = articles.filter(**date_filters)

    for article in articles:
        article.title_in_language = article.get_title(current_language)
        article.abstract_in_language = article.get_abstract(current_language)

    # 分页
    paginator = Paginator(articles, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # 获取所有可用的分类
    categories = Article.objects.values_list('category', flat=True).distinct()

    context = {
        'page_obj': page_obj,
        'query': query,
        'category': category,
        'categories': categories,
        # 'date_form': date_form,
    }

    return render(request, 'articles/article_list.html', context)

def year_default(request, archive: str):
    """Year's stats for an archive."""
    response, code, headers = year_controller.year_page(archive, None)

    if code == HTTPStatus.TEMPORARY_REDIRECT:
        return HttpResponse('', status=code, headers=headers)
    elif code == HTTPStatus.MOVED_PERMANENTLY:
        return HttpResponsePermanentRedirect(headers["Location"])

    return render(request, "articles/year.html", response, status=code)

def year_view(request, archive: str, year: int):
    """Year's stats for an archive."""
    response, code, headers = year_controller.year_page(archive, year)

    if code == HTTPStatus.TEMPORARY_REDIRECT:
        return HttpResponse('', status=code, headers=headers)
    elif code == HTTPStatus.MOVED_PERMANENTLY:
        return HttpResponsePermanentRedirect(headers["Location"])

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

    response, code, headers = catchup_page.get_catchup_form()

    if code == HTTPStatus.OK:
        return render(request, "articles/catchup_form.html", response, status=code)

    return HttpResponse(response, status=code, headers=headers)

def catchup(request, subject: str, date: str):
    """Display the catchup page for a given subject and date."""
    if request.method != "GET":
        return HttpResponse('Method not allowed', status=405)

    response, code, headers = catchup_page.get_catchup_page(subject, date)
    headers = add_surrogate_key(headers, ["catchup"])

    if code == HTTPStatus.OK:
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