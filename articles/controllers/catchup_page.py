"""handles requests to the catchup page.
Allows users to access something equivalent to the /new page for up to 90 days back
"""
import re
import time
import itertools
import logging
from typing import Tuple, Union, Dict, Any, List
from datetime import date, datetime, timedelta
# import requests
from bs4 import BeautifulSoup
from celery import group
import concurrent.futures

from http import HTTPStatus
# from flask import request, redirect, url_for
from werkzeug.exceptions import BadRequest

from django.urls import reverse
from django.conf import settings
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

import arxivapi  # The PyPI arxiv package

from arxiv.document.metadata import DocMetadata
from arxiv.integration.fastly.headers import add_surrogate_key
from arxiv.taxonomy.category import Group, Archive, Category
from arxiv.taxonomy.definitions import CATEGORIES, ARCHIVES, GROUPS, ARCHIVES_ACTIVE
from arxiv.document.version import VersionEntry
from arxiv.document.metadata import DocMetadata, AuthorList as AuList

# from browse.controllers.archive_page.by_month_form import MONTHS
from browse.controllers.list_page import latexml_links_for_articles, dl_for_articles, authors_for_articles, Response
from browse.services.database.catchup import get_catchup_data, CATCHUP_LIMIT, get_next_announce_day
from browse.services.listing import ListingNew, ListingItem, gen_expires

from .list_page import sub_sections_for_types
from ..models import Article#, Author, Category, Link
from ..tasks import download_and_compile_arxiv
from ..templatetags import article_filters
from ..utils import get_translation_dict, chinese_week_days, request_get, translate_and_save_article
from .archive_page.by_month_form import MONTHS
# from ..translators import translator


logger = logging.getLogger(__name__)

def get_catchup_page(request, subject_str:str, date:str)-> Response:
    """get the catchup page for a given set of request parameters
    see process_catchup_params for details on parameters
    """
    subject, start_day, include_abs, page = _process_catchup_params(request, subject_str, date)
    #check for redirects for noncanon subjects
    if subject.id != subject.canonical_id:
        new_address = reverse('articles:catchup_form',
                    kwargs={
                        'subject': subject.canonical_id,
                        'date': start_day,
                        'page': page,
                        'abs': include_abs
                    })
        return {}, HTTPStatus.MOVED_PERMANENTLY, {"Location": new_address}


    language = get_language()

    headers: Dict[str,str] = {}
    headers = add_surrogate_key(headers, ["catchup", f"list-{start_day.year:04d}-{start_day.month:02d}-{subject.id}"])
    # get data
    # listing = get_catchup_data(subject, start_day, include_abs, page)
    # next_announce_day = get_next_announce_day(start_day)

    #format data
    response_data: Dict[str, Any] = {}
    # headers.update({'Surrogate-Control': f'max-age={listing.expires}'})
    # count = listing.new_count+listing.cross_count+listing.rep_count
    # response_data['announced'] = listing.announced
    # skip = (page-1)*CATCHUP_LIMIT
    # response_data.update(catchup_index_for_types(listing.new_count, listing.cross_count, listing.rep_count,  subject, start_day, include_abs, page))
    # response_data.update(sub_sections_for_types(listing, skip, CATCHUP_LIMIT))

    # idx = 0
    # for item in listing.listings:
    #     idx = idx + 1
    #     setattr(item, 'list_index', idx + skip)

    # response_data['listings'] = listing.listings
    # response_data['author_links'] = authors_for_articles(listing.listings)
    # response_data['downloads'] = dl_for_articles(listing.listings)
    # response_data['latexml'] = latexml_links_for_articles(listing.listings)

    count = 0
    next_announce_day = None

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

    # Define the regex pattern to match the text and capture count and date
    pattern = r'Total of (\d+) entries for (\w{3}, \s*\d{2} \w{3} \d{4})'
    # Find the div that matches the regex pattern
    target_div = soup.find('div', {'id': 'dlpage'}).find('div')
    if target_div:
        # Extract the text from the div
        text = target_div.text.strip()

        # Use regex to extract count and date
        match = re.search(pattern, text)
        if match:
            count = int(match.group(1))  # Extract the count
        #     date_str = match.group(2)     # Extract the date string

        #     print(f"Count: {count}, Date: {date_str}")
        # else:
        #     print("No match found in the div text.")

        # get next_day
        next_day_alink = target_div.find('a')
        if next_day_alink:
            next_announce_day = next_day_alink['href'].split('?')[0].split('/')[-1]
    # else:
        # print("Div not found.")

    if count > 0:
        new_start = int(soup.find('a', text="New submissions")['href'].replace('#item', ''))
        cross_start = int(soup.find('a', text="Cross-lists")['href'].replace('#item', ''))
        rep_start = int(soup.find('a', text="Replacements")['href'].replace('#item', ''))

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
        arxiv_idvs = [ result.entry_id.split('/')[-1] for result in results ]
        # use celery to download and compile pdfs asynchronously
        if settings.CELERY_DOWNLOAD_AND_COMPILE_ARXIV:
            processing_group = group(download_and_compile_arxiv.s(arxiv_idv) for arxiv_idv in arxiv_idvs)
            processing_group.apply_async()

        retry = 0
        while results:
            if retry >= retries:
                msg = f'Failed to translate some of the articles after {retries} retries'
                logger.error(msg)
                raise Exception(msg)

            with concurrent.futures.ThreadPoolExecutor() as executor:
                oks = list(executor.map(translate_and_save_article, results))

            if all(oks):
                break

            results = [ results[i] for i in range(len(oks)) if oks[i] == False ]

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


        new_count = cross_start - new_start
        cross_count = rep_start - cross_start
        rep_count = len(paper_ids) - new_count - cross_count

        dts = soup.find_all('dt')
        # dds = soup.find_all('dd')
        authors_divs = soup.find_all('div', {'class': 'list-authors'})

        # organize results into expected listing
        items = []
        for i, paper_id in enumerate(paper_ids):
            if i < new_count:
                listing_type = 'new'
            elif i < new_count + cross_count:
                listing_type = 'cross'
            else:
                listing_type = 'rep'

            article = Article.objects.filter(source_archive='arxiv', entry_id=paper_id).order_by('entry_version').last()

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

        listing = ListingNew(listings=items,
                        new_count=new_count,
                        cross_count=cross_count,
                        rep_count=rep_count,
                        announced=datetime.today(),
                        expires=gen_expires())

        skip = (page-1)*CATCHUP_LIMIT
        response_data.update(catchup_index_for_types(listing.new_count, listing.cross_count, listing.rep_count, subject, start_day, include_abs, page))
        # response_data.update(sub_sections_for_types((listing, dts, dds), skip, CATCHUP_LIMIT))
        response_data.update(sub_sections_for_types(listing, skip, CATCHUP_LIMIT))

        idx = 0
        for item in listing.listings:
            idx = idx + 1
            setattr(item, 'list_index', idx + skip)

    response_data.update({
        'subject': subject,
        # 'date': start_day,
        'date_adbY': start_day.strftime("%Y年%m月%d日") + f'， {chinese_week_days[start_day.weekday()]}' if language == 'zh-hans' else start_day.strftime("%a, %d %b %Y"),
        'date_Ymd': start_day.strftime('%Y-%m-%d'),
        'next_day': next_announce_day,
        'page': page,
        'include_abs': include_abs,
        'count': count,
        'list_type': "new" if include_abs else "catchup", # how the list macro checks to display abstract
        'paging': catchup_paging(subject, start_day, include_abs, page, count)
    })

    # def author_query(article: DocMetadata, query: str)->str:
    #     try:
    #         if article.primary_archive:
    #             archive_id = article.primary_archive.id
    #         elif article.primary_category:
    #             archive_id = article.primary_category.in_archive
    #         else:
    #             archive_id=''
    #         return str(url_for('search_archive',
    #                        searchtype='author',
    #                        archive=archive_id,
    #                        query=query))
    #     except (AttributeError, KeyError):
    #         return str(url_for('search_archive',
    #                            searchtype='author',
    #                            archive=archive_id,
    #                            query=query))

    # response_data['url_for_author_search'] = author_query

    return response_data, 200, headers

