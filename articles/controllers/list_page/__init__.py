"""Handle requests for the /list pages.

/list requests will show a list of articles for a category for a given
time period.

The primary entrypoint to this module is :func:`.get_list_page`, which
handles GET and POST requests to the list endpoint.

This should handle requests like:
/list/$category/YYYY
/list/$category/YYYY-MM
/list/$category/new|recent|current|pastweek
/list/$archive/new|recent|current|pastweek
/list/$archive/YY
/list/$category/YY should redirect to /YYYY

And all of the above with ?skip=n&show=n
Examples of odd requests to throw out:
/list/?400
/list/cs/14?skip=%25CRAZYSTUFF
/list/1801.00023

1. Figure out what category and time_period is being requested. It's
either a POST or GET with params about what to get OR it's all in the
path.

Things to figure out:
A: what subject category is being requested
B: time period aka listing_type: 'pastweek' 'new' 'current' 'pastyear'
C: show_abstracts only if listing_type='new'

2. Query the listing service for that category and time_period

3. Check for not modified.

4. Display the page

Differences from legacy arxiv:
Doesn't handle the /view path.
"""
import time
import glob
import calendar
import logging
import math
from datetime import date, datetime
from http import HTTPStatus as status
from typing import Any, Dict, List, Optional, Tuple, Union
import re
import itertools
# import requests
from bs4 import BeautifulSoup
from celery import group
import concurrent.futures

import arxivapi  # The PyPI arxiv package

# From arxiv-base package
from arxiv.taxonomy.definitions import CATEGORIES, ARCHIVES_SUBSUMED, ARCHIVES
from arxiv.integration.fastly.headers import add_surrogate_key
from arxiv.document.version import VersionEntry#, SourceFlag
from arxiv.document.metadata import DocMetadata, AuthorList as AuList
from arxiv.formats import formats_from_source_flag

from browse.controllers.abs_page import truncate_author_list_size
# from browse.controllers.list_page.paging import paging
from browse.formatting.search_authors import AuthorList, queries_for_authors, split_long_author_list
from browse.services.documents import get_doc_service
from browse.services.listing import Listing, ListingNew, NotModifiedResponse, get_listing_service, ListingItem, gen_expires
# from browse.services.listing import db_listing
from browse.formatting.latexml import get_latexml_url, get_latexml_urls_for_articles

from django.urls import reverse
from django.conf import settings
from django.http import HttpResponseBadRequest
from django.core.cache import cache
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from .paging import paging
from ...models import Article#, Author, Category, Link
from ...tasks import download_and_compile_arxiv
from ...templatetags import article_filters
from ...utils import get_translation_dict, chinese_week_days, request_get, translate_and_save_article
# from ...translators import translator


logger = logging.getLogger(__name__)

show_values = [25, 50, 100, 250, 500, 1000, 2000]
"""" Values of $show for more/fewer/all."""

year_month_pattern = re.compile(r'^\d{4}-\d{1,2}$')

min_show = 25
max_show = show_values[-1]
"""Max value for show that controller respects."""

default_show = show_values[1]
"""Default value for show."""

Response = Tuple[Dict[str, Any], int, Dict[str, Any]]

type_to_template = {
    'new': 'list/new.html',
    'recent': 'list/recent.html',
    'current': 'list/month.html',
    'month': 'list/month.html',
    'year': 'list/year.html',
    'all': 'list/all.html'
}

class ListingSection:
    heading: str
    day: str
    items: List[ListingItem]
    total: int
    continued: bool
    last: bool
    visible: bool

    def __init__(self, day: str, items: List[ListingItem], total: int,
                continued: bool, last: bool, visible: bool, heading: str):
        self.day = day
        self.items = items
        self.total = total
        self.continued = continued
        self.last = last
        self.visible = visible
        self.heading = heading

    def __repr__(self) -> str:
        return f"<ListingSection {self.heading}>"

