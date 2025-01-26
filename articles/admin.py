from django.contrib import admin
from django.db import models
from django import forms
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from .models import Article#, Author, Category, Link


# class AuthorInline(admin.StackedInline):
#     model = Author

# class CategoryInline(admin.StackedInline):
#     model = Category

# class LinkInline(admin.StackedInline):
#     model = Link

@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    # readonly_fields = ['source_archive', 'entry_id', 'entry_version', 'title_en', 'abstract_en', 'published_date', 'updated_date', 'comment_en', 'journal_ref_en', 'doi', 'primary_category', 'display_authors', 'display_categories', 'display_links']
    list_filter = ['updated_date', 'primary_category']
    search_fields = ['entry_id', 'title_en', 'title_cn', 'abstract_en', 'abstract_cn']
    show_facets = admin.ShowFacets.ALWAYS
    # inlines = [AuthorInline, CategoryInline, LinkInline]

    def has_add_permission(self, request):
        return False  # Disable the add permission

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        readonly_fields.extend(['source_archive', 'entry_id', 'entry_version', 'title_en', 'abstract_en', 'published_date', 'updated_date', 'comment_en', 'journal_ref_en', 'doi', 'primary_category', 'display_authors', 'display_categories', 'display_links'])
        if obj and not obj.comment_en:
            readonly_fields.append('comment_cn')
        if obj and not obj.journal_ref_en:
            readonly_fields.append('journal_ref_cn')
        return readonly_fields

    def display_authors(self, obj):
        # Query all author names for the article and join them with ', '
        return ', '.join(author.name for author in obj.authors.all())
    display_authors.short_description = _('Authors')  # Set the column name in the admin

    def display_categories(self, obj):
        # Query all category names for the article and join them with ', '
        return ', '.join(cat.name for cat in obj.categories.all())
    display_categories.short_description = _('Categories')  # Set the column name in the admin

    def display_links(self, obj):
        # Query all link urls for the article and create clickable HTML links
        return mark_safe('<br>'.join(f'<a href="{link.url}" target="_blank" rel="noopener noreferrer">{link.url}</a>' for link in obj.links.all()))
    display_links.short_description = _('Links')  # Set the column name in the admin

    fieldsets = [
        # (_("Entry Info"), {
        #     "fields": ["source_archive", "entry_id", "entry_version"]
        # }),
        (_("Title"), {
            "fields": ["title_en", "title_cn"]
        }),
        (_("Authors"), {
            "fields": ["display_authors"]
        }),
        (_("Abstract"), {
            "fields": ["abstract_en", "abstract_cn"]
        }),
        (_("Comment"), {
            "fields": ["comment_en", "comment_cn"]
        }),
        (_("Journal Ref"), {
            "fields": ["journal_ref_en", "journal_ref_cn"]
        }),
        (_("Category"), {
            "fields": ["primary_category", 'display_categories', "doi"]
        }),
        (_("Link"), {
            "fields": ['display_links']
        }),
        (_("Dates"), {
            "fields": ["published_date", "updated_date"]
        }),
        (_("Validation"), {
            "fields": ["translation_validated"]
        }),
    ]

    # Override the formfield_overrides to customize the width of title_cn
    formfield_overrides = {
        models.CharField: {
            'widget': forms.TextInput(attrs={'style': 'width: 1000px;'})  # Set the width to 500px
        },
    }