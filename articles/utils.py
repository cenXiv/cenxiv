import json


def get_translation_dict():
    with open("articles/groups_dict.json", "r") as f:
        groups_dict = json.load(f)
    with open("articles/categories_dict.json", "r") as f:
        categories_dict = json.load(f)

    return groups_dict | categories_dict