def get_listing(request, subject_or_category: str, time_period: str, skip: str = '', show: str = '') -> Response:
    """
    Handle requests to list articles.

    Parameters
    ----------
    subject_or_category
       Subject or categtory to get listing for.
    time_period
       YYYY or YYYY-MM or YY or'recent' or 'pastweek' or 'new' or 'current'.
       recent and pastweek mean the last 5 listings,
       new means the most recent listing,
       current means the listings for the current month.
    skip
       Number of articles to skip for this subject and time_period.
    show
       Number of articles to show
    """
    skip = skip or request.GET.get('skip', '0')
    show = show or request.GET.get('show', '')
    if request.GET.get('archive', None) is not None:
        subject_or_category = request.GET.get('archive')
    if request.GET.get('year', None):
        time_period = request.GET.get('year')
        month = request.GET.get('month', None)
        if month and month != 'all':
            time_period = time_period + "-" + request.GET.get('month')


    if (
        not subject_or_category or
        not (
            time_period and
             (time_period.isdigit()
              or time_period in ['new', 'current', 'pastweek', 'recent', 'all']
              or year_month_pattern.match(time_period)
              )
        )
    ):
        return HttpResponseBadRequest("Listing requires subject and valid time period parameters.")

    if subject_or_category in ARCHIVES_SUBSUMED:
        subject_or_category = ARCHIVES_SUBSUMED[subject_or_category]

    if subject_or_category in CATEGORIES:
        list_type = 'category'
        cat = CATEGORIES[subject_or_category]
        category = cat.get_canonical() # make sure we use the canonical version of the category
        list_ctx_name = category.full_name
        list_ctx_id = category.id
        list_ctx_in_archive = category.in_archive
    elif subject_or_category in ARCHIVES:
        list_type = 'archive'
        archive = ARCHIVES[subject_or_category]
        list_ctx_id = archive.id
        list_ctx_name = archive.full_name
        list_ctx_in_archive = archive.id
    else:
        return HttpResponseBadRequest(f"Invalid archive or category: {subject_or_category}")

    language = get_language()

    if not skip or not skip.isdigit():
        skipn = 0
    else:
        skipn = int(skip)

    if not show or not show.isdigit():
        if time_period == 'new':
            shown = max_show
        else:
            shown = default_show
    else:
        shown = max(min(int(show), max_show), min_show)

    if_mod_since = request.META.get('HTTP_IF_MODIFIED_SINCE', None)

    response_data: Dict[str, Any] = {}
    response_headers: Dict[str, Any] = {}

    if time_period == 'new':
        list_type = 'new'
        response_headers = add_surrogate_key(response_headers, ["list-new", "announce", f"list-new-{list_ctx_id}"])

        # items, dts, dds = get_new_listing(request, list_ctx_id, skipn, shown)
        items = get_new_listing(request, list_ctx_id, skipn, shown)
        if _check_modified(items.listings, if_mod_since):
            new_resp = NotModifiedResponse(True, gen_expires())
        else:
            new_resp = items

        response_headers.update(_expires_headers(new_resp))
        if isinstance(new_resp, NotModifiedResponse):
            return {}, status.NOT_MODIFIED, response_headers
        listings = new_resp.listings
        count = new_resp.new_count + new_resp.rep_count + new_resp.cross_count
        response_data['announced'] = new_resp.announced.strftime("%Y年%m月%d日") + f'， {chinese_week_days[new_resp.announced.weekday()]}' if language == 'zh-hans' else new_resp.announced.strftime('%A, %-d %B %Y')
        response_data.update(index_for_types(new_resp, list_ctx_id, time_period, skipn, shown))
        # response_data.update(sub_sections_for_types((new_resp, dts, dds), skipn, shown))
        response_data.update(sub_sections_for_types(new_resp, skipn, shown))

    elif time_period in ['pastweek', 'recent']:
        # A bit different due to returning days not listings
        list_type = 'recent'
        response_headers = add_surrogate_key(response_headers, ["list-recent", "announce", f"list-recent-{list_ctx_id}"])

        # items, dts, dds = get_recent_listing(request, list_ctx_id, skipn, shown)
        items = get_recent_listing(request, list_ctx_id, skipn, shown)
        if _check_modified(items.listings, if_mod_since):
            rec_resp = NotModifiedResponse(True, gen_expires())
        else:
            rec_resp = items

        response_headers.update(_expires_headers(rec_resp))
        if isinstance(rec_resp, NotModifiedResponse):
            return {}, status.NOT_MODIFIED, response_headers
        listings = rec_resp.listings
        count = rec_resp.count
        # response_data['pubdates'] = rec_resp.pubdates
        pubdates = []
        start = 0
        for day, number in rec_resp.pubdates:
            start += number
            if language == 'zh-hans':
                pubdates.append((day.strftime("%Y年%m月%d日") + f'， {chinese_week_days[day.weekday()]}', start-number))
            else:
                pubdates.append((day.strftime('%a, %-d %b %Y'), start-number))
        response_data['pubdates'] = pubdates
        # response_data.update(sub_sections_for_recent((rec_resp, dts, dds), skipn, shown))
        response_data.update(sub_sections_for_recent(rec_resp, skipn, shown))

    elif time_period == 'all':
        list_type = 'all'

        items = get_all_cn_pdfs(request, skipn, shown)
        count = items.count
        if _check_modified(items.listings, if_mod_since):
            all_resp = NotModifiedResponse(True, gen_expires())
        else:
            all_resp = items

        response_headers.update(_expires_headers(all_resp))
        if isinstance(all_resp, NotModifiedResponse):
            return {}, status.NOT_MODIFIED, response_headers
        listings = all_resp.listings

    else:  # current or YYMM or YYYYMM or YY

        yandm = year_month(time_period)
        response_headers = add_surrogate_key(response_headers, ["list-ym"])

        if yandm is None:
            raise Http404(f"Invalid time period: {time_period}")
        should_redir, list_year, list_month = yandm
        if list_year < 1990:
            raise Http404(f"Invalid Year: {list_year}")
        if list_year > date.today().year:
            raise Http404(f"Invalid Year: {list_year}") # not BadRequest, might be valid in future

        if should_redir:
            if list_month:
                new_time = f"{list_year:04d}-{list_month:02d}"
            else:
                new_time = f"{list_year:04d}"
            new_address = reverse('articles:list_articles', kwargs={'context': list_ctx_id, 'subcontext': new_time})
            response_headers["Location"] = new_address
            return {}, status.MOVED_PERMANENTLY, response_headers

        response_data['list_time'] = time_period # doesnt appear to be used
        response_data['list_year'] = str(list_year)
        if list_month or list_month == 0:
            if list_month < 1 or list_month > 12:
                raise Http404(f"Invalid month: {list_month}")
            list_type = 'month'
            response_headers = add_surrogate_key(response_headers, [f"list-{list_year:04d}-{list_month:02d}-{list_ctx_id}"])
            if date.today().year == list_year and date.today().month == list_month:
                response_headers = add_surrogate_key(response_headers, ["announce"])
            response_data['list_month'] = str(list_month)
            response_data['list_month_name'] = calendar.month_abbr[list_month]

            if list_year < 91: # in 2000s
                list_year += 2000
            elif list_year < 1900: # 90s articles
                list_year += 1900

            # items, dts, dds = get_articles_for_month(request, list_ctx_id, time_period, list_year, list_month, skipn, shown)
            items = get_articles_for_month(request, list_ctx_id, time_period, list_year, list_month, skipn, shown)
            if _check_modified(items.listings, if_mod_since):
                resp = NotModifiedResponse(True, gen_expires())
            else:
                resp = items

        else:
            list_type = 'year'
            response_headers = add_surrogate_key(response_headers, [f"list-{list_year:04d}-{list_ctx_id}"])
            if list_year == date.today().year:
                response_headers = add_surrogate_key(response_headers, ["announce"])

            if list_year < 91: # in 2000s
                list_year += 2000
            elif list_year < 1900: # 90s articles
                list_year += 1900

            # items, dts, dds = get_articles_for_month(request, list_ctx_id, time_period, list_year, None, skipn, shown)
            items = get_articles_for_month(request, list_ctx_id, time_period, list_year, None, skipn, shown)
            if _check_modified(items.listings, if_mod_since):
                resp = NotModifiedResponse(True, gen_expires())
            else:
                resp = items


        response_headers.update(_expires_headers(resp))
        if isinstance(resp, NotModifiedResponse):
            return {}, status.NOT_MODIFIED, response_headers
        listings = resp.listings
        count = resp.count
        if resp.pubdates and resp.pubdates[0]:
            # response_data['pubmonth'] = resp.pubdates[0][0]
            if list_type == 'month':
                response_data['pubmonth'] = resp.pubdates[0][0].strftime('%Y年%m月') if language == 'zh-hans' else resp.pubdates[0][0].strftime('%B %Y')
            else:
                response_data['pubmonth'] = resp.pubdates[0][0].strftime('%Y年') if language == 'zh-hans' else resp.pubdates[0][0].strftime('%Y')
        else:
            # response_data['pubmonth'] = datetime.now() # just to make the template happy
            if list_type == 'month':
                response_data['pubmonth'] = datetime.now().strftime('%Y年%m月') if language == 'zh-hans' else datetime.now().strftime('%B %Y') # just to make the template happy
            else:
                response_data['pubmonth'] = datetime.now().strftime('%Y年')  if language == 'zh-hans' else datetime.now().strftime('%Y') # just to make the template happy

    # TODO if it is a HEAD, and nothing has changed, send not modified

    idx = 0

    for item in listings:
        idx = idx + 1
        setattr(item, 'list_index', idx + skipn)
        if not hasattr(item, 'article') or item.article is None:
            setattr(item, 'article', get_doc_service().get_abs(item.id))

    response_data['listings'] = listings
    # response_data['listings'] = [ (lst, str(dt), str(dd)) for (lst, dt, dd) in zip(listings, dts, dds) ]
    response_data['author_links'] = authors_for_articles(listings)
    # response_data['downloads'] = dl_for_articles(listings)
    # response_data['latexml'] = latexml_links_for_articles(listings)
    # response_data['dts'] = dts

    response_data.update({
        'context': list_ctx_id,
        'count': count,
        'subcontext': time_period,
        'shown': shown,
        'skipn': skipn,
        'list_type': list_type,
        'list_ctx_name': list_ctx_name,
        'list_ctx_id': list_ctx_id,
        'list_ctx_in_archive': list_ctx_in_archive,
        'paging': paging(count, skipn, shown, list_ctx_id, time_period),
        'viewing_all': shown >= count,
        'template': type_to_template[list_type]
    })

    response_data.update(more_fewer(shown, count, shown >= count))

    def author_query(article: DocMetadata, query: str)->str:
        try:
            if article.primary_archive:
                archive_id = article.primary_archive.id
            elif article.primary_category:
                archive_id = article.primary_category.in_archive
            else:
                archive_id = list_ctx_in_archive
            return str(reverse('articles:search_archive', kwargs={
                'archive': archive_id,
                'searchtype': 'author',
                'query': query
            }))
        except (AttributeError, KeyError):
            return str(reverse('articles:search_archive', kwargs={
                'archive_id': list_ctx_in_archive,
                'searchtype': 'author',
                'query': query
            }))

    response_data['url_for_author_search'] = author_query

    return response_data, status.OK, response_headers

