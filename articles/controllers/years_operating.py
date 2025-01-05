"""Year link functions."""

from typing import List, Tuple
from datetime import date
from django.urls import reverse 

from arxiv.taxonomy.definitions import Archive


def years_operating(archive: Archive) -> List[int]:
    """Returns list of years operating in desc order. ex [1993,1992,1991]."""
    start = archive.start_date.year
    if archive.end_date:
        end=archive.end_date.year
    else:
        end=date.today().year
    return list(reversed(range(start, end + 1)))


def stats_by_year(archive: Archive, years: List[int], page_year: int=0) -> List[Tuple[str, str]]:
    """Returns links to year pages."""
    return [(_year_stats_link(archive.id, year, page_year), str(year)) for year in years]


def _year_stats_link(archive_id: str, year: int, page_year: int = 0) -> str:
    if year == page_year:
        return ''
    else:
        # Use Django's reverse URL pattern instead of Flask's url_for
        return reverse('articles:year', kwargs={'year': str(year), 'archive': archive_id})
