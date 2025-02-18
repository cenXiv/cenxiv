import json
from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.tmt.v20180321 import tmt_client, models


class TencentCloudTranslator(object):
    def __init__(self, secret_id, secret_key, region="ap-guangzhou"):
        cred = credential.Credential(secret_id, secret_key)
        http_profile = HttpProfile()
        http_profile.endpoint = "tmt.tencentcloudapi.com"
        client_profile = ClientProfile()
        client_profile.httpProfile = http_profile
        self.client = tmt_client.TmtClient(cred, region, client_profile)
        self.translateReq = models.TextTranslateRequest()

    def cloud_translate(self, original_text, source_lang, target_lang):
        try:
            params = {
                "Source": source_lang,
                "Target": target_lang,
                "ProjectId": 0,
                "SourceText": original_text
            }
            self.translateReq.from_json_string(json.dumps(params))
            translate_resp = self.client.TextTranslate(self.translateReq)
            return json.loads(translate_resp.to_json_string())["TargetText"]
        except TencentCloudSDKException as err:
            print(err)