def year_month(tp: str) -> Optional[Tuple[bool, int, Optional[int]]]:
    """Gets the year and month from the time_period parameter. The boolean is if a redirect needs to be sent"""
    if tp == "current":
        day = date.today()
        return False, day.year, day.month

    if not tp or len(tp) > 7 or len(tp) < 2:
        return None

    if len(tp) == 2:  # 2 dig year gets redirected to 4 now
        year = int(tp) + 1900
        if year < 1990:
            year += 100
        return True, year, None

    if len(tp) == 4:  # 4 dig year
        return False, int(tp), None

    if len(tp) == 4+2 and tp.isdigit():  # 4 digit year, but no dash for month
        return True, int(tp[0:4]), int(tp[4:])

    if year_month_pattern.match(tp):
        if len(tp) == 4+1+1: # YYYY-M, should be MM
            return True, int(tp[0:4]), int(tp[5:])
        else: # wow perfect url
            return False, int(tp[0:4]), int(tp[5:])

    else:
        return None


def more_fewer(show: int, count: int, viewing_all: bool) -> Dict[str, Any]:
    """Links for the more/fewer sections.

    We want first show_values[n] where show_values[n] < show and
    show_values[n+1] > show
    """
    nplus1s = show_values[1:]
    n_n1_tups = map(lambda n, n1: (n, n1), show_values, nplus1s)
    tup_f = filter(lambda nt: nt[0] < show and nt[1] >= show, n_n1_tups)
    rd = {'mf_fewer': next(tup_f, (None, None))[0]}

    if not viewing_all and count > show and show < max_show:
        rd['mf_all'] = max_show

    # python lacks a find(labmda x:...) ?
    rd['mf_more'] = next(
        filter(lambda x: x > show and x < count, show_values), None)  # type: ignore

    return rd

def _src_code(article: DocMetadata)->str:
    vhs = [vh for vh in article.version_history if vh.version == article.version]
    if vhs:
        return vhs[0].source_flag.code
    else:
        return ''

def dl_for_article(article: DocMetadata)-> Dict[str, Any]:
    """Gets the download links for an article."""
    return {article.arxiv_id_v: formats_from_source_flag(_src_code(article))}

def dl_for_articles(items: List[Any])->Dict[str, Any]:
    """Gets the download links for an article."""
    return {item.article.arxiv_id_v: formats_from_source_flag(_src_code(item.article))
            for item in items}

def latexml_links_for_article (article: DocMetadata)->Dict[str, Any]:
    """Returns a Dict of article id to latexml links"""
    return {article.arxiv_id_v: get_latexml_url(article, True)}

def latexml_links_for_articles (articles: List[ListingItem]) -> Dict[str, Optional[str]]:
    """Returns a Dict of article id to latexml links"""
    return get_latexml_urls_for_articles(map(lambda x: x.article, articles))

def authors_for_article(article: DocMetadata)->Dict[str, Any]:
    """Returns a Dict of article id to author links."""
    return {article.arxiv_id_v: author_links(article)}

def authors_for_articles(listings: List[Any])->Dict[str, Any]:
    """Returns a Dict of article id to author links."""
    return {item.article.arxiv_id_v: author_links(item.article)
            for item in listings}


def author_links(abs_meta: DocMetadata) -> Tuple[AuthorList, AuthorList, int]:
    """Creates author list links in a very similar way to abs page."""
    raw = abs_meta.authors.raw
    return split_long_author_list(queries_for_authors(raw),
                                  truncate_author_list_size)


def index_for_types(resp: ListingNew,
                    context: str, subcontext: str,
                    skipn: int, shown: int) ->Dict[str, Any]:
    """Creates index for types of new papers in a ListingNew."""
    ift = []
    new_count = resp.new_count
    cross_count = resp.cross_count
    rep_count = resp.rep_count

    base_url = reverse('articles:list_articles', kwargs={'context': context, 'subcontext': subcontext})

    if new_count > 0:
        if skipn != 0:
            ift.append((_('New submissions'),
                        f'{base_url}?skip=0&show={shown}',
                        0))
        else:
            ift.append((_('New submissions'), '', 0))

    if cross_count > 0:
        cross_index = new_count + 1
        c_skip = math.floor(new_count / shown) * shown

        if new_count > shown:
            ift.append((_('Cross-lists'),
                        f'{base_url}?skip={c_skip}&show={shown}',
                        cross_index))
        else:
            ift.append((_('Cross-lists'), '', cross_index))

    if rep_count > 0:
        rep_index = new_count+cross_count + 1
        rep_skip = math.floor((new_count + cross_count)/shown) * shown
        if new_count + cross_count > shown:
            ift.append((_('Replacements'),
                        f'{base_url}?skip={rep_skip}&show={shown}',
                        rep_index))
        else:
            ift.append((_('Replacements'), '', rep_index))

    return {'index_for_types': ift}

def sub_sections_for_recent(
        resp: Listing,
        skip: int, show: int) -> Dict[str, Any]:
    """Creates data used in section headings on /list/ARCHIVE/recent."""
    # resp, dts, dds = resp
    secs: List[ListingSection] = []
    articles_passed = 0
    shown = 0

    language = get_language()

    for entry in resp.pubdates:
        day, count = entry
        skipped = max(skip - articles_passed, 0)
        to_show = max(min(count - skipped, show-shown), 0)

        if count > 0 and skip >= articles_passed + count: # section with content completely skipped
            display = False
        elif count == 0 and skip > articles_passed: # empty section overskipped
            display = False
        elif show == shown: # no room
            display = False
        else:
            display = True

        # listings = [ (resp.listings[i], str(dts[i]), str(dds[i])) for i in range(shown, shown+to_show) ]
        listings = resp.listings[shown:shown+to_show]
        sec = ListingSection(
            day=day.strftime("%Y年%m月%d日") + f'， {chinese_week_days[day.weekday()]}' if language == 'zh-hans' else day.strftime('%a, %-d %b %Y'),
            items=listings,
            total=count,
            continued=skipped > 0,
            last=skipped + to_show ==count,
            visible=display,
            heading="" # filled out later
        )

        secs.append(sec)
        articles_passed += count
        shown += to_show

    language = get_language()
    for sec in secs:
        showing = '展示 ' if language == 'zh-hans' else 'showing '
        if sec.continued:
            showing = ('继续， ' if language == 'zh-hans' else 'continued, ') + showing
            if sec.last:
                showing = showing + ('最后 ' if language == 'zh-hans' else 'last ')
        if not sec.last and not sec.continued:
            showing = showing + ('首先 ' if language == 'zh-hans' else 'first ')

        heading = sec.day
        if sec.total > 0:
            if language == 'zh-hans':
                heading += f' ({showing}{sec.total} 之 {len(sec.items)} 条目 )'
            else:
                heading += f' ({showing}{len(sec.items)} of {sec.total} entries )'
        sec.heading = heading

    return {'sub_sections_for_types': secs}

