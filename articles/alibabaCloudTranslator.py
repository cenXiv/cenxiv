# -*- coding: utf-8 -*-
from alibabacloud_alimt20181012.client import Client as alimt20181012Client
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_alimt20181012 import models as alimt_20181012_models
from alibabacloud_tea_util import models as util_models
# from alibabacloud_tea_util.client import Client as UtilClient


class AlibabaCloudTranslator(object):
    def __init__(self, secret_id, secret_key, endpoint="mt.aliyuncs.com"):
        config = open_api_models.Config(access_key_id=secret_id, access_key_secret=secret_key)
        config.endpoint = 'mt.aliyuncs.com'
        self.client = alimt20181012Client(config)

    def translate(self, original_text, source_lang, target_lang):
        translate_general_request = alimt_20181012_models.TranslateGeneralRequest(
            format_type='text',
            source_language=source_lang,
            target_language=target_lang,
            source_text=original_text
        )
        runtime = util_models.RuntimeOptions()
        try:
            resp = self.client.translate_general_with_options(translate_general_request, runtime)
            return resp.body.data.translated
        except Exception as error:
            print(error.message)
            print(error.data.get("Recommend"))