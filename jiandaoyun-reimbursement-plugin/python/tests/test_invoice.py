# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import invoice_recognize_verify_dedup as inv  # noqa: E402


class TestInvoiceEndToEnd(unittest.TestCase):
    def setUp(self):
        self._orig_http = inv.http_post_json
        self._orig_fetch = inv.fetch_image
        # OCR 改为多模态 LLM
        inv.CONFIG["ocr"] = {"endpoint": "http://test/llm-ocr", "apiKey": "ocrk", "model": "gpt-4o", "timeoutMs": 5000}
        inv.CONFIG["verify"]["endpoint"] = "http://test/verify"
        inv.CONFIG["verify"]["requireVerify"] = True
        inv.CONFIG["jdy"]["apiKey"] = "k"
        inv.CONFIG["fields"].update({"subform": "sub", "invoiceNumber": "num",
                                     "invoiceCode": "code", "recordNo": "no", "flowStatus": "FILL_flow"})
        inv.CONFIG["dedup"].update({"alsoMatchCode": True, "excludeSelf": True})
        inv.fetch_image = lambda url, t=5000: {"base64": "AAAA", "mediaType": "image/jpeg"}
        self.verify_calls = {"n": 0}

    def tearDown(self):
        inv.http_post_json = self._orig_http
        inv.fetch_image = self._orig_fetch

    def _ocr_resp(self, content):
        return {"choices": [{"message": {"content": content}}]}

    def _install(self, hist_num, ocr_json='{"invoiceNumber":"12345678","invoiceCode":"011002000111","amountWithTax":1130}'):
        calls = self.verify_calls

        def router(url, body, headers, timeout_ms=15000):
            if "llm-ocr" in url:
                return self._ocr_resp(ocr_json)
            if "/verify" in url:
                calls["n"] += 1
                return {"code": 0, "data": {"invoiceStatus": "正常"}}
            if "data/list" in url:
                return {"data": [{"_id": "A", "no": "BX-001",
                                  "sub": [{"_id": "r1", "num": hist_num, "code": "011002000111"}]}]}
            return {}

        inv.http_post_json = router

    def test_llm_ocr_extract_and_duplicate_skips_verify(self):
        self._install("12345678")
        r = inv.main({"imageUrl": "http://x/inv.jpg", "dataId": "CUR"})
        self.assertEqual(r["invoiceNumber"], "12345678", "LLM 抽取发票号码")
        self.assertEqual(r["amountWithTax"], 1130)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "发票重复")
        self.assertEqual(self.verify_calls["n"], 0, "重复票不应触发验真")
        self.assertEqual(r["verifyCount"], 0)

    def test_pass_when_not_duplicate(self):
        self._install("99999999")
        r = inv.main({"imageUrl": "http://x/inv.jpg", "dataId": "CUR"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "验证通过")
        self.assertEqual(r["invoiceNumber"], "12345678")
        self.assertEqual(self.verify_calls["n"], 1)
        self.assertEqual(r["verifyCount"], 1)

    def test_verify_fail(self):
        def router(url, body, headers, timeout_ms=15000):
            if "llm-ocr" in url:
                return self._ocr_resp('{"invoiceNumber":"12345678","invoiceCode":"011002000111"}')
            if "/verify" in url:
                return {"code": 0, "data": {"invoiceStatus": "已作废"}}
            if "data/list" in url:
                return {"data": []}
            return {}
        inv.http_post_json = router
        r = inv.main({"imageUrl": "http://x/inv.jpg", "dataId": "CUR"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "验真失败")
        self.assertEqual(r["verifyCount"], 1)

    def test_llm_ocr_no_number(self):
        # 模型没返回可解析 JSON / 无号码
        inv.http_post_json = lambda url, body, headers, t=5000: self._ocr_resp("这不是一张发票")
        r = inv.main({"imageUrl": "http://x/inv.jpg"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "识别失败")

    def test_dedup_query_error_blocks(self):
        def router(url, body, headers, timeout_ms=15000):
            if "llm-ocr" in url:
                return self._ocr_resp('{"invoiceNumber":"12345678","invoiceCode":"011002000111"}')
            if "data/list" in url:
                raise RuntimeError("网络错误")
            return {}
        inv.http_post_json = router
        r = inv.main({"imageUrl": "http://x/inv.jpg"})
        self.assertFalse(r["ok"])
        self.assertIn("去重查询失败", r["note"])


if __name__ == "__main__":
    unittest.main()