def sub_sections_for_types(resp: ListingNew, skipn: int, shown: int) -> Dict[str, Any]:
    """Creates data used in section headings on /list/ARCHIVE/new."""
    # resp, dts, dds = resp
    new_count = resp.new_count
    cross_count = resp.cross_count
    rep_count = resp.rep_count

    news = [item for item in resp.listings if item.listingType == 'new']
    crosses = [item for item in resp.listings if item.listingType == 'cross']
    reps = [item for item in resp.listings if item.listingType == 'rep']
    # news = [(item, str(dt), str(dd)) for (item, dt, dd) in zip(resp.listings, dts, dds) if item.listingType == 'new']
    # crosses = [(item, str(dt), str(dd)) for (item, dt, dd) in zip(resp.listings, dts, dds) if item.listingType == 'cross']
    # reps = [(item, str(dt), str(dd)) for (item, dt, dd) in zip(resp.listings, dts, dds) if item.listingType == 'rep']

    cross_start = new_count+1
    rep_start = new_count + cross_count + 1
    last_shown = skipn + shown

    language = get_language()
    date = resp.announced.strftime("%Y年%m月%d日") + f'， {chinese_week_days[resp.announced.weekday()]}' if language == 'zh-hans' else resp.announced.strftime('%A, %-d %B %Y')

    sec_new = ListingSection(
        day=date,
        items=news,
        total=new_count,
        continued=skipn > 0,
        last=skipn >= new_count - shown,
        visible=len(news)>0,
        heading=_('New submissions ')
    )

    sec_cross = ListingSection(
        day=date,
        items=crosses,
        total=cross_count,
        continued=skipn + 1 > cross_start,
        last=skipn >= rep_start - shown,
        visible=len(crosses)>0,
        heading=_('Cross submissions ')
    )

    sec_rep = ListingSection(
        day=date,
        items=reps,
        total=rep_count,
        continued=skipn + 1 > rep_start,
        last=last_shown >= new_count + cross_count + rep_count,
        visible=len(reps)>0,
        heading=_('Replacement submissions ')
    )

    secs = [sec_new, sec_cross, sec_rep]

    language = get_language()
    for sec in secs:
        showing = '展示 ' if language == 'zh-hans' else 'showing '
        if sec.continued:
            showing = ('继续， ' if language == 'zh-hans' else 'continued, ') + showing
            if sec.last:
                showing = showing + ('最后 ' if language == 'zh-hans' else 'last ')
        if not sec.last and not sec.continued:
            showing = showing + ('首先 ' if language == 'zh-hans' else 'first ')

        if language == 'zh-hans':
            sec.heading += f' ({showing}{sec.total} 之 {len(sec.items)} 条目 )'
        else:
            sec.heading += f' ({showing}{len(sec.items)} of {sec.total} entries )'

    return {'sub_sections_for_types': secs}


def _not_modified(response: Union[Listing, ListingNew, NotModifiedResponse]) -> bool:
    return bool(response and isinstance(response, NotModifiedResponse))


def _expires_headers(listing_resp:
                     Union[Listing, ListingNew, NotModifiedResponse]) \
                     -> Dict[str, str]:
    if listing_resp and listing_resp.expires:
        return {'Surrogate-Control': f'max-age={listing_resp.expires}'}
    else:
        return {}


def _check_modified(items: List[ListingItem], if_modified_since: Optional[str] = None)->bool:
    if if_modified_since:
        parsed = datetime.strptime(if_modified_since, '%a, %d %b %Y %H:%M:%S GMT')
        for item in items:
            if item.article and item.article.modified >=parsed:
                return True
    return False

def get_new_listing(request, archive_or_cat: str, skip: int, show: int) -> ListingNew:
    "Gets the most recent day of listings for an archive or category"
    language = get_language()

    url = request.get_full_path()
    url = url.replace('/en', '')
    url = url.replace('/zh-hans', '')
    arxiv_url = 'https://arxiv.org' + url
    retries = 3
    retry_delay = 0.5
    response = request_get(arxiv_url, retries=retries, retry_delay=retry_delay)
    if not response:
        msg = f"Failed to fetch URL: {arxiv_url} after {retries} attempts."
        # logger.error(msg)
        raise Exception(msg)
    soup = BeautifulSoup(response.content, 'lxml')
    # Find the specific h3 containing the target text
    target_h3 = soup.find('h3', string=re.compile(r'Showing new listings for'))
    # Extract the date part using regex
    match = re.search(r'Showing new listings for (.*?)$', target_h3.text.strip())
    date_text = match.group(1)
    announced = datetime.strptime(date_text, '%A, %d %B %Y')
    # print('announced:', announced)

    # get the number of total entries
    paging_div = soup.find('div', class_='paging')
    total = 0
    if paging_div:
        match = re.search(r'Total of (\d+) entries', paging_div.text)
        if match:
            total = int(match.group(1))


    new_start = 1
    cross_start = 1
    rep_start = 1

    ### new_start always start from 1
    # a_new = soup.find('a', text="New submissions")
    # if a_new:
    #     new_start = int(a_new['href'].replace('#item', ''))
    a_cross = soup.find('a', text="Cross-lists")
    if a_cross:
        cross_start = int(a_cross['href'].replace('#item', ''))
    else:
        cross_start = new_start
    a_rep = soup.find('a', text="Replacements")
    if a_rep:
        rep_start = int(a_rep['href'].replace('#item', ''))
    else:
        rep_start = cross_start

    new_count = cross_start - new_start
    cross_count = rep_start - cross_start
    rep_count = total - new_count - cross_count


    paper_ids = []
    atags = soup.find_all('a', {'title': 'Abstract'})
    for atag in atags:
        paper_ids.append(atag['id'])

    results = []
    uncached_inds = []
    uncached_pids = []
    for i, pid in enumerate(paper_ids):
        cache_key = f"{announced.strftime('%Y%m%d')}_{pid}"
        result = cache.get(cache_key)
        if result:
            results.append(result)
        else:
            results.append(None)
            uncached_inds.append(i)
            uncached_pids.append(pid)

    if len(uncached_pids) > 0:
        # Create the search client
        client = arxivapi.Client()

        # build cache
        for pids in itertools.batched(uncached_pids, 200):
            search = arxivapi.Search(id_list=pids)
            for pid, result in zip(pids, client.results(search)):
                cache_key = f"{announced.strftime('%Y%m%d')}_{pid}"
                cache.set(cache_key, result, 3*24*3600) # cache for 3 days

        # get results from cache
        for ui, pid in zip(uncached_inds, uncached_pids):
            cache_key = f"{announced.strftime('%Y%m%d')}_{pid}"
            results[ui] = cache.get(cache_key)

    # # Create the search query
    # results = []
    # # for pids in itertools.batched(paper_ids, 200):
    # #     search = arxivapi.Search(id_list=pids)
    # #     results.extend(list(client.results(search)))
    # for pid in paper_ids:
    #     cache_key = f"{announced.strftime('%Y%m%d')}_{pid}"
    #     result = cache.get(cache_key)
    #     if not result:
    #         search = arxivapi.Search(id_list=[pid])
    #         result = list(client.results(search))[0]
    #         cache.set(cache_key, result, 3*24*3600) # cache for 3 days
    #     results.append(result)

    # get arxiv_idv for all paper_ids
    # arxiv_idvs = [ result.entry_id.split('/')[-1] for result in results ]
    arxiv_idvs = [ result.entry_id.split(r'/abs/')[-1] for result in results ]
    # use celery to download and compile pdfs asynchronously
    if settings.CELERY_DOWNLOAD_AND_COMPILE_ARXIV:
        processing_group = group(download_and_compile_arxiv.s(arxiv_idv) for arxiv_idv in arxiv_idvs)
        processing_group.apply_async()


    oks = [False] * len(results)

    retry = 0
    while True:
        if retry >= retries:
            msg = f'Failed to translate some of the articles after {retries} retries'
            logger.error(msg)
            raise Exception(msg)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            results, oks = list(zip(*executor.map(translate_and_save_article, results, oks)))

        if all(oks):
            break

        # results = [ results[i] for i in range(len(oks)) if oks[i] == False ]

        delay = 1.0 * (2**retry)
        time.sleep(delay)

        retry += 1


    dts = soup.find_all('dt')
    # dds = soup.find_all('dd')
    authors_divs = soup.find_all('div', {'class': 'list-authors'})

    # organize results into expected listing
    items = []
    for i, paper_id in enumerate(paper_ids):
        if skip + i < new_count:
            listing_type = 'new'
        elif skip + i < new_count + cross_count:
            listing_type = 'cross'
        else:
            listing_type = 'rep'

        # article = Article.objects.filter(source_archive='arxiv', entry_id=paper_id).order_by('entry_version').last()
        article = results[i]

        # dt = dts[i]
        # new_a = soup.new_tag('a', attrs={'href': f"/cn-pdf/{paper_id}", 'title': "Download Chinese PDF", 'id': f"cn-pdf-{paper_id}", 'aria-labelledby': f"cn-pdf-{paper_id}"})
        # if get_language() == 'zh-hans':
        #     new_a.string = '中文pdf'
        # else:
        #     new_a.string = 'cn-pdf'
        # abstract_a = dt.find('a', {'title': 'Abstract'})
        # abstract_a.next_sibling.insert_after(new_a)
        # new_a = dt.find('a', {'id': f"cn-pdf-{paper_id}"})
        # new_a.insert_after(', ')

        # if get_language() == 'zh-hans':
        #     dd = dds[i]
        #     dd.find('div', {'class': "list-title mathjax"}).span.next_sibling.replace_with(article.title_cn)
        #     if dd.p:
        #         dd.p.string = article.abstract_cn

        arxiv_id, version = article.entry_id, article.entry_version
        primary_cat = CATEGORIES[article.primary_category]
        secondary_cats = [ CATEGORIES[sc.name] for sc in article.categories.all() if sc.name in CATEGORIES ]
        modified = max(article.updated_date, article.published_date)
        doc = DocMetadata(
            arxiv_id=arxiv_id,
            arxiv_id_v=f'{arxiv_id}v{version}',
            title=article.title_cn if language == 'zh-hans' else article.title_en,
            authors=AuList(', '.join([ author.name for author in article.authors.all() ])),
            abstract=article.abstract_cn if language == 'zh-hans' else article.abstract_en,
            categories=[ cat.name for cat in article.categories.all() ],
            primary_category=primary_cat,
            secondary_categories=secondary_cats,
            comments=article.comment_cn if language == 'zh-hans' else article.comment_en,
            journal_ref=article.journal_ref_cn if language == 'zh-hans' else article.journal_ref_en,
            version=version,
            version_history=[
                VersionEntry(
                    version=version,
                    raw="",
                    submitted_date=None, # type: ignore
                    size_kilobytes=0,
                    source_flag=''
                )
            ],
            raw_safe="",
            submitter=None, # type: ignore
            arxiv_identifier=None, # type: ignore
            primary_archive=primary_cat.get_archive(),
            primary_group=primary_cat.get_archive().get_group(),
            modified=modified
        )

        a_html = dts[i].find('a', string='html')
        if a_html:
            doc.latexml_link = a_html['href']
        a_other = dts[i].find('a', string='other')
        if a_other:
            doc.other_link = a_other['href']

        doc.authors_list = str(authors_divs[i])
        doc.primary_display = doc.primary_category.display()
        doc.secondaries_display = doc.display_secondaries()

        doc.title_other_language = article.title_en if language == 'zh-hans' else article.title_cn
        doc.abstract_other_language = article.abstract_en if language == 'zh-hans' else article.abstract_cn
        doc.show_title_text = '显示英文标题' if language == 'zh-hans' else 'Show Chinese title'
        doc.hide_title_text = '隐藏英文标题' if language == 'zh-hans' else 'Hide Chinese title'
        doc.show_abstract_text = '显示英文摘要' if language == 'zh-hans' else 'Show Chinese abstract'
        doc.hide_abstract_text = '隐藏英文摘要' if language == 'zh-hans' else 'Hide Chinese abstract'

        if language == 'zh-hans':
            translation_dict = get_translation_dict()
            # Define the regex pattern
            pattern = r'^(.*?) \((.*?)\)$'

            # Use re.search to find matches
            match = re.search(pattern, doc.primary_display)
            if match:
                # Extract the two groups
                cat_full_name = match.group(1)  # e.g. 'High Energy Astrophysical Phenomena'
                category = match.group(2)  # e.g. 'astro-ph.HE'
                cat_full_name_cn = article_filters.dict_get_key(translation_dict, cat_full_name)
                doc.primary_display = f'{cat_full_name_cn} ({category})'

            for i, secondary_display in enumerate(doc.secondaries_display):
                match = re.search(pattern, secondary_display)
                if match:
                    # Extract the two groups
                    cat_full_name = match.group(1)  # e.g. 'High Energy Astrophysical Phenomena'
                    category = match.group(2)  # e.g. 'astro-ph.HE'
                    cat_full_name_cn = article_filters.dict_get_key(translation_dict, cat_full_name)
                    doc.secondaries_display[i] = f'{cat_full_name_cn} ({category})'

        item = ListingItem(
            id=arxiv_id,
            listingType=listing_type,
            primary=primary_cat.id,
            article=doc,
        )
        items.append(item)


    return ListingNew(listings=items,
                      new_count=new_count,
                      cross_count=cross_count,
                      rep_count=rep_count,
                      announced=announced,
                      expires=gen_expires())#, dts, dds

