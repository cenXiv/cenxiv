import json


chinese_week_days = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']

def get_translation_dict():
    with open("articles/groups_dict.json", "r") as f:
        groups_dict = json.load(f)
    with open("articles/categories_dict.json", "r") as f:
        categories_dict = json.load(f)

    return groups_dict | categories_dict