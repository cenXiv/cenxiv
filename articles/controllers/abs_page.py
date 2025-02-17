"""Handle requests to support the abs feature.

The primary entrypoint to this module is :func:`.get_abs_page`, which
handles GET requests to the abs endpoint.
"""
import re
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin
# import requests
from bs4 import BeautifulSoup
from celery import group

from http import HTTPStatus as status
from dateutil import parser
from dateutil.tz import tzutc
from flask import request, url_for, current_app
from werkzeug.exceptions import InternalServerError

from django.utils.translation import get_language
from django.conf import settings

import arxivapi  # The PyPI arxiv package

# From arxiv-base package
# from arxiv.base import logging
from arxiv.taxonomy.definitions import ARCHIVES, CATEGORIES
from arxiv.taxonomy.category import Category as Cat
from arxiv.identifier import (
    Identifier,
    IdentifierException,
    IdentifierIsArchiveException,
)
from arxiv.document.metadata import DocMetadata, Submitter, AuthorList as AuList
from arxiv.document.exceptions import (
    AbsDeletedException,
    AbsException,
    AbsNotFoundException,
    AbsVersionNotFoundException,
)
from arxiv.integration.fastly.headers import add_surrogate_key
from arxiv.document.version import SourceFlag, VersionEntry

from browse.exceptions import AbsNotFound
from browse.services.database import (
    count_trackback_pings,
    get_datacite_doi,
    get_dblp_authors,
    get_dblp_listing_path,
    get_trackback_ping_latest_date,
    get_latexml_publish_dt,
)
# from browse.services.documents import get_doc_service
# from browse.services.documents.db_implementation import db_abs
from browse.services.dissemination import get_article_store
# from browse.controllers import check_supplied_identifier
from browse.formatting.external_refs_cits import (
    DBLP_BASE_URL,
    DBLP_BIBTEX_PATH,
    DBLP_AUTHOR_SEARCH_PATH,
    include_inspire_link,
    include_dblp_section,
    get_computed_dblp_listing_path,
    get_dblp_bibtex_path,
)
# from browse.formatting.latexml import get_latexml_url
# from browse.formatting.search_authors import queries_for_authors, split_long_author_list
from browse.controllers.response_headers import mime_header_date
# from browse.formatting.metatags import meta_tag_metadata

from . import check_supplied_identifier
from ..models import Article, Author, Category, Link
from ..tasks import download_and_compile_arxiv
from ..templatetags import article_filters
from ..utils import get_translation_dict, request_get, chinese_week_days, translate_latex_paragraph
from ..translators import translator


logger = logging.getLogger(__name__)

tl = settings.TRANSLATOR

Response = Tuple[Dict[str, Any], int, Dict[str, Any]]

truncate_author_list_size = 100


