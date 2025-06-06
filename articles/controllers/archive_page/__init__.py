"""Archive landing page."""

import copy
import json
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional
from http import HTTPStatus as status

from arxiv.taxonomy.definitions import ARCHIVES, ARCHIVES_ACTIVE, ARCHIVES_SUBSUMED, CATEGORIES
from arxiv.taxonomy.category import Category, Archive
from arxiv.integration.fastly.headers import add_surrogate_key

from browse.controllers import biz_tz
# from browse.controllers.archive_page.by_month_form import ByMonthForm
# from browse.controllers.years_operating import stats_by_year, years_operating
from browse.controllers.years_operating import years_operating
from browse.controllers.response_headers import abs_expires_header

from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from ..years_operating import stats_by_year
from .by_month_form import ByMonthForm


def get_archive(archive_id: Optional[str]) -> Tuple[Dict[str, Any], int, Dict[str, Any]]:
    """Gets archive page."""
    data: Dict[str, Any] = {}
    response_headers: Dict[str, Any] = {}
    response_headers["Surrogate-Control"] = "max-age=86400" #one day
    response_headers = add_surrogate_key(response_headers, ["archive"])

    if not archive_id or archive_id == "list":
        return archive_index("list", status_in=status.OK)

    archive = ARCHIVES.get(archive_id, None)
    if not archive: # check if maybe its a category
        category = CATEGORIES.get(archive_id, None)
        if category:
            archive = category.get_archive()
    if not archive:
        return archive_index(archive_id, status_in=status.NOT_FOUND)

    if archive.is_active == False: # subsumed archives
        subsuming_category = archive.get_canonical()
        if not isinstance(subsuming_category, Category):
            return archive_index(archive_id, status_in=status.NOT_FOUND)
        data["subsumed_id"] = archive.id
        data["subsuming_category"] = subsuming_category
        archive = subsuming_category.get_archive()

    language = get_language()

    cats_list = copy.deepcopy(category_list(archive))
    years = years_operating(archive)
    data["years"] = [datetime.now().year, datetime.now().year-1] # only last 90 days allowed anyways
    data["months"] = MONTHS
    data["days"] = DAYS
    data["archive"] = archive
    data["archive_start_date"] = archive.start_date.strftime('%Y年%-m月') if language == 'zh-hans' else archive.start_date.strftime('%B %Y')
    data["list_form"] = ByMonthForm(archive, years)
    data["stats_by_year"] = stats_by_year(archive, years)
    # data["category_list"] = cats_list
    data["current_month"] = datetime.now().strftime('%m')
    data["template"] = "archive/single_archive.html"

    if language == 'zh-hans':
        with open("articles/categories_description_dict.json", "r", encoding='utf-8') as f:
            categories_description_dict = json.load(f)
        for cat in cats_list:
            if cat.id in categories_description_dict:
                for key, val in categories_description_dict[cat.id].items():
                    cat.description = cat.description.replace(key, val)
    data["category_list"] = cats_list

    return data, status.OK, response_headers


def archive_index(bad_archive_id: str, status_in: int) -> Tuple[Dict[str, Any], int, Dict[str, Any]]:
    """Landing page for when there is no archive specified."""
    data: Dict[str, Any] = {}
    data["bad_archive"] = bad_archive_id

    archives = [
        value
        for key,value in ARCHIVES_ACTIVE.items()
        if not key.startswith("test")
    ]
    archives.sort(key=lambda x: x.id)
    data["archives"] = archives

    defunct = [
        ARCHIVES[id]
        for id in ARCHIVES_SUBSUMED.keys()
    ]
    defunct.sort(key=lambda x: x.id)
    data["defunct"] = defunct

    data["template"] = "archive/archive_list_all.html"
    headers: Dict[str, str]={}
    headers = add_surrogate_key(headers, ["archive"])
    return data, status_in, headers


def category_list(archive: Archive) -> List[Category]:
    """Returns active categories for archive."""
    cats = [cat for cat in archive.get_categories()]
    cats.sort(key=lambda x: x.id)
    return cats


def _write_expires_header(response_headers: Dict[str, Any]) -> None:
    """Writes an expires header for the response."""
    response_headers["Expires"] = abs_expires_header(biz_tz())


DAYS = ["{:0>2d}".format(i) for i in range(1, 32)]

MONTHS = [
    ("01", _("01 (Jan)")),
    ("02", _("02 (Feb)")),
    ("03", _("03 (Mar)")),
    ("04", _("04 (Apr)")),
    ("05", _("05 (May)")),
    ("06", _("06 (Jun)")),
    ("07", _("07 (Jul)")),
    ("08", _("08 (Aug)")),
    ("09", _("09 (Sep)")),
    ("10", _("10 (Oct)")),
    ("11", _("11 (Nov)")),
    ("12", _("12 (Dec)")),
]