def get_catchup_form(request) -> Response:
    headers = {}
    headers = add_surrogate_key(headers, ["catchup"])

    # check for form/parameter requests
    subject = request.GET.get('subject')
    date = request.GET.get('date')
    include_abs = request.GET.get('include_abs')
    if subject and date:
        new_address = reverse('articles:catchup', kwargs={'subject': subject, 'date': date})
        if include_abs:
            new_address += f'?abs={include_abs}'
        headers.update({'Location': new_address})
        headers.update({'Surrogate-Control': f'max-age=2600000'})  # one month, url construction should never change
        headers = add_surrogate_key(headers, ["catchup-redirect"])
        return {}, 301, headers

    # otherwise create catchup form
    response_data = {
        'years': [datetime.now().year, datetime.now().year - 1],  # only last 90 days allowed anyways
        'months': MONTHS[1:],
        'current_month': datetime.now().strftime('%m'),
        'days': [str(day).zfill(2) for day in range(1, 32)],
        'groups': GROUPS,
    }

    headers = add_surrogate_key(headers, ["catchup-form"])
    headers.update({'Surrogate-Control': f'max-age=604800'})  # one week, form never changes except for autoselecting currently month
    return response_data, 200, headers


def _process_catchup_params(request, subject_str:str, date_str:str)->Tuple[Union[Group, Archive, Category], date, bool, int]:
    """processes the request parameters to the catchup page
    raises an error or returns usable values

    Returns:
    subject: as a Group, Archive, or Category. Still needs to be checked for canonicalness
    start_day: date (date to catchup on)
    abs: bool (include abstracts or not )
    page: int (which page of results, default is 1)
    """

    # check for valid arguments
    ALLOWED_PARAMS = {"abs", "page"}
    unexpected_params = request.GET.keys() - ALLOWED_PARAMS
    if unexpected_params:
        raise BadRequest(f"Unexpected parameters. Only accepted parameters are: 'page', and 'abs'")

    # subject validation
    subject: Union[Group, Archive, Category]
    if subject_str == "grp_physics":
        subject = GROUPS["grp_physics"]
    elif subject_str in ARCHIVES:
        subject= ARCHIVES[subject_str]
    elif subject_str in CATEGORIES:
        subject= CATEGORIES[subject_str]
    else:
        raise BadRequest("Invalid subject. Subject must be an archive, category or 'grp_physics'")

    # date validation
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str): # enforce two digit days and months
        raise BadRequest(f"Invalid date format. Use format: YYYY-MM-DD")
    try:
        start_day = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise BadRequest(f"Invalid date format. Use format: YYYY-MM-DD")
    # only allow dates within the last 90 days (91 just in case time zone differences)
    today = datetime.now().date()
    earliest_allowed = today - timedelta(days=91)
    if start_day < earliest_allowed:
        # TODO link to earliest allowed date
        raise BadRequest(f"Invalid date: {start_day}. Catchup only allowed for past 90 days")
    elif start_day > today:
        raise BadRequest(f"Invalid date: {start_day}. Can't request date later than today")

    # include abstract or not
    abs_str = request.GET.get("abs", "False")
    if abs_str == "True":
        include_abs = True
    elif abs_str == "False":
        include_abs = False
    else:
        raise BadRequest(f"Invalid abs value. Use ?abs=True to include abstracts or ?abs=False to not")

    # select page number (each page has 2000 items)
    page_str = request.GET.get("page", "1") # page defaults to 1
    if page_str.isdigit():
        page = int(page_str)
    else:
        raise BadRequest(f"Invalid page value. Page value should be a positive integer like ?page=3")
    if page < 1:
        raise BadRequest(f"Invalid page value. Page value should be a positive integer like ?page=3")

    return subject, start_day, include_abs, page

