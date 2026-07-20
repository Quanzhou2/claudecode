# 费用报销单 · 发票/凭证防重插件（简道云自建插件）

针对简道云「**费用报销单**」表单开发的自建插件，实现两项防重能力：

1. **发票识别 · 验真 · 去重**：在「票据录入」子表单的 **发票** 图片字段上传发票时，自动
   OCR 识别发票信息、查验发票真伪，并与历史**已报销**记录中的发票号码去重；识别/验真/去重
   任一不通过则阻止提交并给出失败提示。
2. **付款凭证图片相似度去重**：在「票据录入」子表单的 **附件** 字段上传付款凭证时，用多模态
   大模型（LLM）把该图片与历史**已报销**记录同字段的图片做相似度分析，超过阈值判为重复，
   阻止提交。

> 发票的识别与验真方式参考「**重庆猫猫智能科技有限公司**」的发票识别插件（OCR 提取要素 + 税务
> 通道验真）；**去重逻辑与相似度判重逻辑为本插件自研**（见 `src/invoice/dedup.js`、
> `src/similarity/`）。

---

## 一、目标表单结构

费用报销单主表 + 「票据录入」子表单（发票信息）。子表单关键字段：

| 角色 role | 字段名 | 用途 |
| --- | --- | --- |
| `invoiceImage` | 发票 | 上传发票图片，**功能一的触发字段** |
| `invoiceType` | 发票类型 | 识别回填 |
| `invoiceCode` | 发票代码 | 识别回填 + 去重键之一 |
| `invoiceNumber` | 发票号码 | 识别回填 + **去重主键** |
| `invoiceDate` | 票据日期 | 识别回填 |
| `invoiceAmount` | 发票金额 | 识别回填 |
| `taxAmount` | 税额 | 识别回填 |
| `amountWithTax` | 价税合计 | 识别回填 |
| `checkCode` | 校验码 | 识别回填 + 验真要素 |
| `sellerTaxNo` | 销方税号 | 识别回填 |
| `status` | 状态 | 写入校验结论（提交校验依据） |
| `verifyCount` | 查验次数 | 累计查验次数 |
| `recognizeNote` | 识别说明 | 写入识别/验真/去重说明 |
| `voucherAttachment` | 附件 | 上传付款凭证，**功能二的触发字段** |

主表另需 `报销单编号`（命中回显）与 `流程状态`（去重只比对已报销记录）。

---

## 二、整体架构

```
                        简道云「费用报销单」表单
   ┌──────────────────────────────────────────────────────────────┐
   │  子表单：票据录入                                              │
   │   发票(图片) ──前端事件──▶ 后端函数 invoiceRecognizeVerifyDedup │
   │                              1) OCR 识别   （猫猫式适配器）     │
   │                              2) 发票验真   （税务通道适配器）   │
   │                              3) 去重       （自研，查历史已报销）│
   │                              └─▶ 回填字段 + 写「状态/识别说明」 │
   │                                                                │
   │   附件(凭证) ─前端事件──▶ 后端函数 voucherSimilarityCheck       │
   │                              1) 取历史同字段图片（已报销）      │
   │                              2) 感知哈希粗筛（可选）            │
   │                              3) LLM 多模态相似度分析            │
   │                              4) 超阈值判重（自研）              │
   │                              └─▶ 写「状态/识别说明」            │
   └──────────────────────────────────────────────────────────────┘
                         │
                         ▼
   表单提交校验：子表单每行「状态」必须 = 「验证通过」，否则拦截提交
```

- **后端函数**（Node.js）承担识别、验真、查历史、LLM 调用等需要密钥与网络的重活；
- **前端扩展**负责在上传时触发、回填、弹出提示；
- **表单提交校验**是最终的提交闸门（即使绕过前端，状态不为「验证通过」也无法提交）。

---

## 三、目录结构

```
jiandaoyun-reimbursement-plugin/
├── plugin.config.json          # 运行配置：字段映射 / 阈值 / 服务商 / 去重范围
├── manifest/plugin.manifest.json  # 插件与函数声明（请求参数 / 返回参数）
├── src/
│  ├── shared/                  # 通用：配置、日志、HTTP、简道云数据接口、记录抽取、归一化
│  ├── invoice/                 # 功能一：ocrClient / verifyClient / dedup(自研) / invoiceBackend
│  ├── similarity/              # 功能二：imageHash / similarity(自研) / llmSimilarityClient / similarityBackend
│  └── frontend/                # 前端扩展 + 提交校验判定
├── dist/                       # 打包后的单文件（可直接粘贴进简道云代码框）
├── scripts/build-bundles.js    # 打包脚本
└── test/                       # 单元 + 集成测试（node:test，无需联网）
```

---

## 四、部署步骤

### 1. 准备第三方服务

