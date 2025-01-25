import mtranslate as mt
import translators as ts


def translator(name):
    if name == 'google':
        return lambda text: mt.translate(text, 'zh-CN', 'en')
    elif name in ['baidu', 'alibaba']:
        return lambda text: ts.translate_text(text, translator=name, from_language='en', to_language='zh')
    else:
        raise NotImplementedError(f'Translator {name} not implemented yet')