def get_abs_page(request, arxiv_id: str) -> Response:
    """Get abs page data from the document metadata service.

    Parameters
    ----------
    arxiv_id : str
        The arXiv identifier as provided in the request.
    download_format_pref: str
        Download format preference.

    Returns
    -------
    dict
        Search result response data.
    int
        HTTP status code.
    dict
        Headers to add to the response.

    Raises
    ------
    :class:`.InternalServerError`
        Raised when there was an unexpected problem executing the query.
    """
    response_data: Dict[str, Any] = {}
    response_headers: Dict[str, Any] = {}
    try:
        if not Identifier.is_mostly_safe(arxiv_id):
            raise AbsNotFound(data={"reason": "poorly formatted paper id"})

        arxiv_id = _check_legacy_id_params(request, arxiv_id)
        arxiv_identifier = Identifier(arxiv_id=arxiv_id)
        response_headers = add_surrogate_key(response_headers, [f"abs-{arxiv_identifier.id}", f"paper-id-{arxiv_identifier.id}"])
        redirect = check_supplied_identifier(arxiv_identifier, "article:abstract")
        if redirect:
            return redirect

        # # abs_meta = get_doc_service().get_abs(arxiv_identifier)
        # abs_meta = db_abs.DbDocMetadataService().get_abs(arxiv_identifier)
        # not_modified = _check_request_headers(abs_meta, response_data, response_headers)
        # if not_modified:
        #     return {}, status.NOT_MODIFIED, response_headers

        request_id = (
            arxiv_identifier.idv
            if arxiv_identifier.has_version
            else arxiv_identifier.id
        )
        response_data["requested_id"] = request_id

        # Create the search client
        client = arxivapi.Client()

        # Create the search query
        # first search for the latest version
        search = arxivapi.Search(id_list=[arxiv_identifier.id])
        result = list(client.results(search))[0]

        arxiv_id, latest_version = result.entry_id.split('/')[-1].split('v')
        latest_version = int(latest_version)
        if 'v' in request_id:
            request_version = int(request_id.split('v')[-1])
        else:
            request_version = latest_version

        # use celery to download and compile pdfs asynchronously
        if settings.CELERY_DOWNLOAD_AND_COMPILE_ARXIV:
            processing_group = group(download_and_compile_arxiv.s(f'{arxiv_id}v{v}') for v in range(1, latest_version+1))
            processing_group.apply_async()

        # text_translator = translate.TextTranslator(tl, 'en', 'zh-CN')
        # latex_translator = translate.LatexTranslator(text_translator, debug=False, threads=0)

        retries = 3
        retry = 0
        while True:
            if retry >= retries:
                msg = f'Failed to translate article arxiv:{arxiv_id}v{latest_version} after {retries} retries'
                logger.error(msg)
                raise Exception(msg)

            try:
                article = Article.objects.get(source_archive='arxiv', entry_id=arxiv_id, entry_version=latest_version)
                break
            except Article.DoesNotExist:
                try:
                    # title_cn = latex_translator.translate_full_latex(result.title, make_complete=False).strip()
                    # abstract_cn = latex_translator.translate_full_latex(result.summary, make_complete=False).strip()
                    title_cn = translate_latex_paragraph(result.title, tl)
                    abstract_cn = translate_latex_paragraph(result.summary, tl)
                    comment_cn = None
                    journal_ref_cn = None
                    if result.comment:
                        comment_cn = translator(tl)(result.comment.replace('\n', ' '))
                    if result.journal_ref:
                        journal_ref_cn = translator(tl)(result.journal_ref.replace('\n', ' '))
                    # title_cn = '中文标题'
                    # abstract_cn = '中文摘要'
                    logger.info(f'Successfully translated arxiv:{arxiv_id}v{latest_version}.')
                except Exception:
                    logger.warning(f'Failed to translate arxiv:{arxiv_id}v{latest_version}, will retry latter.')
                    retry += 1
                    continue

                article = Article(
                    entry_id=arxiv_id,
                    entry_version=latest_version,
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

                break

        # then search for all other latest versions
        versions = list(range(1, latest_version))
        retry = 0
        while versions:
            if retry >= retries:
                msg = f'Failed to translate versions {versions} of the article arxiv:{arxiv_id} after {retries} retries'
                logger.error(msg)
                raise Exception(msg)

            for version in list(versions):  # Copy the versions list so we can alter it.
                try:
                    article = Article.objects.get(source_archive='arxiv', entry_id=arxiv_id, entry_version=version)
                except Article.DoesNotExist:
                    search = arxivapi.Search(id_list=[f'{arxiv_id}v{version}'])
                    result = list(client.results(search))[0]

                    try:
                        # title_cn = latex_translator.translate_full_latex(result.title, make_complete=False).strip()
                        # abstract_cn = latex_translator.translate_full_latex(result.summary, make_complete=False).strip()
                        title_cn = translate_latex_paragraph(result.title, tl)
                        abstract_cn = translate_latex_paragraph(result.summary, tl)
                        comment_cn = None
                        journal_ref_cn = None
                        if result.comment:
                            comment_cn = translator(tl)(result.comment.replace('\n', ' '))
                        if result.journal_ref:
                            journal_ref_cn = translator(tl)(result.journal_ref.replace('\n', ' '))
                        # title_cn = '中文标题'
                        # abstract_cn = '中文摘要'
                        logger.info(f'Successfully translated arxiv:{arxiv_id}v{version}.')
                    except Exception:
                        logger.warning(f'Failed to translate arxiv:{arxiv_id}v{version}, will retry latter.')
                        continue

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

                versions.remove(version)

            retry += 1


        # get article of the request_version
        article = Article.objects.get(source_archive='arxiv', entry_id=arxiv_id, entry_version=request_version)

        language = get_language()

        primary_cat = CATEGORIES[article.primary_category]
        secondary_cats = [ CATEGORIES[sc.name] for sc in article.categories.all() if sc.name in CATEGORIES ]
        modified = max(article.updated_date, article.published_date)
        abs_meta = DocMetadata(
            raw_safe='',
            abstract=article.abstract_cn if language == 'zh-hans' else article.abstract_en,
            arxiv_id=arxiv_id,
            arxiv_id_v=f'{arxiv_id}v{request_version}',
            arxiv_identifier=arxiv_identifier,
            title=article.title_cn if language == 'zh-hans' else article.title_en,
            modified=modified,
            authors=AuList(', '.join([ author.name for author in article.authors.all() ])),
            submitter=Submitter(name='', email=''),
            source_format='', # type: ignore
            journal_ref=article.journal_ref_cn if language == 'zh-hans' else article.journal_ref_en,
            report_num=0,
            doi=article.doi,
            acm_class= None,
            msc_class= None,
            proxy= None,
            comments=article.comment_cn if language == 'zh-hans' else article.comment_en,
            version=request_version,
            license='',
            version_history=[
                VersionEntry(
                    version=version,
                    raw="",
                    submitted_date=Article.objects.get(source_archive='arxiv', entry_id=arxiv_id, entry_version=version).updated_date,
                    size_kilobytes=0,
                    source_flag=''
                ) for version in range(1, latest_version+1)
            ],

            is_definitive=True,
            is_latest=(request_version == latest_version),

            # Below are all from the latest version
            # On the abs page the convention is to display all versions as having these fields with values from the latest
            categories=[ cat.name for cat in article.categories.all() ],
            primary_category=primary_cat,
            secondary_categories=secondary_cats,
            primary_archive=primary_cat.get_archive(),
            primary_group=primary_cat.get_archive().get_group(),
        )

        # a_html = dts[i].find('a', string='html')
        # if a_html:
        #     abs_meta.latexml_link = a_html['href']
        # a_other = dts[i].find('a', string='other')
        # if a_other:
        #     abs_meta.other_link = a_other['href']

        # abs_meta.authors_list = str(authors_divs[i])
        abs_meta.primary_display = abs_meta.primary_category.display()
        abs_meta.secondaries_display = abs_meta.display_secondaries()

        abs_meta.title_other_language = article.title_en if language == 'zh-hans' else article.title_cn
        abs_meta.abstract_other_language = article.abstract_en if language == 'zh-hans' else article.abstract_cn
        abs_meta.show_title_text = '显示英文标题' if language == 'zh-hans' else 'Show Chinese title'
        abs_meta.hide_title_text = '隐藏英文标题' if language == 'zh-hans' else 'Hide Chinese title'
        abs_meta.show_abstract_text = '显示英文摘要' if language == 'zh-hans' else 'Show Chinese abstract'
        abs_meta.hide_abstract_text = '隐藏英文摘要' if language == 'zh-hans' else 'Hide Chinese abstract'

        if language == 'zh-hans':
            translation_dict = get_translation_dict()
            # Define the regex pattern
            pattern = r'^(.*?) \((.*?)\)$'

            # Use re.search to find matches
            match = re.search(pattern, abs_meta.primary_display)
            if match:
                # Extract the two groups
                cat_full_name = match.group(1)  # e.g. 'High Energy Astrophysical Phenomena'
                category = match.group(2)  # e.g. 'astro-ph.HE'
                cat_full_name_cn = article_filters.dict_get_key(translation_dict, cat_full_name)
                abs_meta.primary_display = f'{cat_full_name_cn} ({category})'

            for i, secondary_display in enumerate(abs_meta.secondaries_display):
                match = re.search(pattern, secondary_display)
                if match:
                    # Extract the two groups
                    cat_full_name = match.group(1)  # e.g. 'High Energy Astrophysical Phenomena'
                    category = match.group(2)  # e.g. 'astro-ph.HE'
                    cat_full_name_cn = article_filters.dict_get_key(translation_dict, cat_full_name)
                    abs_meta.secondaries_display[i] = f'{cat_full_name_cn} ({category})'


        # response_data["arxiv_id"] = arxiv_id
        response_data["latest_version"] = latest_version
        response_data["request_version"] = request_version
        first_version_submitted_date = abs_meta.version_history[0].submitted_date
        request_version_submitted_date = abs_meta.version_history[request_version-1].submitted_date
        latest_version_submitted_date = abs_meta.version_history[-1].submitted_date
        response_data["first_version_submitted_date"] = first_version_submitted_date.strftime('%Y年%-m月%-d日') if language == 'zh-hans' else first_version_submitted_date.strftime('%-d %b %Y')
        response_data["request_version_submitted_date"] = request_version_submitted_date.strftime('%Y年%-m月%-d日') if language == 'zh-hans' else request_version_submitted_date.strftime('%-d %b %Y')
        response_data["this_is_the_first_version"] = (request_version == 1)
        response_data["there_are_more_versions"] = (latest_version > 1)
        response_data["this_is_the_latest_version"] = (request_version == latest_version)
        response_data["latest_version_submitted_date"] = latest_version_submitted_date.strftime('%Y年%-m月%-d日') if language == 'zh-hans' else latest_version_submitted_date.strftime('%-d %b %Y')
        response_data["arxiv_id_v1"] = f'{arxiv_id}v1'
        response_data["arxiv_id_v_1"] = f'{arxiv_id}v{latest_version}'
        # primary_category = CATEGORIES[result.primary_category]
        # primary_archive = ARCHIVES[primary_category.in_archive]
        # response_data["primary_archive"] = primary_archive
        # response_data["primary_category"] = primary_category

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

        meta_tags = soup.find_all('meta')

        div_content_inner = soup.find('div', {'id': 'content-inner'})
        authors_div = div_content_inner.find('div', {'class': 'authors'})
        abs_meta.author_list = str(authors_div)
        # metadata related
        msc_class_td = div_content_inner.find('td', {'class': "tablecell msc-classes"})
        if msc_class_td:
            response_data['msc_class'] = msc_class_td.string
        acm_class_td = div_content_inner.find('td', {'class': "tablecell acm-classes"})
        if acm_class_td:
            response_data['acm_class'] = acm_class_td.string
        report_number_td = div_content_inner.find('td', {'class': "tablecell jref"}) # jref may be wrong
        if report_number_td:
            response_data['report_number'] = report_number_td.string
        # datacite doi related
        datacite_doi_td = div_content_inner.find('td', {'class': "tablecell arxivdoi"})
        doi_a = datacite_doi_td.find('a', {'id': "arxiv-doi-link"})
        doi_link = doi_a['href']
        response_data['doi_link'] = doi_link
        if not 'pending' in datacite_doi_td.find('div', {'id': "more-info-desc-1"}).text:
            response_data['datacite_doi'] = True
        # journal ref
        journal_ref_td = div_content_inner.find('td', {'class': "tablecell jref"})
        if journal_ref_td:
            response_data['journal_ref'] = journal_ref_td.string
        # doi
        doi_td = div_content_inner.find('td', {'class': "tablecell doi"})
        if journal_ref_td:
            response_data['doi'] = str(doi_td.contents[0])

        # for a in div_content_inner.find_all('a'):
        #     if a.get('href', ''):
        #         a['href'] = a['href'].replace('https://arxiv.org', '')

        # if get_language() == 'zh-hans':
        #     div_content_inner.find('h1', {'class': "title mathjax"}).span.next_sibling.replace_with(article.title_cn)
        #     div_content_inner.find('blockquote', {'class': "abstract mathjax"}).span.next_sibling.replace_with(article.abstract_cn)

        div_submission_history = soup.find('div', {'class': 'submission-history'})
        submitter_name = ''
        show_email_link = ''
        br_idx = 0
        for i, ct in enumerate(div_submission_history.contents):
            if ct.string and 'From: ' in ct.string.strip():
                submitter_name = ct.string.strip().replace('From: ', '').replace(' [', '')
            if ct.string and ct.string == 'view email' and ct.get('href', ''):
                show_email_link = ct['href']
            if ct.name == 'br':
                br_idx = i
                break
        submission_history_entries = [ str(ct) for ct in div_submission_history.contents[br_idx:] ]
        response_data['submitter_name'] = submitter_name
        response_data['show_email_link'] = show_email_link
        response_data['submission_history_entries'] = submission_history_entries

        div_extra_services = soup.find('div', {'class': 'extra-services'})
        # for img in div_extra_services.find_all('img'):
        #     if img.get('src', ''):
        #         img['src'] = re.sub(r'/static/browse/[\d.]+/images/', '/static/images/', img['src'])
        # div_extra_alink = div_extra_services.find('a', {'id': 'bib-cite-css'})
        # div_extra_alink['href'] = re.sub(r'/static/browse/[\d.]+/css/', '/static/css/', div_extra_alink['href'])

        # ancillary_files related
        ancillary_files_div = div_extra_services.find('div', {'class': "ancillary"})
        if ancillary_files_div:
            response_data['ancillary_files'] = True
        # TODO: need some further work for anc_file_list
        # trackback_ping_count
        trackback_ping_count_div = div_extra_services.find('div', {'class': "extra-general"})
        if trackback_ping_count_div:
            response_data['trackback_ping_count'] = int(trackback_ping_count_div.find('h3').text.strip().split(' ')[0])
        # dblp
        # dblp_div = div_extra_services.find('div', {'class': "dblp"})
        # if dblp_div:
        #     # response_data['dblp'] = True
        # TODO: work for dblp

        # latexml_link = ''
        format_list = ['cn-pdf']
        lis = div_extra_services.find('ul').find_all('li')
        for li in lis:
            if 'PDF' in li.text:
                format_list.append('pdf')
            if 'HTML' in li.text and 'experimental' in li.text:
                format_list.append('latexml')
                # latexml_link = li.a['href']
            if 'Source' in li.text:
                format_list.append('src')
            if 'HTML' in li.text and (not 'experimental' in li.text):
                format_list.append('html')
            if 'Other' in li.text:
                format_list.append('other')
        response_data['format_list'] = format_list

        # for license
        license_effective_uri = ''
        license_icon_uri_path = ''
        license_div = div_extra_services.find('div', {'class': "abs-license"})
        license_a = license_div.find('a')
        if license_a:
            license_effective_uri = license_a['href']
            if 'has_license' in license_a.get('class', []): # return a list
                license_icon_uri_path = license_a.find('img')['src']
        response_data['license_effective_uri'] = license_effective_uri
        response_data['license_icon_uri_path'] = license_icon_uri_path

        # for browse
        response_data['browse_context'] = div_extra_services.find('div', {'class': 'browse'}).find('div', {'class': 'current'}).string

        browse_context_previous_url = None
        browse_context_next_url = None
        prevnext_div = div_extra_services.find('div', {'class': 'prevnext'})
        prev_a = prevnext_div.find('a', {'class': "abs-button prev-url"})
        if prev_a:
            browse_context_previous_url = prev_a['href']
        next_a = prevnext_div.find('a', {'class': "abs-button next-url"})
        if next_a:
            browse_context_next_url = next_a['href']

        response_data['browse_context_previous_url'] = browse_context_previous_url
        response_data['browse_context_next_url'] = browse_context_next_url
        response_data['yyyymm'] = div_extra_services.find('div', {'class': 'list'}).find('a', {'class': "abs-button abs-button-grey abs-button-small context-id"}).string

        response_data['meta_tags'] = [ str(mt) for mt in meta_tags ]
        # response_data['div_content_inner'] = str(div_content_inner)
        # response_data['div_submission_history'] = str(div_submission_history)
        # response_data['div_extra_services'] = str(div_extra_services)
        # response_data['div_labstabs'] = str(soup.find('div', {'id': 'labstabs'}))

        # response_data["article"] = article
        response_data["abs_meta"] = abs_meta
        # response_data["meta_tags"] = meta_tag_metadata(abs_meta)
        # response_data["author_links"] = split_long_author_list(
        #     queries_for_authors(abs_meta.authors.raw), truncate_author_list_size
        # )
        # response_data["url_for_author_search"] = lambda author_query: url_for(
        #     "search_archive",
        #     searchtype="author",
        #     archive=abs_meta.primary_archive.id,
        #     query=author_query,
        # )
        # response_data['latexml_url'] = get_latexml_url(abs_meta)

        # # Dissemination formats for download links
        # response_data["formats"] = abs_meta.get_requested_version().formats()

        # if response_data['latexml_url'] is not None:
        #     response_data['formats'].insert(1, 'latexml')

        sh_entries = [ entry for entry in submission_history_entries if 'KB' in entry ]
        pattern = r"\(([\d,]+) KB\)"
        matches = [ re.search(pattern, text) for text in sh_entries ]
        kbs = [ match.group(1) if match else 0 for match in matches ]
        if language == 'zh-hans':
            sh_dates = [ (chinese_week_days[abs_meta.version_history[v].submitted_date.weekday()] + '， ' + abs_meta.version_history[v].submitted_date.strftime('%Y 年 %-m 月 %-d 日 %H:%M:%S %Z')) for v in range(0, latest_version) ]
        else:
            sh_dates = [ abs_meta.version_history[v].submitted_date.strftime('%a, %-d %b %Y %H:%M:%S %Z') for v in range(0, latest_version) ]
        withdrawn_status = [ True if 'withdrawn' in sh_entry else False for sh_entry in sh_entries ]
        version_entries = [ (v, f'{arxiv_id}v{v}', d, kb, w) for (v, d, kb, w) in zip(range(1, latest_version+1), sh_dates, kbs, withdrawn_status) ]
        response_data['version_entries'] = version_entries
        response_data["withdrawn_versions"] = [ i+1 for i in range(latest_version) if withdrawn_status[i] ]
        response_data["higher_version_withdrawn"] = any(withdrawn_status[request_version:])
        response_data["withdrawn"] = withdrawn_status[request_version-1]
        if response_data["higher_version_withdrawn"]:
            response_data["higher_version_withdrawn_submitter"] = submitter_name

        # response_data["withdrawn_versions"] = []
        # response_data["higher_version_withdrawn"] = False
        # response_data["withdrawn"] = False
        # for ver in abs_meta.version_history:
        #     if ver.withdrawn_or_ignore:
        #         response_data["withdrawn_versions"].append(ver)
        #         if abs_meta.version == ver.version:
        #             response_data["withdrawn"] = True
        #         if not response_data["higher_version_withdrawn"] and ver.version > abs_meta.version:
        #             response_data["higher_version_withdrawn"] = True
        #             response_data["higher_version_withdrawn_submitter"] = _get_submitter(abs_meta.arxiv_identifier, ver.version)

        # response_data["encrypted"] = abs_meta.get_requested_version().source_flag.source_encrypted
        response_data["show_refs_cites"] = _show_refs_cites(arxiv_identifier)
        response_data["show_labs"] = _show_labs(arxiv_identifier)
        response_data["rd_int"] = int(datetime.today().strftime("%Y%m%d%H%M"))

        # _non_critical_abs_data(abs_meta, arxiv_identifier, response_data)

    except AbsNotFoundException as ex:
        if (arxiv_identifier.is_old_id
            and arxiv_identifier.archive in ARCHIVES):
            archive_name = ARCHIVES[arxiv_identifier.archive].full_name
            raise AbsNotFound(
                data={
                    "reason": "old_id_not_found",
                    "arxiv_id": arxiv_id,
                    "archive_id": arxiv_identifier.archive,
                    "archive_name": archive_name,
                }
            ) from ex
        raise AbsNotFound(data={"reason": "not_found", "arxiv_id": arxiv_id}) from ex
    except AbsVersionNotFoundException as ex:
        raise AbsNotFound(
            data={
                "reason": "version_not_found",
                "arxiv_id": arxiv_identifier.idv,
                "arxiv_id_latest": arxiv_identifier.id,
            }
        ) from ex
    except AbsDeletedException as ex:
        raise AbsNotFound(
            data={
                "reason": "deleted",
                "arxiv_id_latest": arxiv_identifier.id,
                "message": ex,
            }
        ) from ex
    except IdentifierIsArchiveException as ex:
        raise AbsNotFound(
            data={"reason": "is_archive", "arxiv_id": arxiv_id, "archive_name": ex}
        ) from ex
    except IdentifierException:
        raise AbsNotFound(data={"arxiv_id": arxiv_id})
    except AbsException as ex:
        raise InternalServerError(
            "There was a problem. If this problem persists, please contact "
            "help@arxiv.org."
        ) from ex

    return response_data, status.OK, response_headers


def _non_critical_abs_data(
    abs_meta: DocMetadata, arxiv_identifier: Identifier, response_data: Dict
) -> None:
    """Get additional non-essential data for the abs page."""
    # The DBLP listing and trackback counts depend on the DB.
    response_data["dblp"] = _check_dblp(abs_meta)
    response_data["trackback_ping_count"] = count_trackback_pings(arxiv_identifier.id)
    if response_data["trackback_ping_count"] > 0:
        response_data["trackback_ping_latest"] = get_trackback_ping_latest_date(
            arxiv_identifier.id
        )

    # Include INSPIRE link in references & citations section
    response_data["include_inspire_link"] = include_inspire_link(abs_meta)

    # Ancillary files
    response_data["ancillary_files"] = get_article_store().get_ancillary_files(abs_meta)

    _prevnext_links(arxiv_identifier, abs_meta.primary_category, response_data)

    response_data["is_covid_match"] = _is_covid_match(abs_meta)
    response_data["datacite_doi"] = get_datacite_doi(
        paper_id=abs_meta.arxiv_id
    )


def _check_request_headers(
    docmeta: DocMetadata, response_data: Dict[str, Any], resp_headers: Dict[str, Any]
) -> bool:
    """Check the request headers, update the response headers accordingly."""
    version = docmeta.get_version()
    if version:
        html_updated = get_latexml_publish_dt(docmeta.arxiv_id, version.version) or datetime.min.replace(tzinfo=timezone.utc)
    else:
        html_updated = datetime.min.replace(tzinfo=timezone.utc)
    last_mod_dt: datetime = max(html_updated, docmeta.modified)

    # Latest trackback ping time depends on the database
    if 'trackback_ping_latest' in response_data \
       and isinstance(response_data['trackback_ping_latest'], datetime) \
       and response_data['trackback_ping_latest'] > last_mod_dt:
        # If there is a more recent trackback ping, use that datetime
        last_mod_dt = response_data["trackback_ping_latest"]

    resp_headers["Last-Modified"] = mime_header_date(last_mod_dt)
    # surrogate-control is used by caching servers like fastly. Caching services may strip this
    resp_headers["Surrogate-Control"] = f"max-age={current_app.config.get('ABS_CACHE_MAX_AGE')}"
    # cache-control is used by browsers, set shorter so refreshes happen on browsers
    resp_headers["Cache-Control"] = f"max-age=3600"

    mod_since_dt = _time_header_parse("If-Modified-Since")
    return bool(mod_since_dt and mod_since_dt.replace(microsecond=0) >= last_mod_dt.replace(microsecond=0))


def _time_header_parse(header: str) -> Optional[datetime]:
    try:
        dt = parser.parse(str(_get_req_header(header)))
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=tzutc())
        return dt
    except (ValueError, TypeError, KeyError):
        pass
    return None