| 服务 | 用途 | 需要的密钥/地址（写入插件环境变量） |
| --- | --- | --- |
| 发票 OCR 识别 | 提取发票要素 | `INVOICE_OCR_ENDPOINT` / `INVOICE_OCR_APP_KEY` / `INVOICE_OCR_APP_SECRET` |
| 发票验真 | 查验真伪 | `INVOICE_VERIFY_ENDPOINT` / `INVOICE_VERIFY_APP_KEY` / `INVOICE_VERIFY_APP_SECRET` |
| 多模态 LLM | 图片相似度 | `LLM_SIMILARITY_ENDPOINT` / `LLM_SIMILARITY_API_KEY` |
| 简道云数据接口 | 查询历史记录去重 | `JDY_API_KEY`（开发者后台生成的 API 密钥） |

> OCR/验真也可直接复用简道云插件中心已有的「发票识别插件 / 发票验真插件」（猫猫等厂商），
> 此时把 `provider` 设为对应厂商，并按其文档调整 `ocrClient.buildRequest` / `mapOcrResult`
> 的请求体与字段映射即可——映射层已兼容百度/腾讯/华为/通用中文键等多种返回结构。

### 2. 新建自建插件并导入函数

在「开放平台 → 插件管理 → 新建自建插件」中创建 4 个函数（声明见
`manifest/plugin.manifest.json`）：

| 函数 key | 类型 | 代码来源 |
| --- | --- | --- |
| `invoiceRecognizeVerifyDedup` | 后端函数 | 粘贴 `dist/invoiceBackend.bundle.js` |
| `voucherSimilarityCheck` | 后端函数 | 粘贴 `dist/similarityBackend.bundle.js` |
| `invoiceGuardFrontend` | 前端扩展 | 粘贴 `dist/invoiceFrontend.bundle.js` |
| `voucherGuardFrontend` | 前端扩展 | 粘贴 `dist/attachmentFrontend.bundle.js` |

按 manifest 里的 `requestParams` / `returnParams` 逐项填写每个函数的**请求参数声明**与
**返回参数声明**。

### 3. 填写字段映射

编辑 `plugin.config.json`，把所有 `FILL_*` 占位符替换为真实的 `_widget_` 字段 ID：

- 字段 ID 获取：表单设计里选中字段可见字段标识；或用简道云「表单字段查询」接口列出所有
  widget；数据接口返回的 JSON key 即为字段 ID。
- `dataset.appId` / `dataset.entryId` 已按上传的表单地址预填
  （app `68ca0e2f...`，form `6899902c...`），请与实际应用核对。

> 打包脚本会把 `plugin.config.json` 内联进 `dist/*.bundle.js`。**改完配置后需重新
> `npm run build` 并重新粘贴后端函数代码**（或在简道云插件环境变量里维护同名配置）。

### 4. 配置前端事件（触发 + 回填）

在「表单设计 → 表单属性 → 前端事件」新增两条：

1. **发票识别**：触发字段 = 子表单 `发票`（值改变）；执行动作 = 调用插件
   `invoiceRecognizeVerifyDedup`；请求参数 `imageUrl` 绑定该行 `发票` 的文件地址，
   `dataId` 绑定当前记录 ID；在**字段存储关系**里把返回的 `invoiceCode/invoiceNumber/...
   /status/note/verifyCount` 回填到子表单对应字段。
2. **凭证查重**：触发字段 = 子表单 `附件`（值改变）；执行动作 = 调用插件
   `voucherSimilarityCheck`；`imageUrl` 绑定 `附件` 文件地址；把返回的 `status/note`
   回填到 `状态/识别说明`。

> 简道云限制：前端事件里插件**不能把图片直接写回图片字段**，只能回填文本/数字字段——本
> 插件回填的都是文本/数字，符合限制。

### 5. 配置表单提交校验（提交闸门）

「表单属性 → 表单提交校验 → 添加校验条件」：

> 当 子表单「状态」 ≠ `验证通过` 时，禁止提交，提示「存在未通过校验的发票/凭证，请检查」。

这样即便前端提示被忽略，只要有一行状态不是「验证通过」（发票重复 / 验真失败 / 凭证重复 /
识别失败），就无法提交。前端扩展 `src/frontend/submitValidation.js` 提供了等价的纯函数判定
`validateSubmission()`，可在自定义提交校验里直接调用。

---

## 五、功能一：发票识别 · 验真 · 去重

后端函数 `invoiceRecognizeVerifyDedup`（`src/invoice/invoiceBackend.js`）流程：

1. **OCR 识别**（`ocrClient.js`）：把发票图片 URL 交给识别服务，映射为标准字段
   （发票代码/号码/日期/金额/税额/价税合计/校验码/销方税号）。识别不到号码 → `识别失败`。
