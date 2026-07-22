# 最简版：费用报销单查重（发票 + 付款凭证）

两个独立的简道云「后端函数（Python）」，各只做一件事，上传文件时返回是否重复。

| 文件 | 查什么 | 怎么查 | 返回 |
| --- | --- | --- | --- |
| `invoice_dup_check.py` | 发票 | LLM 读发票号码 → 和历史号码比 | `duplicated`, `invoiceNumber`, `message` |
| `payment_dup_check.py` | 付款凭证 | LLM 图片相似度 → 超阈值判重 | `duplicated`, `similarity`, `message` |

## 装法（3 步）

1. **建函数**：简道云 → 插件管理 → 新建自建插件 → 新建函数（后端函数 / Python），
   把对应 `.py` 整段粘进去（入口 `main`）。
2. **填 `CONF`**：把文件顶部的 `FILL_*` 换成真实值——
   - `llm_key`：小米 MiMo 密钥（platform.xiaomimimo.com 申请）。默认已配好
     `llm_url=https://api.xiaomimimo.com/v1/chat/completions`、`llm_model=mimo-v2.5-pro`
     （OpenAI 兼容）。**两个函数都要把图片发给模型，务必用能看图的多模态 MiMo 版本；
     若 `mimo-v2.5-pro` 不支持图片输入，改成多模态版如 `mimo-v2-omni`。**
   - `jdy_key`：简道云 API 密钥（需有本表单读取权限）；`app_id`/`entry_id` 已按你的表单预填
   - `sub_widget`：发票信息子表单的 widget id
   - `number_widget`（发票）/ `attach_widget`（凭证）：子表单里对应字段的 widget id
3. **接前端事件 + 提交校验**：
   - 前端事件：触发字段 = 子表单「发票」（或「附件」）值改变 → 调用对应函数，
     `imageUrl` 绑该字段文件地址、`dataId` 绑当前记录 id；把返回的 `duplicated`/`message`
     回填到子表单一个文本字段（如「查重结果」）。
   - 表单提交校验：当「查重结果」= 重复 时禁止提交。

## 入参 / 出参

- 入参：`imageUrl`(必填)、`dataId`(选填，编辑时排除自身)
- 出参：`duplicated`（true/false，是否重复，**核心**）、`message`（说明）、
  发票另有 `invoiceNumber`，凭证另有 `similarity`

## 说明

- 只做查重，不做发票验真、不按流程状态过滤——和**所有**历史记录比，避免漏判。
- 单文件自包含，只依赖 `requests`（无则回退标准库 `urllib`），无需 `pip install`。
- 阈值在 `payment_dup_check.py` 的 `CONF["threshold"]`（默认 0.9），比对张数上限 `max_compare`。

## 排错

- **404 报错**：多半是「模型名不对/账号没这个模型」，而不是地址错。函数已把服务器返回体带出来，
  看报错里的 message 即可（如 `model not found`）。到 platform.xiaomimimo.com 控制台核对你有权限
  的模型名（可能是 `mimo-v2.5`、`mimo-v2-omni` 等），改 `CONF["llm_model"]`。
- **鉴权**：MiMo 用 `api-key` 请求头（函数已同时带 `api-key` 和 `Authorization: Bearer`）。
- **先单测模型**：接进简道云前，先用一张图跑通模型（下方 curl），确认地址/密钥/模型名/看图能力都 OK。

```bash
curl -sS -X POST https://api.xiaomimimo.com/v1/chat/completions \
  -H "api-key: $MIMO_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"mimo-v2.5-pro","messages":[{"role":"user","content":[
       {"type":"text","text":"这张图里是什么？"},
       {"type":"image_url","image_url":{"url":"https://<一张可访问的图片>"}}]}]}'
```

若 404 提示模型不存在，就换控制台里列出的模型名；若返回说看不了图，就换**多模态**版本。

> 需要发票验真、流程状态过滤、感知哈希预筛、JS 版等更完整能力，见仓库上层的完整实现。