def get_recent_listing(request, archive_or_cat: str, skip: int, show: int) -> Listing:
    language = get_language()

    url = request.get_full_path()
    url = url.replace('/en', '')
    url = url.replace('/zh-hans', '')
    arxiv_url = 'https://arxiv.org' + url
    retries = 3
    retry_delay = 0.5
    response = request_get(arxiv_url, retries=retries, retry_delay=retry_delay)
    if not response:
        msg = f"Failed to fetch URL: {arxiv_url} after {retries} attempts."
        # logger.error(msg)
        raise Exception(msg)
    soup = BeautifulSoup(response.content, 'lxml')
    paging_div = soup.find('div', class_='paging')
    total = 0
    if paging_div:
        match = re.search(r'Total of (\d+) entries', paging_div.text)
        if match:
            total = int(match.group(1))

    # Find all <a> tags that are inside <li> tags that are inside <ul> tags
    skip_dates = []
    a_links = soup.select('ul li a')
    for link in a_links:
        # Extract skip number
        href = link.get('href', '')
        skip_match = re.search(r'skip=(\d+)', href)
        if skip_match:
            skip_number = int(skip_match.group(1))
            # Extract date
            date_text = link.text.strip()
            try:
                date = datetime.strptime(date_text, '%a, %d %b %Y')
                skip_dates.append((skip_number, date))
            except ValueError as e:
                print(f"Error parsing date '{date_text}': {e}")

    daily_counts = []
    num_dates = len(skip_dates)
    for i in range(num_dates):
        next_skip = skip_dates[i+1][0] if i < num_dates - 1 else total
        day, number = skip_dates[i][1], next_skip - skip_dates[i][0]
        daily_counts.append((day, number))

    paper_ids = []
    atags = soup.find_all('a', {'title': 'Abstract'})
    for atag in atags:
        paper_ids.append(atag['id'])

    # Create the search client
    client = arxivapi.Client()

    # Create the search query
    results = []
    for pids in itertools.batched(paper_ids, 200):
        search = arxivapi.Search(id_list=pids)
        results.extend(list(client.results(search)))

    # get arxiv_idv for all paper_ids
    # arxiv_idvs = [ result.entry_id.split('/')[-1] for result in results ]
    arxiv_idvs = [ result.entry_id.split(r'/abs/')[-1] for result in results ]
    # use celery to download and compile pdfs asynchronously
    if settings.CELERY_DOWNLOAD_AND_COMPILE_ARXIV:
        processing_group = group(download_and_compile_arxiv.s(arxiv_idv) for arxiv_idv in arxiv_idvs)
        processing_group.apply_async()


    oks = [False] * len(results)

    retry = 0
    while True:
        if retry >= retries:
            msg = f'Failed to translate some of the articles after {retries} retries'
            logger.error(msg)
            raise Exception(msg)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            results, oks = list(zip(*executor.map(translate_and_save_article, results, oks)))

        if all(oks):
            break

        # results = [ results[i] for i in range(len(oks)) if oks[i] == False ]

        delay = 1.0 * (2**retry)
        time.sleep(delay)

        retry += 1


        # for result in list(results):  # Copy the results list so we can alter it.
        #     arxiv_id, version = result.entry_id.split('/')[-1].split('v')
        #     try:
        #         article = Article.objects.get(source_archive='arxiv', entry_id=arxiv_id, entry_version=version)
        #     except Article.DoesNotExist:
        #         try:
        #             title_cn = translator('google')(result.title)
        #             abstract_cn = translator('google')(result.summary)
        #             comment_cn = None
        #             journal_ref_cn = None
        #             if result.comment:
        #                 comment_cn = translator('google')(result.comment)
        #             if result.journal_ref:
        #                 journal_ref_cn = translator('google')(result.journal_ref)
        #             # title_cn = '中文标题'
        #             # abstract_cn = '中文摘要'
        #             logger.info(f'Successfully translated arxiv:{arxiv_id}v{version}.')
        #         except Exception:
        #             logger.warning(f'Failed to translate arxiv:{arxiv_id}v{version}, will retry latter.')
        #             continue

        #         article = Article(
        #             entry_id=arxiv_id,
        #             entry_version=version,
        #             title_en=result.title,
        #             title_cn=title_cn,
        #             abstract_en=result.summary,
        #             abstract_cn=abstract_cn,
        #             published_date=result.published,
        #             updated_date=result.updated,
        #             comment_en=result.comment,
        #             comment_cn=comment_cn,
        #             journal_ref_en=result.journal_ref,
        #             journal_ref_cn=journal_ref_cn,
        #             doi=result.doi,
        #             primary_category=result.primary_category,
        #         )
        #         article.save()
        #         for author in result.authors:
        #             author_ = Author(name=author.name, article=article)
        #             author_.save()
        #         for category in result.categories:
        #             category_ = Category(name=category, article=article)
        #             category_.save()
        #         for link in result.links:
        #             link_ = Link(url=link.href, article=article)
        #             link_.save()

        #     results.remove(result)

        # retry += 1

    dts = soup.find_all('dt')
    # dds = soup.find_all('dd')
    authors_divs = soup.find_all('div', {'class': 'list-authors'})

    # organize results into expected listing
    items = []
    for i, paper_id in enumerate(paper_ids):
        # article = Article.objects.filter(source_archive='arxiv', entry_id=paper_id).order_by('entry_version').last()
        article = results[i]

        # dt = dts[i]
        # new_a = soup.new_tag('a', attrs={'href': f"/cn-pdf/{paper_id}", 'title': "Download Chinese PDF", 'id': f"cn-pdf-{paper_id}", 'aria-labelledby': f"cn-pdf-{paper_id}"})
        # if get_language() == 'zh-hans':
        #     new_a.string = '中文pdf'
        # else:
        #     new_a.string = 'cn-pdf'
        # abstract_a = dt.find('a', {'title': 'Abstract'})
        # abstract_a.next_sibling.insert_after(new_a)
        # new_a = dt.find('a', {'id': f"cn-pdf-{paper_id}"})
        # new_a.insert_after(', ')

        # if get_language() == 'zh-hans':
        #     dd = dds[i]
        #     dd.find('div', {'class': "list-title mathjax"}).span.next_sibling.replace_with(article.title_cn)
        #     if dd.p:
        #         dd.p.string = article.abstract_cn

        listing_type = 'new' # need to get the correct 'new' or 'cross'
        arxiv_id, version = article.entry_id, article.entry_version
        primary_cat = CATEGORIES[article.primary_category]
        secondary_cats = [ CATEGORIES[sc.name] for sc in article.categories.all() if sc.name in CATEGORIES ]
        modified = max(article.updated_date, article.published_date)
        doc = DocMetadata(
            arxiv_id=arxiv_id,
            arxiv_id_v=f'{arxiv_id}v{version}',
            title=article.title_cn if language == 'zh-hans' else article.title_en,
            authors=AuList(', '.join([ author.name for author in article.authors.all() ])),
            abstract=article.abstract_cn if language == 'zh-hans' else article.abstract_en,
            categories=[ cat.name for cat in article.categories.all() ],
            primary_category=primary_cat,
            secondary_categories=secondary_cats,
            comments=article.comment_cn if language == 'zh-hans' else article.comment_en,
            journal_ref=article.journal_ref_cn if language == 'zh-hans' else article.journal_ref_en,
            version=version,
            version_history=[
                VersionEntry(
                    version=version,
                    raw="",
                    submitted_date=None, # type: ignore
                    size_kilobytes=0,
                    source_flag=''
                )
            ],
            raw_safe="",
            submitter=None, # type: ignore
            arxiv_identifier=None, # type: ignore
            primary_archive=primary_cat.get_archive(),
            primary_group=primary_cat.get_archive().get_group(),
            modified=modified
        )

        if doc.primary_category.in_archive  != archive_or_cat:
            listing_type = 'cross'

        a_html = dts[i].find('a', string='html')
        if a_html:
            doc.latexml_link = a_html['href']
        a_other = dts[i].find('a', string='other')
        if a_other:
            doc.other_link = a_other['href']

        doc.authors_list = str(authors_divs[i])
        doc.primary_display = doc.primary_category.display()
        doc.secondaries_display = doc.display_secondaries()

        doc.title_other_language = article.title_en if language == 'zh-hans' else article.title_cn
        doc.abstract_other_language = article.abstract_en if language == 'zh-hans' else article.abstract_cn
        doc.show_title_text = '显示英文标题' if language == 'zh-hans' else 'Show Chinese title'
        doc.hide_title_text = '隐藏英文标题' if language == 'zh-hans' else 'Hide Chinese title'
        doc.show_abstract_text = '显示英文摘要' if language == 'zh-hans' else 'Show Chinese abstract'
        doc.hide_abstract_text = '隐藏英文摘要' if language == 'zh-hans' else 'Hide Chinese abstract'

        if language == 'zh-hans':
            translation_dict = get_translation_dict()
            # Define the regex pattern
            pattern = r'^(.*?) \((.*?)\)$'

            # Use re.search to find matches
            match = re.search(pattern, doc.primary_display)
            if match:
                # Extract the two groups
                cat_full_name = match.group(1)  # e.g. 'High Energy Astrophysical Phenomena'
                category = match.group(2)  # e.g. 'astro-ph.HE'
                cat_full_name_cn = article_filters.dict_get_key(translation_dict, cat_full_name)
                doc.primary_display = f'{cat_full_name_cn} ({category})'

            for i, secondary_display in enumerate(doc.secondaries_display):
                match = re.search(pattern, secondary_display)
                if match:
                    # Extract the two groups
                    cat_full_name = match.group(1)  # e.g. 'High Energy Astrophysical Phenomena'
                    category = match.group(2)  # e.g. 'astro-ph.HE'
                    cat_full_name_cn = article_filters.dict_get_key(translation_dict, cat_full_name)
                    doc.secondaries_display[i] = f'{cat_full_name_cn} ({category})'

        item = ListingItem(
            id=arxiv_id,
            listingType=listing_type,
            primary=primary_cat.id,
            article=doc,
        )
        items.append(item)

    return Listing(
        listings=items,
        pubdates=daily_counts,
        count=total,
        expires=gen_expires()
    )#, dts, dds

