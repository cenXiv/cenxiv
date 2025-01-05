"""Form for month selection of list controller."""
from typing import List

from django import forms
from arxiv.taxonomy.definitions import Archive

MONTHS = [
    ('all', 'all months'),
    ('01', '01 (Jan)'),
    ('02', '02 (Feb)'),
    ('03', '03 (Mar)'),
    ('04', '04 (Apr)'),
    ('05', '05 (May)'),
    ('06', '06 (Jun)'),
    ('07', '07 (Jul)'),
    ('08', '08 (Aug)'),
    ('09', '09 (Sep)'),
    ('10', '10 (Oct)'),
    ('11', '11 (Nov)'),
    ('12', '12 (Dec)'),
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
