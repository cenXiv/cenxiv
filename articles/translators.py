from django.conf import settings


def translator(name):
    if name == 'google':
        import mtranslate as mt
        return lambda text: mt.translate(text, 'zh-CN', 'en')
    if name == 'deepl':
        import httpx, json

        def deepl_translate(text):
            deeplx_api = settings.DEEPLX_API

            data = {
                    "text": text,
                    "source_lang": "EN",
                    "target_lang": "ZH"
            }

            post_data = json.dumps(data)
            r = httpx.post(url=deeplx_api, data=post_data)
            return json.loads(r.content)['data']

        return deepl_translate

    elif name == 'baidu':
        import translators as ts
        return lambda text: ts.translate_text(text, translator=name, from_language='en', to_language='zh')
    elif name == 'tencent':
        from . import tencentCloudTranslator as tcTranslator

        secretId = settings.TENCENT_SECRET_ID
        secretKey = settings.TENCENT_SECRET_KEY
        tc_translator = tcTranslator.TencentCloudTranslator(secretId, secretKey)
        return lambda text: tc_translator.cloud_translate(text, "en", "zh")
    elif name == 'alibaba':
        from .alibabaCloudTranslator import AlibabaCloudTranslator

        secretId = settings.ALIBABA_SECRET_ID
        secretKey = settings.ALIBABA_SECRET_KEY
        ali_translator = AlibabaCloudTranslator(secretId, secretKey)
        return lambda text: ali_translator.translate(text, "en", "zh")
    elif name == 'aliyun':
        from openai import OpenAI

        model = settings.ALIYUNBAILIAN_MODEL
        api_key = settings.ALIYUNBAILIAN_API_KEY
        base_url = settings.ALIYUNBAILIAN_BASE_URL
        system_content = settings.ALIYUNBAILIAN_SYSTEM_CONTENT

        client = OpenAI(
            # 若没有配置环境变量，请用百炼API Key将下行替换为：api_key="sk-xxx",
            api_key=api_key,
            base_url=base_url,
        )
        return lambda text: client.chat.completions.create(
            model=model, # 此处以qwen-plus为例，可按需更换模型名称。模型列表：https://help.aliyun.com/zh/model-studio/getting-started/models
            messages=[
                {'role': 'system', 'content': system_content},
                {'role': 'user', 'content': text}
                ],
            ).choices[0].message.content

    elif name == 'ollama':
        from ollama import Client

        ollama_host = settings.OLLAMA_LOCATION
        ollama_model = settings.OLLAMA_MODEL
        ollama_message_content_prefix = settings.OLLAMA_MESSAGE_CONTENT_PREFIX
        client = Client(host=ollama_host)
        return lambda text: client.chat(model=ollama_model, messages=[{'role': 'user', 'content': f'{ollama_message_content_prefix} {text}'},]).message.content.strip()

    else:
        raise NotImplementedError(f'Translator {name} not implemented yet')