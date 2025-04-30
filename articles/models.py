from django.db import models
from django.utils.translation import gettext_lazy as _

class Article(models.Model):
    source_archive = models.CharField(_('Source Archive'), max_length=100, default='arxiv')
    entry_id = models.CharField(_('Entry ID'), max_length=100)
    entry_version = models.PositiveIntegerField(_('Entry Version'), default=1)
    title_en = models.CharField(_('English Title'), max_length=1000)
    title_cn = models.CharField(_('Chinese Title'), max_length=1000)
    abstract_en = models.TextField(_('English Abstract'))
    abstract_cn = models.TextField(_('Chinese Abstract'))
    published_date = models.DateTimeField(_('Article Published Date'))
    updated_date = models.DateTimeField(_('Article Updated Date'))
    comment_en = models.CharField(_('English Comment'), max_length=500, null=True, blank=True)
    comment_cn = models.CharField(_('Chiness Comment'), max_length=500, null=True, blank=True)
    journal_ref_en = models.CharField(_('English Journal Ref'), max_length=500, null=True, blank=True)
    journal_ref_cn = models.CharField(_('Chinses Journal Ref'), max_length=500, null=True, blank=True)
    doi = models.CharField(_('DOI'), max_length=200, null=True, blank=True)
    primary_category = models.CharField(_('Primary Category'), max_length=100)
    created_at = models.DateTimeField(_('Created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('Updated at'), auto_now=True)
    translation_validated = models.BooleanField(_('Translation Validated'), default=False)

    class Meta:
        ordering = ['-updated_date']
        verbose_name = _('Article')
        verbose_name_plural = _('Articles')
        indexes = [
            models.Index(fields=['-updated_date']),
            models.Index(fields=['source_archive', 'entry_id', 'entry_version']),
        ]
        # 添加唯一约束
        constraints = [
            models.UniqueConstraint(
                fields=['source_archive', 'entry_id', 'entry_version'],
                name='unique_article_version'
            )
        ]

    def __str__(self):
        return f"{self.source_archive}:{self.entry_id}v{self.entry_version}"

    def get_title(self, language='zh-hans'):
        return self.title_cn if language == 'zh-hans' else self.title_en

    def get_abstract(self, language='zh-hans'):
        return self.abstract_cn if language == 'zh-hans' else self.abstract_en


class Author(models.Model):
    name = models.CharField(_('Name'), max_length=100)
    article = models.ForeignKey('Article', on_delete=models.CASCADE, related_name='authors')

    class Meta:
        verbose_name = _('Author')
        verbose_name_plural = _('Authors')

    def __str__(self):
        return f"{self.name}"


class Category(models.Model):
    name = models.CharField(_('Name'), max_length=100)
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