def catchup_paging(subject: Union[Group, Archive, Category], day:date, include_abs:bool, page: int, count:int)-> List[Tuple[str,str]]:
    '''creates a dictionary of page links for the case that there is more than one page of data'''
    if CATCHUP_LIMIT >= count: # only one page
        return []

    total_pages = count//CATCHUP_LIMIT + 1
    url_base = reverse('articles:catchup', kwargs={'subject': subject.id, 'date': day.strftime('%Y-%m-%d')}) + f'?abs={include_abs}'
    page_links = []

    if total_pages < 10: # realistically there should be at most 2-3 pages per day
        for i in range(1, total_pages+1):
            if i == page:
                page_links.append((str(i), 'no-link'))
            else:
                page_links.append((str(i), url_base+f'&page={i}'))

    else: # shouldnt happen but its handled
        if page != 1:
            page_links.append(('1', url_base+f'&page=1'))
        if page > 2:
            page_links.append(('...', 'no-link'))
        page_links.append((str(page), 'no-link'))
        if page < total_pages-1:
            page_links.append(('...', 'no-link'))
        if page != total_pages:
            page_links.append((str(total_pages), url_base+f'&page={total_pages}'))

    return page_links

def catchup_index_for_types(new_count:int, cross_count:int, rep_count:int,  subject: Union[Group, Archive, Category], day:date, include_abs:bool, page: int) ->Dict[str, Any]:
    """Creates index for types for catchup papers.
    page count and index both start at 1
    """
    ift = []

    if new_count > 0:
        if page != 1:
            ift.append((_('New submissions'),
                        reverse('articles:catchup', kwargs={'subject': subject.id, 'date': day.strftime('%Y-%m-%d')}) + f'?abs={include_abs}&page=1',
                        1))
        else:
            ift.append((_('New submissions'), '', 1))

    if cross_count > 0:
        cross_start = new_count + 1
        cross_start_page = (cross_start-1)//CATCHUP_LIMIT +1 # item 2000 is on page 1, 2001 is on page 2
        cross_index = cross_start-(cross_start_page-1)*CATCHUP_LIMIT

        if page == cross_start_page:
            ift.append((_('Cross-lists'), '', cross_index))
        else:
            ift.append((_('Cross-lists'),
                        reverse('articles:catchup', kwargs={'subject': subject.id, 'date': day.strftime('%Y-%m-%d')}) + f'?abs={include_abs}&page={cross_start_page}',
                        cross_index))

    if rep_count > 0:
        rep_start = new_count + cross_count+ 1
        rep_start_page = (rep_start-1)//CATCHUP_LIMIT + 1 # item 2000 is on page 1, 2001 is on page 2
        rep_index = rep_start - (rep_start_page-1)*CATCHUP_LIMIT

        if page == rep_start_page:
            ift.append((_('Replacements'), '', rep_index))
        else:
            ift.append((_('Replacements'),
                        reverse('articles:catchup', kwargs={'subject': subject.id, 'date': day.strftime('%Y-%m-%d')}) + f'?abs={include_abs}&page={rep_start_page}',
                        rep_index))

    return {'index_for_types': ift}
