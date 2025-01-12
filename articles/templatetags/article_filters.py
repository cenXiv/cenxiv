from django import template


register = template.Library()

@register.filter(name='dict_get_none')
def dict_get_none(dictionary, key):
    """
    Retrieve the value for a given key from a dictionary.

    If the key does not exist in the dictionary, return None.

    Args:
        dictionary (dict): The dictionary to search.
        key: The key to look for in the dictionary.

    Returns:
        The value associated with the key if found, otherwise None.
    """
    return dictionary.get(key, None)

@register.filter(name='dict_get_key')
def dict_get_key(dictionary, key):
    """
    Retrieve the value for a given key from a dictionary.

    If the key does not exist, return the key itself.

    Args:
        dictionary (dict): The dictionary to search.
        key: The key to look for in the dictionary.

    Returns:
        The value associated with the key if found, otherwise the key itself.
    """
    return dictionary.get(key, key)