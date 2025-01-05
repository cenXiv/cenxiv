from django import template

register = template.Library()

@register.filter(name='dict_get')
def dict_get(dictionary, key):
    return dictionary.get(key, None)