def _get_req_header(header: str) -> Optional[str]:
    """Gets request header, needs to be case insensative for keys.

    HTTP header keys are case insensitive. RFC 2616
    """
    return next((value for key, value in request.headers.items()
                 if key.lower() == header.lower()), None)

def _check_legacy_id_params(request, arxiv_id: str) -> str:
    """Check for legacy request parameters related to old arXiv identifiers.

    Parameters
    ----------
    arxiv_id : str

    Returns
    -------
    arxiv_id: str
        A possibly modified version of the input arxiv_id string.
    """
    if request.GET and "/" not in arxiv_id:
        # To support old references to /abs/<archive>?papernum=\d{7}
        if "papernum" in request.GET:
            return f"{arxiv_id}/{request.GET['papernum']}"

        for param in request.GET:
            # singleton case, where the parameter is the value
            # To support old references to /abs/<archive>?\d{7}
            if not request.GET[param] and re.match(r"^\d{7}$", param):
                return f"{arxiv_id}/{param}"
    return arxiv_id


def _prevnext_links(
    arxiv_identifier: Identifier,
    primary_category: Optional[Cat],
    response_data: Dict[str, Any],
) -> None:
    """Adds previous and next URLs and context to response."""
    context = None
    if "context" in request.args and (
        request.args["context"] == "arxiv"
        or request.args["context"] in CATEGORIES
        or request.args["context"] in ARCHIVES
    ):
        context = request.args["context"]
    elif primary_category:
        context = primary_category.canonical_id
    elif arxiv_identifier.is_old_id:
        if arxiv_identifier.archive in ARCHIVES: #context from old style id
                    context=ARCHIVES[arxiv_identifier.archive].canonical_id

    response_data["browse_context"] = context
    response_data["browse_context_previous_url"] = url_for(
            "browse.previous_next",
            id=arxiv_identifier.id,
            function="prev",
            context=context if context else None,
        )
    response_data["browse_context_next_url"] = url_for(
            "browse.previous_next",
            id=arxiv_identifier.id,
            function="next",
            context=context if context else None,
        )


