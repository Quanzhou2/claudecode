# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import invoice_recognize_verify_dedup as inv  # noqa: E402
import voucher_similarity_check as vou  # noqa: E402


class TestNormalize(unittest.TestCase):
    def test_norm_no_strip_and_upper(self):
        self.assertEqual(inv.norm_no("  044 001-9000 123 "), "0440019000123")
        self.assertEqual(inv.norm_no("abc123"), "ABC123")

    def test_norm_no_fullwidth(self):
        self.assertEqual(inv.norm_no("１２３４５"), "12345")

    def test_norm_no_empty(self):
        self.assertEqual(inv.norm_no(None), "")
        self.assertEqual(inv.norm_no(""), "")

    def test_norm_amt(self):
        self.assertEqual(inv.norm_amt("￥1,234.56"), 1234.56)
        self.assertEqual(inv.norm_amt(88.8), 88.8)
        self.assertIsNone(inv.norm_amt(""))
        self.assertIsNone(inv.norm_amt("abc"))

    def test_norm_date(self):
        self.assertEqual(inv.norm_date("20240131"), "2024-01-31")
        self.assertEqual(inv.norm_date("2024年1月3日"), "2024-01-03")
        self.assertEqual(inv.norm_date("2024-1-3"), "2024-01-03")


class TestDedupKey(unittest.TestCase):
    def test_combine_code_number(self):
        self.assertEqual(inv.dedup_key({"invoiceCode": "011002000111", "invoiceNumber": "12345678"}, True),
                         "011002000111:12345678")

    def test_number_only_when_no_code(self):
        self.assertEqual(inv.dedup_key({"invoiceNumber": "23312000000012345678"}, True),
                         "23312000000012345678")

    def test_also_false(self):
        self.assertEqual(inv.dedup_key({"invoiceCode": "011002000111", "invoiceNumber": "12345678"}, False),
                         "12345678")


class TestCheckDuplicate(unittest.TestCase):
    def setUp(self):
        inv.CONFIG["fields"].update({"subform": "sub", "invoiceNumber": "num",
                                     "invoiceCode": "code", "recordNo": "no"})
        inv.CONFIG["dedup"].update({"alsoMatchCode": True, "excludeSelf": True})

    def _records(self, num):
        return [{"_id": "A", "no": "BX-001", "sub": [{"_id": "r1", "num": num, "code": "011002000111"}]}]

    def test_hit(self):
        r = inv.check_duplicate({"invoiceNumber": "12345678", "invoiceCode": "011002000111"},
                                self._records("12345678"), "CUR")
        self.assertTrue(r["duplicate"])
        self.assertEqual(r["matched"]["recordNo"], "BX-001")

    def test_miss(self):
        r = inv.check_duplicate({"invoiceNumber": "99999999", "invoiceCode": "011002000111"},
                                self._records("12345678"), "CUR")
        self.assertFalse(r["duplicate"])

    def test_exclude_self(self):
        recs = [{"_id": "CUR", "no": "BX-CUR", "sub": [{"_id": "r1", "num": "12345678", "code": "011002000111"}]}]
        r = inv.check_duplicate({"invoiceNumber": "12345678", "invoiceCode": "011002000111"}, recs, "CUR")
        self.assertFalse(r["duplicate"], "与自身记录比对不应判重")

    def test_normalized_number_match(self):
        r = inv.check_duplicate({"invoiceNumber": "1234-5678", "invoiceCode": "011002000111"},
                                self._records("12345678"), "CUR")
        self.assertTrue(r["duplicate"])


class TestMapOcr(unittest.TestCase):
    def test_baidu_words_result(self):
        raw = {"words_result": {"InvoiceCode": {"words": "011002000111"},
                                "InvoiceNum": {"words": "1234-5678"},
                                "InvoiceDate": {"words": "2024年01月31日"},
                                "AmountWithTax": {"words": "￥1,130.00"}}}
        m = inv.map_ocr(raw)
        self.assertEqual(m["invoiceCode"], "011002000111")
        self.assertEqual(m["invoiceNumber"], "12345678")
        self.assertEqual(m["invoiceDate"], "2024-01-31")
        self.assertEqual(m["amountWithTax"], 1130.0)

    def test_chinese_keys_data_wrap(self):
        m = inv.map_ocr({"data": {"发票号码": "99887766", "销方税号": "91500", "价税合计": "200"}})
        self.assertEqual(m["invoiceNumber"], "99887766")
        self.assertEqual(m["sellerTaxNo"], "91500")
        self.assertEqual(m["amountWithTax"], 200)


class TestVerifyInterpret(unittest.TestCase):
    def setUp(self):
        vou_noop = None  # placeholder
        inv.CONFIG["verify"]["requireVerify"] = True
        inv.CONFIG["verify"]["endpoint"] = "http://test/verify"

    def _mk(self, resp):
        inv.http_post_json = lambda url, body, headers, t: resp

    def test_normal(self):
        self._mk({"code": 0, "data": {"invoiceStatus": "正常"}})
        r = inv.verify_invoice({"invoiceNumber": "1", "invoiceCode": "", "invoiceDate": "", "checkCode": "", "invoiceAmount": None})
        self.assertTrue(r["authentic"])

    def test_void(self):
        self._mk({"code": 0, "data": {"status": "已作废"}})
        r = inv.verify_invoice({"invoiceNumber": "1", "invoiceCode": "", "invoiceDate": "", "checkCode": "", "invoiceAmount": None})
        self.assertFalse(r["authentic"])


class TestParseJson(unittest.TestCase):
    def test_noisy(self):
        p = vou.parse_json('结果：{"scores":[0.1,0.92],"bestIndex":1,"similarity":0.92,"sameDocument":true,"reason":"一致"} done', 2)
        self.assertEqual(p["similarity"], 0.92)
        self.assertTrue(p["sameDocument"])

    def test_max_of_scores(self):
        p = vou.parse_json('{"scores":[0.3,0.7,0.5]}', 3)
        self.assertEqual(p["similarity"], 0.7)

    def test_unparseable(self):
        p = vou.parse_json("模型抽风", 2)
        self.assertEqual(p["scores"], [0, 0])
        self.assertEqual(p["similarity"], 0)

    def test_clamp(self):
        p = vou.parse_json('{"scores":[1.5,-0.2],"similarity":1.5}', 2)
        self.assertEqual(p["similarity"], 1.0)
        self.assertEqual(p["scores"], [1.0, 0.0])


class TestFileUrls(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(vou.file_urls([{"url": "a"}, {"url": "b"}]), ["a", "b"])
        self.assertEqual(vou.file_urls("x"), ["x"])
        self.assertEqual(vou.file_urls(None), [])


if __name__ == "__main__":
    unittest.main()