def get_articles_for_month(request, archive_or_cat: str, time_period: str, year: int, month: Optional[int], skip: int, show: int) -> Listing:
    """archive: archive or category name, year:requested year, month: requested month - no month means retreive listings for the year,
    skip: number of entries to skip, show:number of entries to return
    Retrieve entries from the Metadata table for papers in a given category and month.
    Searches for all possible category names that could apply to a particular archive or category
    also retrieves information on if any of the possible categories is the articles primary
    """
    language = get_language()

    url = request.get_full_path()
    url = url.replace('/en', '')
    url = url.replace('/zh-hans', '')
    arxiv_url = 'https://arxiv.org' + url
    retries = 3
    retry_delay = 0.5
    response = request_get(arxiv_url, retries=retries, retry_delay=retry_delay)
    if not response:
        msg = f"Failed to fetch URL: {arxiv_url} after {retries} attempts."
        # logger.error(msg)
        raise Exception(msg)
    soup = BeautifulSoup(response.content, 'lxml')
    paging_div = soup.find('div', class_='paging')
    total = 0
    if paging_div:
        match = re.search(r'Total of (\d+) entries', paging_div.text)
        if match:
            total = int(match.group(1))

    paper_ids = []
    atags = soup.find_all('a', {'title': 'Abstract'})
    for atag in atags:
        paper_ids.append(atag['id'])

    # Create the search client
    client = arxivapi.Client()

    # Create the search query
    results = []
    for pids in itertools.batched(paper_ids, 200):
        search = arxivapi.Search(id_list=pids)
        results.extend(list(client.results(search)))

    # get arxiv_idv for all paper_ids
    # arxiv_idvs = [ result.entry_id.split('/')[-1] for result in results ]
    arxiv_idvs = [ result.entry_id.split(r'/abs/')[-1] for result in results ]
    # use celery to download and compile pdfs asynchronously
    if settings.CELERY_DOWNLOAD_AND_COMPILE_ARXIV:
        processing_group = group(download_and_compile_arxiv.s(arxiv_idv) for arxiv_idv in arxiv_idvs)
        processing_group.apply_async()


    oks = [False] * len(results)

    retry = 0
    while True:
        if retry >= retries:
            msg = f'Failed to translate some of the articles after {retries} retries'
            logger.error(msg)
            raise Exception(msg)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            results, oks = list(zip(*executor.map(translate_and_save_article, results, oks)))

        if all(oks):
            break

        # results = [ results[i] for i in range(len(oks)) if oks[i] == False ]

        delay = 1.0 * (2**retry)
        time.sleep(delay)

        retry += 1


        # for result in list(results):  # Copy the results list so we can alter it.
        #     arxiv_id, version = result.entry_id.split('/')[-1].split('v')
        #     try:
        #         article = Article.objects.get(source_archive='arxiv', entry_id=arxiv_id, entry_version=version)
        #     except Article.DoesNotExist:
        #         try:
        #             title_cn = translator('google')(result.title)
        #             abstract_cn = translator('google')(result.summary)
        #             comment_cn = None
        #             journal_ref_cn = None
        #             if result.comment:
        #                 comment_cn = translator('google')(result.comment)
        #             if result.journal_ref:
        #                 journal_ref_cn = translator('google')(result.journal_ref)
        #             # title_cn = '中文标题'
        #             # abstract_cn = '中文摘要'
        #             logger.info(f'Successfully translated arxiv:{arxiv_id}v{version}.')
        #         except Exception:
        #             logger.warning(f'Failed to translate arxiv:{arxiv_id}v{version}, will retry latter.')
        #             continue

        #         article = Article(
        #             entry_id=arxiv_id,
        #             entry_version=version,
        #             title_en=result.title,
        #             title_cn=title_cn,
        #             abstract_en=result.summary,
        #             abstract_cn=abstract_cn,
        #             published_date=result.published,
        #             updated_date=result.updated,
        #             comment_en=result.comment,
        #             comment_cn=comment_cn,
        #             journal_ref_en=result.journal_ref,
        #             journal_ref_cn=journal_ref_cn,
        #             doi=result.doi,
        #             primary_category=result.primary_category,
        #         )
        #         article.save()
        #         for author in result.authors:
        #             author_ = Author(name=author.name, article=article)
        #             author_.save()
        #         for category in result.categories:
        #             category_ = Category(name=category, article=article)
        #             category_.save()
        #         for link in result.links:
        #             link_ = Link(url=link.href, article=article)
        #             link_.save()

        #     results.remove(result)

        # retry += 1

    dts = soup.find_all('dt')
    # dds = soup.find_all('dd')
    authors_divs = soup.find_all('div', {'class': 'list-authors'})

    # organize results into expected listing
    items = []
    for i, paper_id in enumerate(paper_ids):
        # article = Article.objects.filter(source_archive='arxiv', entry_id=paper_id).order_by('entry_version').last()
        article = results[i]

        # dt = dts[i]
        # new_a = soup.new_tag('a', attrs={'href': f"/cn-pdf/{paper_id}", 'title': "Download Chinese PDF", 'id': f"cn-pdf-{paper_id}", 'aria-labelledby': f"cn-pdf-{paper_id}"})
        # if get_language() == 'zh-hans':
        #     new_a.string = '中文pdf'
        # else:
        #     new_a.string = 'cn-pdf'
        # abstract_a = dt.find('a', {'title': 'Abstract'})
        # abstract_a.next_sibling.insert_after(new_a)
        # new_a = dt.find('a', {'id': f"cn-pdf-{paper_id}"})
        # new_a.insert_after(', ')

        # if get_language() == 'zh-hans':
        #     dd = dds[i]
        #     dd.find('div', {'class': "list-title mathjax"}).span.next_sibling.replace_with(article.title_cn)
        #     if dd.p:
        #         dd.p.string = article.abstract_cn

        listing_type = 'new' # need to get the correct 'new' or 'cross'
        arxiv_id, version = article.entry_id, article.entry_version
        primary_cat = CATEGORIES[article.primary_category]
        secondary_cats = [ CATEGORIES[sc.name] for sc in article.categories.all() if sc.name in CATEGORIES ]
        modified = max(article.updated_date, article.published_date)
        doc = DocMetadata(
            arxiv_id=arxiv_id,
            arxiv_id_v=f'{arxiv_id}v{version}',
            title=article.title_cn if language == 'zh-hans' else article.title_en,
            authors=AuList(', '.join([ author.name for author in article.authors.all() ])),
            abstract=article.abstract_cn if language == 'zh-hans' else article.abstract_en,
            categories=[ cat.name for cat in article.categories.all() ],
            primary_category=primary_cat,
            secondary_categories=secondary_cats,
            comments=article.comment_cn if language == 'zh-hans' else article.comment_en,
            journal_ref=article.journal_ref_cn if language == 'zh-hans' else article.journal_ref_en,
            version=version,
            version_history=[
                VersionEntry(
                    version=version,
                    raw="",
                    submitted_date=None, # type: ignore
                    size_kilobytes=0,
                    source_flag=''
                )
            ],
            raw_safe="",
            submitter=None, # type: ignore
            arxiv_identifier=None, # type: ignore
            primary_archive=primary_cat.get_archive(),
            primary_group=primary_cat.get_archive().get_group(),
            modified=modified
        )

        if doc.primary_category.in_archive  != archive_or_cat:
            listing_type = 'cross'

        a_html = dts[i].find('a', string='html')
        if a_html:
            doc.latexml_link = a_html['href']
        a_other = dts[i].find('a', string='other')
        if a_other:
            doc.other_link = a_other['href']

        doc.authors_list = str(authors_divs[i])
        doc.primary_display = doc.primary_category.display()
        doc.secondaries_display = doc.display_secondaries()

        doc.title_other_language = article.title_en if language == 'zh-hans' else article.title_cn
        doc.abstract_other_language = article.abstract_en if language == 'zh-hans' else article.abstract_cn
        doc.show_title_text = '显示英文标题' if language == 'zh-hans' else 'Show Chinese title'
        doc.hide_title_text = '隐藏英文标题' if language == 'zh-hans' else 'Hide Chinese title'
        doc.show_abstract_text = '显示英文摘要' if language == 'zh-hans' else 'Show Chinese abstract'
        doc.hide_abstract_text = '隐藏英文摘要' if language == 'zh-hans' else 'Hide Chinese abstract'

        if language == 'zh-hans':
            translation_dict = get_translation_dict()
            # Define the regex pattern
            pattern = r'^(.*?) \((.*?)\)$'

            # Use re.search to find matches
            match = re.search(pattern, doc.primary_display)
            if match:
                # Extract the two groups
                cat_full_name = match.group(1)  # e.g. 'High Energy Astrophysical Phenomena'
                category = match.group(2)  # e.g. 'astro-ph.HE'
                cat_full_name_cn = article_filters.dict_get_key(translation_dict, cat_full_name)
                doc.primary_display = f'{cat_full_name_cn} ({category})'

            for i, secondary_display in enumerate(doc.secondaries_display):
                match = re.search(pattern, secondary_display)
                if match:
                    # Extract the two groups
                    cat_full_name = match.group(1)  # e.g. 'High Energy Astrophysical Phenomena'
                    category = match.group(2)  # e.g. 'astro-ph.HE'
                    cat_full_name_cn = article_filters.dict_get_key(translation_dict, cat_full_name)
                    doc.secondaries_display[i] = f'{cat_full_name_cn} ({category})'

        item = ListingItem(
            id=arxiv_id,
            listingType=listing_type,
            primary=primary_cat.id,
            article=doc,
        )
        items.append(item)


    # new_listings, cross_listings = _entries_into_monthly_listing_items(result)

    if not month:
        month = 1 # yearly listings need a month for datetime

    return Listing(
        # listings=new_listings + cross_listings,
        listings=items,
        pubdates=[(datetime(year, month, 1), 1)],  # only used for display month
        count=total,
        expires=gen_expires(),
    )#, dts, dds






