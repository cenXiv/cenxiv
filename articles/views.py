from django.shortcuts import render
from django.utils.translation import get_language
from django.core.paginator import Paginator
from django.db.models import Q
from .models import Article
from .utils import fetch_arxiv_feed, parse_arxiv_feed

def article_list(request):
    # 获取当前语言
    current_language = get_language()

    # 获取搜索查询参数
    query = request.GET.get('q', '')
    category = request.GET.get('category', '')

    # 从数据库获取文章
    articles = Article.objects.all()

    # 如果数据库为空，从 arXiv 获取数据
    if not articles.exists():
        xml_content = fetch_arxiv_feed()
        parsed_articles = parse_arxiv_feed(xml_content)
        print(f'parsed_articles: {parsed_articles}')

        # 保存到数据库
        for article_data in parsed_articles:
            Article.objects.create(**article_data)

        articles = Article.objects.all()

    # 应用搜索过滤
    if query:
        articles = articles.filter(
            Q(title_en__icontains=query) |
            Q(title_cn__icontains=query) |
            Q(abstract_en__icontains=query) |
            Q(abstract_cn__icontains=query) |
            Q(authors__icontains=query) |
            Q(arxiv_id__icontains=query)
        )

    if category:
        articles = articles.filter(category__icontains=category)

    for article in articles:
        article.title_in_language = article.get_title(current_language)
        article.abstract_in_language = article.get_abstract(current_language)

    # 分页
    paginator = Paginator(articles, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # 获取所有可用的分类
    categories = Article.objects.values_list('category', flat=True).distinct()

    context = {
        'page_obj': page_obj,
        'query': query,
        'category': category,
        'categories': categories,
    }

    return render(request, 'articles/article_list.html', context)