# 简道云后端函数 · Python 版

与 `dist/jdy-paste/` 的 JS 版功能完全一致，改用 **Python** 实现，便于粘进简道云
「后端函数（Python）」。逻辑同样是：发票 **先查重、再验真**；付款凭证 **OpenAI 兼容 LLM**
图片相似度判重。

| 文件 | 函数 | 入口 |
| --- | --- | --- |
| `invoice_recognize_verify_dedup.py` | 发票识别 · 去重 · 验真 | `main(params, context)` |
| `voucher_similarity_check.py` | 付款凭证相似度查重 | `main(params, context)` |

## 安装

1. 简道云 → 插件管理 → **新建自建插件** → **新建函数**（类型：后端函数，语言选 **Python**）。
2. 把对应 `.py` 文件整段粘进代码框（入口是 `main`）。**不要**打包成 zip 导入——简道云
   「导入插件」只认它自己导出的插件包，手工 zip 会判为损坏。
3. 改文件顶部 `CONFIG`：把 `FILL_*` 换成真实的表单字段 widget id、服务地址与密钥。
   （密钥更推荐走插件「身份验证/通用参数」，再在 `CONFIG` 或 `main` 里读对应入参。）
4. 按下表在函数里声明入参/出参（也见 `../manifest/plugin.manifest.json`、
   `../examples/io-samples.json`）。
5. 点「插件调试」跑通，再接表单「前端事件」，并在「表单提交校验」里加：
   子表单「状态」≠「验证通过」时禁止提交。

### 入参 / 出参

- `invoice_recognize_verify_dedup`
  - 入参：`imageUrl`(必填)、`dataId`、`rowId`、`priorVerifyCount`
  - 出参：`ok, status, invoiceType, invoiceCode, invoiceNumber, invoiceDate,
    invoiceAmount, taxAmount, amountWithTax, checkCode, sellerTaxNo, verifyCount,
    note, duplicate, matchedRecord`
- `voucher_similarity_check`
  - 入参：`imageUrl`(必填)、`dataId`、`rowId`
  - 出参：`ok, status, duplicate, similarity, threshold, note, matchedRecord`

## 运行时假设

- 入口 `main(params, context)`；`params` 为入参 dict。若你的入口签名不同，改 `main` 一行即可。
- HTTP 用 `requests`（简道云 Python 环境通常自带；无则自动回退到标准库 `urllib`）。
- 全部逻辑自包含在单文件内：无第三方本地依赖、无需 `pip install`。

## 本地测试

```bash
python3 -m unittest discover -s python/tests -p 'test_*.py'
```

测试用 mock 替换 HTTP，离线运行，覆盖：归一化、发票去重（含数电票、排除自身、归一命中）、
OCR 多结构映射、验真结论、LLM 输出解析容错、两个函数端到端分支（识别失败 / 验真失败 /
重复 / 通过 / 查询异常）。共 31 个用例。
