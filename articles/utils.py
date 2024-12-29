import requests
import xml.etree.ElementTree as ET
from mtranslate import translate
from datetime import datetime
from django.conf import settings

def fetch_arxiv_feed(category='cs'):
    """获取 arXiv feed"""
    url = f'https://rss.arxiv.org/atom/{category}'
    response = requests.get(url)
    return response.text

def translate_text(text, to_lang='zh-cn'):
    """翻译文本"""
    try:
        return translate(text, to_lang)
    except Exception as e:
        print(f"Translation error: {e}")
        return text

def parse_arxiv_feed(xml_content):
    """解析 arXiv feed 内容"""
    root = ET.fromstring(xml_content)
    namespace = {'atom': 'http://www.w3.org/2005/Atom'}
    articles = []

    for entry in root.findall('atom:entry', namespace):
        article = {
            'arxiv_id': entry.find('atom:id', namespace).text.split('/')[-1],
            'title_en': entry.find('atom:title', namespace).text.strip(),
            'abstract_en': entry.find('atom:summary', namespace).text.strip(),
            'authors': ', '.join([author.find('atom:name', namespace).text
                                for author in entry.findall('atom:author', namespace)]),
            'published_date': datetime.strptime(
                entry.find('atom:published', namespace).text,
                '%Y-%m-%dT%H:%M:%SZ'
            ),
            'updated_date': datetime.strptime(
                entry.find('atom:updated', namespace).text,
                '%Y-%m-%dT%H:%M:%SZ'
            ),
            'pdf_url': next(
                link.get('href') for link in entry.findall('atom:link', namespace)
                if link.get('title') == 'pdf'
            ),
            'category': entry.find('atom:category', namespace).get('term'),
        }

        # 翻译标题和摘要
        article['title_cn'] = translate_text(article['title_en'])
        article['abstract_cn'] = translate_text(article['abstract_en'])

        articles.append(article)

    return articles