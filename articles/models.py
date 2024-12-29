from django.db import models
from django.utils.translation import gettext_lazy as _

class Article(models.Model):
    arxiv_id = models.CharField(_('arXiv ID'), max_length=50, unique=True)
    title_en = models.CharField(_('English Title'), max_length=500)
    title_cn = models.CharField(_('Chinese Title'), max_length=500)
    abstract_en = models.TextField(_('English Abstract'))
    abstract_cn = models.TextField(_('Chinese Abstract'))
    authors = models.TextField(_('Authors'))
    published_date = models.DateTimeField(_('Published Date'))
    updated_date = models.DateTimeField(_('Updated Date'))
    pdf_url = models.URLField(_('PDF URL'))
    category = models.CharField(_('Category'), max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-published_date']
        verbose_name = _('Article')
        verbose_name_plural = _('Articles')

    def __str__(self):
        return f"{self.arxiv_id}: {self.title_en}"

    def get_title(self, language='zh-hans'):
        return self.title_cn if language == 'zh-hans' else self.title_en

    def get_abstract(self, language='zh-hans'):
        return self.abstract_cn if language == 'zh-hans' else self.abstract_en