def get_all_cn_pdfs(request, skip: int, show: int) -> Listing:
    language = get_language()

    cn_pdf_base_dir = f'{settings.CENXIV_FILE_PATH}'
    arxiv_ids = [ arxiv_dir.split('/')[-1][5:15] for arxiv_dir in glob.glob(cn_pdf_base_dir + '/*') if re.match(r'arxiv\d{4}\.\d{5}', arxiv_dir.split('/')[-1]) ]
    arxiv_ids = sorted(arxiv_ids)[::-1]
    # arxiv_ids = arxiv_ids[:2] # for test
    total = len(arxiv_ids)

    paper_ids = arxiv_ids[skip:skip+show]
    arxiv_idvs = []
    for arxiv_id in paper_ids:
        arxiv_idvs.extend([ f'{arxiv_id}v{version.split('/')[-1][1:]}' for version in glob.glob(f'{cn_pdf_base_dir}/arxiv{arxiv_id}/*') if re.match(r'v\d+', version.split('/')[-1]) ])


    # Create the search client
    client = arxivapi.Client()

    # Create the search query
    results = []
    for pids in itertools.batched(arxiv_idvs, 200):
        search = arxivapi.Search(id_list=pids)
        results.extend(list(client.results(search)))


    oks = [False] * len(results)

    retries = 3
    retry = 0
    while True:
        if retry >= retries:
            msg = f'Failed to translate some of the articles after {retries} retries'
            logger.error(msg)
            raise Exception(msg)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            results, oks = list(zip(*executor.map(translate_and_save_article, results, oks)))

        if all(oks):
            break

        # results = [ results[i] for i in range(len(oks)) if oks[i] == False ]

        delay = 1.0 * (2**retry)
        time.sleep(delay)

        retry += 1


    # organize results into expected listing
    items = []
    for i, paper_id in enumerate(paper_ids):
        # article = Article.objects.filter(source_archive='arxiv', entry_id=paper_id).order_by('entry_version').last()
        article = results[i]

        listing_type = 'new' # need to get the correct 'new' or 'cross'
        arxiv_id, version = article.entry_id, article.entry_version
        primary_cat = CATEGORIES[article.primary_category]
        secondary_cats = [ CATEGORIES[sc.name] for sc in article.categories.all() if sc.name in CATEGORIES ]
        modified = max(article.updated_date, article.published_date)
        doc = DocMetadata(
            arxiv_id=arxiv_id,
            arxiv_id_v=f'{arxiv_id}v{version}',
            title=article.title_cn if language == 'zh-hans' else article.title_en,
            authors=AuList(', '.join([ author.name for author in article.authors.all() ])),
            abstract=article.abstract_cn if language == 'zh-hans' else article.abstract_en,
            categories=[ cat.name for cat in article.categories.all() ],
            primary_category=primary_cat,
            secondary_categories=secondary_cats,
            comments=article.comment_cn if language == 'zh-hans' else article.comment_en,
            journal_ref=article.journal_ref_cn if language == 'zh-hans' else article.journal_ref_en,
            version=version,
            version_history=[
                VersionEntry(
                    version=version,
                    raw="",
                    submitted_date=None, # type: ignore
                    size_kilobytes=0,
                    source_flag=''
                )
            ],
            raw_safe="",
            submitter=None, # type: ignore
            arxiv_identifier=None, # type: ignore
            primary_archive=primary_cat.get_archive(),
            primary_group=primary_cat.get_archive().get_group(),
            modified=modified
        )

        doc.authors_list = ', '.join([ author.name for author in article.authors.all() ])
        doc.primary_display = doc.primary_category.display()
        doc.secondaries_display = doc.display_secondaries()

        doc.title_other_language = article.title_en if language == 'zh-hans' else article.title_cn
        doc.abstract_other_language = article.abstract_en if language == 'zh-hans' else article.abstract_cn
        doc.show_title_text = '显示英文标题' if language == 'zh-hans' else 'Show Chinese title'
        doc.hide_title_text = '隐藏英文标题' if language == 'zh-hans' else 'Hide Chinese title'
        doc.show_abstract_text = '显示英文摘要' if language == 'zh-hans' else 'Show Chinese abstract'
        doc.hide_abstract_text = '隐藏英文摘要' if language == 'zh-hans' else 'Hide Chinese abstract'

        if language == 'zh-hans':
            translation_dict = get_translation_dict()
            # Define the regex pattern
            pattern = r'^(.*?) \((.*?)\)$'

            # Use re.search to find matches
            match = re.search(pattern, doc.primary_display)
            if match:
                # Extract the two groups
                cat_full_name = match.group(1)  # e.g. 'High Energy Astrophysical Phenomena'
                category = match.group(2)  # e.g. 'astro-ph.HE'
                cat_full_name_cn = article_filters.dict_get_key(translation_dict, cat_full_name)
                doc.primary_display = f'{cat_full_name_cn} ({category})'

            for i, secondary_display in enumerate(doc.secondaries_display):
                match = re.search(pattern, secondary_display)
                if match:
                    # Extract the two groups
                    cat_full_name = match.group(1)  # e.g. 'High Energy Astrophysical Phenomena'
                    category = match.group(2)  # e.g. 'astro-ph.HE'
                    cat_full_name_cn = article_filters.dict_get_key(translation_dict, cat_full_name)
                    doc.secondaries_display[i] = f'{cat_full_name_cn} ({category})'

        item = ListingItem(
            id=arxiv_id,
            listingType=listing_type,
            primary=primary_cat.id,
            article=doc,
        )
        items.append(item)


    return Listing(
        listings=items,
        pubdates=[(datetime.today(), 1)],  # only used for display month
        count=total,
        expires=gen_expires(),
    )
