"""Form for month selection of list controller."""
from typing import List

from django import forms
from django.utils.translation import gettext_lazy as _
from arxiv.taxonomy.definitions import Archive

MONTHS = [
    ('all', _('all months')),
    ('01', _('01 (Jan)')),
    ('02', _('02 (Feb)')),
    ('03', _('03 (Mar)')),
    ('04', _('04 (Apr)')),
    ('05', _('05 (May)')),
    ('06', _('06 (Jun)')),
    ('07', _('07 (Jul)')),
    ('08', _('08 (Aug)')),
    ('09', _('09 (Sep)')),
    ('10', _('10 (Oct)')),
    ('11', _('11 (Nov)')),
    ('12', _('12 (Dec)')),
]


class ByMonthForm(forms.Form):
    """Form for browse by month input on archive pages.

    This doesn't try to account for the start date of the
    archive, end date of the archive or dates in the future.
    It just accepts these, and expects the /list controller
    to deal with dates for which there are no articles.
    """

    year = forms.ChoiceField(
        required=True,
        choices=[],
        widget=forms.Select()
    )
    month = forms.ChoiceField(
        required=True,
        choices=MONTHS,
        widget=forms.Select()
    )
    archive = forms.CharField(
        required=True,
        widget=forms.HiddenInput()
    )
    # submit = forms.CharField(
    #     widget=forms.SubmitInput(attrs={'value': 'Go'})
    # )
    # submit = forms.Button(label='Go', type='submit')

    def __init__(self, archive: Archive, years: List[int], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['year'].choices = [(str(ye), str(ye)) for ye in years]
        self.fields['archive'].initial = archive.id