2. **验真**（`verifyClient.js`）：以发票号码等要素查验真伪与状态（正常/作废/红冲/查无）。
   `requireVerify=true` 且非真 → `验真失败`。
3. **去重（自研，`dedup.js`）**：
   - 调用简道云数据接口，分页拉取**已报销/审批通过**的历史报销单（`invoice.dedup.statusIncludes`
     控制范围，`scanLimit` 控制扫描上限）；
   - 抽取所有子表单行的发票号码，以「发票代码 + 发票号码」归一化后的 key 建索引；
   - 待校验发票命中索引即判重（编辑时通过 `dataId` 排除自身）；数电票无代码时按号码回退匹配。
   - 命中 → `发票重复`，并回显命中的历史报销单编号。
4. 全部通过 → `验证通过`，`ok=true`。

返回值同时回填子表单字段与 `状态/识别说明/查验次数`。

---

## 六、功能二：付款凭证图片相似度去重

后端函数 `voucherSimilarityCheck`（`src/similarity/similarityBackend.js`）流程：

1. 拉取本次上传凭证图片；
2. 查询历史**已报销**记录中同一 `附件` 字段的图片（排除当前记录自身）；
3. **感知哈希粗筛（可选，`imageHash.js`）**：用 dHash + 汉明距离先粗筛，减少送入 LLM 的候选，
   降低调用成本。需运行环境具备图片解码能力（如 `sharp`/`jimp`），在后端函数入口注入
   `decodeToGrayGrid` 后启用；未启用时按 `maxCandidates` 截断后全部送 LLM。
4. **LLM 相似度分析（`llmSimilarityClient.js`）**：一次把「上传图 + 一批历史图」发给多模态
   模型，返回每张的相似度分值；提示词要求把翻拍/重扫/截图/裁剪/调色都视为同一张，仅版式相同
   但内容不同不算重复。分批比对，命中阈值即停。
5. **判重（自研，`similarity.js`）**：取最高相似度，`≥ threshold`（默认 0.9）判为
   `凭证重复`，回显命中的历史报销单。

---

## 七、配置项速查（`plugin.config.json`）

| 路径 | 含义 | 默认 |
| --- | --- | --- |
| `invoice.ocr.provider` | OCR 服务商适配 | `maomao` |
| `invoice.verify.requireVerify` | 是否强制验真 | `true` |
| `invoice.dedup.statusIncludes` | 参与去重的流程状态 | `["已完成","审批通过","已报销"]` |
| `invoice.dedup.scanLimit` | 去重最多扫描历史条数 | `5000` |
| `invoice.dedup.alsoMatchCode` | 是否把发票代码纳入去重键 | `true` |
| `voucher.similarity.provider` | LLM 服务商 | `claude` |
| `voucher.similarity.model` | 模型 | `claude-opus-4-8` |
| `voucher.similarity.threshold` | 判重阈值 | `0.9` |
| `voucher.similarity.maxCandidates` | 最多比对历史图片数 | `60` |
| `voucher.similarity.prefilter.enabled` | 感知哈希粗筛 | `true`（需解码器） |
| `runtime.excludeSelf` | 编辑时排除自身记录 | `true` |

---

## 八、开发与测试

```bash
npm test        # 运行单元 + 集成测试（node:test，全部离线，无需联网/密钥）
npm run build   # 生成 dist/*.bundle.js（把配置内联，供粘贴进简道云）
```

测试覆盖：号码/金额/日期归一化、发票去重（含数电票、排除自身、归一命中）、OCR 多结构映射、
验真结论解释、感知哈希与汉明距离、相似度粗筛与判重、LLM 输出解析容错、两个后端函数的端到端
分支（识别失败/验真失败/重复/通过/查询异常）、提交校验判定。当前 55 个用例全部通过。

---

## 九、注意事项

- OCR / 验真 / LLM 均为付费能力（简道云插件或第三方按次计费），生产前请评估用量。
- 各识别/验真厂商的请求体与返回字段不同，`ocrClient`/`verifyClient` 已做多结构兼容，接入
  具体厂商时按其文档核对映射即可。
- 密钥务必放在插件的环境变量/密钥配置中，不要硬编码进代码或提交到仓库。
- 去重与相似度都依赖简道云数据接口拉取历史记录，请确保 `JDY_API_KEY` 有该表单的读取权限，
  且历史数据量大时合理设置 `scanLimit` 与 `maxCandidates`。

## 十、参考

- 简道云开放平台 · 自建插件 / 插件设计 / 开发指南（`hc.jiandaoyun.com/open/16639`、`/16641`、`/11261`）
- 简道云 · 前端事件（`hc.jiandaoyun.com/doc/11825`）、表单提交校验（`/doc/9039`）
- 简道云 · 发票识别插件 / 发票验真插件（`hc.jiandaoyun.com/open/14680`、`/15619`）
- 简道云数据接口 · 查询多条数据（v5）
