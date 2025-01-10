from django.db import models
from django.utils.translation import gettext_lazy as _

class Article(models.Model):
    source_archive = models.CharField(_('Source Archive'), max_length=50, default='arxiv')
    entry_id = models.CharField(_('Entry ID'), max_length=50)
    entry_version = models.PositiveIntegerField(_('Entry Version'), default=1)
    title_en = models.CharField(_('English Title'), max_length=500)
    title_cn = models.CharField(_('Chinese Title'), max_length=500)
    abstract_en = models.TextField(_('English Abstract'))
    abstract_cn = models.TextField(_('Chinese Abstract'))
    published_date = models.DateTimeField(_('Article Published Date'))
    updated_date = models.DateTimeField(_('Article Updated Date'))
    comment = models.CharField(_('Comment'), max_length=200, null=True, blank=True)
    journal_ref = models.CharField(_('Journal Ref'), max_length=200, null=True, blank=True)
    doi = models.CharField(_('DOI'), max_length=200, null=True, blank=True)
    primary_category = models.CharField(_('Primary Category'), max_length=50)
    created_at = models.DateTimeField(_('Created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('Updated at'), auto_now=True)
    translation_validated = models.BooleanField(_('Translation Validated'), default=False)

    class Meta:
        ordering = ['-published_date']
        verbose_name = _('Article')
        verbose_name_plural = _('Articles')

    def __str__(self):
        return f"{self.source_archive}:{self.entry_id}v{self.entry_version}"

    def get_title(self, language='zh-hans'):
        return self.title_cn if language == 'zh-hans' else self.title_en

    def get_abstract(self, language='zh-hans'):
        return self.abstract_cn if language == 'zh-hans' else self.abstract_en


class Author(models.Model):
    name = models.CharField(_('Name'), max_length=50)
    article = models.ForeignKey('Article', on_delete=models.CASCADE, related_name='authors')

    class Meta:
        verbose_name = _('Author')
        verbose_name_plural = _('Authors')

    def __str__(self):
        return f"{self.name}"


class Category(models.Model):
    name = models.CharField(_('Name'), max_length=50)
    article = models.ForeignKey('Article', on_delete=models.CASCADE, related_name='categories')

    class Meta:
        verbose_name = _('Category')
        verbose_name_plural = _('Categories')

    def __str__(self):
        return f"{self.name}"


class Link(models.Model):
    url = models.URLField(_('URL'))
    article = models.ForeignKey('Article', on_delete=models.CASCADE, related_name='links')

    class Meta:
        verbose_name = _('Link')
        verbose_name_plural = _('Links')

    def __str__(self):
        return f"{self.url}"