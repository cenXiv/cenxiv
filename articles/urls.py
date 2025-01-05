from django.urls import path
from . import views

app_name = 'articles'

urlpatterns = [
    path('', views.home, name='home'),
    path('articles/', views.article_list, name='article_list'),
    path('search/<str:archive_id>/', views.search_archive, name='search_archive'),
    path('archive/', views.archive, name='archive_index'),
    path('archive/<str:archive_id>/', views.archive, name='archive'),
    path('help/archive/<str:archive_id>/', views.help_archive_description, name='help_archive_description'),
    path('year/<str:archive>/', views.year_default, name='year_default'),
    path('year/<str:archive>/<int:year>/', views.year_view, name='year'),
    path('list/', views.list_articles, {'context': '', 'subcontext': ''}, name='list_default'),
    path('list/<str:context>/<str:subcontext>/', views.list_articles, name='list_articles'),
    path('catchup/', views.catchup_form, name='catchup_form'),
    path('catchup/<str:subject>/<str:date>/', views.catchup, name='catchup'),
    path('author/<str:article_id>/<str:author_id>/', views.author_search, name='author_search'),
]