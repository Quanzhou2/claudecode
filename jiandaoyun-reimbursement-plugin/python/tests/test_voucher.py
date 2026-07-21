# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voucher_similarity_check as vou  # noqa: E402


class TestVoucherEndToEnd(unittest.TestCase):
    def setUp(self):
        self._orig_http = vou.http_post_json
        self._orig_fetch = vou.fetch_image
        vou.CONFIG["jdy"]["apiKey"] = "k"
        vou.CONFIG["llm"].update({"apiKey": "llmk", "threshold": 0.9, "batchSize": 8, "maxCandidates": 60})
        vou.CONFIG["fields"].update({"subform": "sub", "attachment": "att",
                                     "recordNo": "no", "flowStatus": "FILL_flow"})
        vou.CONFIG["dedup"]["excludeSelf"] = True
        vou.fetch_image = lambda url: {"base64": "AAAA", "mediaType": "image/jpeg"}

    def tearDown(self):
        vou.http_post_json = self._orig_http
        vou.fetch_image = self._orig_fetch

    def _install(self, records, score):
        def router(url, body, headers, timeout_ms=15000):
            if "data/list" in url:
                return {"data": records}
            if "chat/completions" in url:
                content = '{"scores":[%s],"similarity":%s,"sameDocument":true,"reason":"金额单号一致"}' % (score, score)
                return {"choices": [{"message": {"content": content}}]}
            return {}
        vou.http_post_json = router

    def test_no_history_passes(self):
        self._install([], 0.0)
        r = vou.main({"imageUrl": "http://x/v.jpg"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "验证通过")

    def test_duplicate_over_threshold(self):
        recs = [{"_id": "A", "no": "BX-007", "sub": [{"_id": "r1", "att": [{"url": "http://h/1.jpg"}]}]}]
        self._install(recs, 0.95)
        r = vou.main({"imageUrl": "http://x/v.jpg", "dataId": "CUR"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "凭证重复")
        self.assertTrue(r["duplicate"])
        self.assertEqual(r["matchedRecord"]["recordNo"], "BX-007")
        self.assertGreaterEqual(r["similarity"], 0.9)

    def test_below_threshold_passes(self):
        recs = [{"_id": "A", "no": "BX-007", "sub": [{"_id": "r1", "att": [{"url": "http://h/1.jpg"}]}]}]
        self._install(recs, 0.4)
        r = vou.main({"imageUrl": "http://x/v.jpg", "dataId": "CUR"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "验证通过")

    def test_exclude_self(self):
        recs = [{"_id": "CUR", "no": "BX-CUR", "sub": [{"_id": "r1", "att": [{"url": "http://h/self.jpg"}]}]}]
        self._install(recs, 0.99)
        r = vou.main({"imageUrl": "http://x/v.jpg", "dataId": "CUR"})
        self.assertTrue(r["ok"], "自身图片被排除后无候选")

    def test_llm_error_blocks(self):
        recs = [{"_id": "A", "no": "BX-007", "sub": [{"_id": "r1", "att": [{"url": "http://h/1.jpg"}]}]}]

        def router(url, body, headers, timeout_ms=15000):
            if "data/list" in url:
                return {"data": recs}
            raise RuntimeError("LLM 超时")
        vou.http_post_json = router
        r = vou.main({"imageUrl": "http://x/v.jpg", "dataId": "CUR"})
        self.assertFalse(r["ok"])
        self.assertIn("相似度分析失败", r["note"])


if __name__ == "__main__":
    unittest.main()
