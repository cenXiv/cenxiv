from django.contrib import admin
from django.db import models
from django import forms
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from .models import Article#, Author, Category, Link
from django.utils.html import escape
from django.contrib.admin import DateFieldListFilter
from django.contrib.admin.models import LogEntry, CHANGE
from django.contrib.contenttypes.models import ContentType
from django.forms.widgets import TextInput, Textarea


class TitleCnWidget(TextInput):
    template_name = 'admin/articles/title_cn_widget.html'

    class Media:
        js = ('admin/js/title_cn_preview.js',)

class AbstractCnWidget(Textarea):
    template_name = 'admin/articles/abstract_cn_widget.html'

    class Media:
        js = ('admin/js/abstract_cn_preview.js',)

# class AuthorInline(admin.StackedInline):
#     model = Author

# class CategoryInline(admin.StackedInline):
#     model = Category

# class LinkInline(admin.StackedInline):
#     model = Link

@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    # readonly_fields = ['source_archive', 'entry_id', 'entry_version', 'title_en', 'abstract_en', 'published_date', 'updated_date', 'comment_en', 'journal_ref_en', 'doi', 'primary_category', 'display_authors', 'display_categories', 'display_links']
    list_filter = [
        ('updated_date', DateFieldListFilter),
        'primary_category',
        'translation_validated'
    ]
    search_fields = [
        'entry_id',
        'title_en', 'title_cn',
        'abstract_en', 'abstract_cn',
        'authors__name',  # 支持作者搜索
        'primary_category'
    ]
    # search_help_text = "支持按文章ID、标题、摘要、作者和分类搜索"
    search_help_text = _("Supports searching by article ID, title, abstract, author and category")
    show_facets = admin.ShowFacets.ALWAYS
    # inlines = [AuthorInline, CategoryInline, LinkInline]

    def has_add_permission(self, request):
        return False  # Disable the add permission

    def has_delete_permission(self, request, obj=None):
        return False  # Disable the delete permission

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        readonly_fields.extend(['source_archive', 'entry_id', 'entry_version', 'title_en', 'abstract_en', 'published_date', 'updated_date', 'comment_en', 'journal_ref_en', 'doi', 'primary_category', 'display_authors', 'display_categories', 'display_links'])
        if obj and not obj.comment_en:
            readonly_fields.append('comment_cn')
        if obj and not obj.journal_ref_en:
            readonly_fields.append('journal_ref_cn')
        return readonly_fields

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related(
            'authors',
            'categories',
            'links'
        )

    def display_authors(self, obj):
        # 使用缓存的预加载数据
        authors = list(obj.authors.all())  # 已经预加载
        return ', '.join(author.name for author in authors)
    display_authors.short_description = _('Authors')  # Set the column name in the admin

    def display_categories(self, obj):
        # Query all category names for the article and join them with ', '
        return ', '.join(cat.name for cat in obj.categories.all())
    display_categories.short_description = _('Categories')  # Set the column name in the admin

    def display_links(self, obj):
        return mark_safe('<br>'.join(
            f'<a href="{escape(link.url)}" target="_blank" rel="noopener noreferrer nofollow">{escape(link.url)}</a>'
            for link in obj.links.all()
        ))
    display_links.short_description = _('Links')  # Set the column name in the admin

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        if change:  # 记录修改操作
            LogEntry.objects.log_action(
                user_id=request.user.id,
                content_type_id=ContentType.objects.get_for_model(obj).pk,
                object_id=obj.pk,
                object_repr=str(obj),
                action_flag=CHANGE,
                # change_message=f"修改了文章 {obj.entry_id} 的翻译"
                change_message=_(f"Modified the translation of article {obj.entry_id}")
            )

    # Define the fieldsets for the admin form
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
            'widget': forms.TextInput(attrs={
                'style': 'width: 1000px;',
                'class': 'vLargeTextField'
            })
        },
        models.TextField: {
            'widget': forms.Textarea(attrs={
                'rows': 10,
                'cols': 100,
                'class': 'vLargeTextField'
            })
        }
    }

    def formfield_for_dbfield(self, db_field, **kwargs):
        if db_field.name == 'title_cn':
            kwargs['widget'] = TitleCnWidget(attrs={
                'style': 'width: 1000px;',
                'class': 'vLargeTextField'
            })
        elif db_field.name == 'abstract_cn':
            kwargs['widget'] = AbstractCnWidget(attrs={
                'rows': 10,
                'cols': 100,
                'class': 'vLargeTextField'
            })
        return super().formfield_for_dbfield(db_field, **kwargs)