def _is_covid_match(docmeta: DocMetadata) -> bool:
    """Check whether paper is about COVID-19."""
    for field in (docmeta.title, docmeta.abstract):
        if re.search(
            r"(covid[-\s]?19|corona[\s]?virus|sars[-\s]cov[-\s]?2)",
            field,
            flags=re.I | re.M,
        ):
            return True
    return False


def _check_dblp(docmeta: DocMetadata, db_override: bool = False) -> Optional[Dict]:
    """Check whether paper has DBLP Bibliography entry."""
    if not include_dblp_section(docmeta):
        return None
    identifier = docmeta.arxiv_identifier
    listing_path = None
    author_list: List[str] = []
    # fallback check in case DB service is not available
    if db_override:
        listing_path = get_computed_dblp_listing_path(docmeta)
    else:
        try:
            if identifier.id is None:
                return None
            listing_path = get_dblp_listing_path(identifier.id)
            if not listing_path:
                return None
            author_list = get_dblp_authors(identifier.id)
        except IOError:
            # log this
            return None
    if listing_path is not None:
        bibtex_path = get_dblp_bibtex_path(listing_path)
    else:
        return None
    return {
        "base_url": DBLP_BASE_URL,
        "author_search_url": urljoin(DBLP_BASE_URL, DBLP_AUTHOR_SEARCH_PATH),
        "bibtex_base_url": urljoin(DBLP_BASE_URL, DBLP_BIBTEX_PATH),
        "bibtex_path": bibtex_path,
        "listing_url": urljoin(DBLP_BASE_URL, listing_path),
        "author_list": author_list,
    }


def _get_submitter(arxiv_id: Identifier, ver:Optional[int]=None) -> Optional[str]:
    """Gets the submitter of the version."""
    try:
        abs_meta = get_doc_service().get_abs(f"{arxiv_id.id}v{ver}")
        return abs_meta.submitter.name or None
    except:
        return None

def _show_refs_cites(arxiv_id: Identifier) -> bool:
    NO_REFS_IDS=[
        "2307.10651" #ARXIVCE-2683
        ]
    if arxiv_id.id in NO_REFS_IDS:
        return False
    else:
        return True

def _show_labs(arxiv_id: Identifier) -> bool:
    NO_LABS_IDS=[
        "2307.10651" #ARXIVCE-2683
        ]
    if arxiv_id.id in NO_LABS_IDS:
        return False
    else:
        return True
