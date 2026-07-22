# 简道云粘贴用「方法体」代码

这两个 `.js` 是**直接粘贴进简道云自建插件函数代码框**的方法体，不是 npm 模块。

## 为什么不是导入 zip？

简道云「导入插件」只接受它自己**导出**的插件包（固定内部结构 + 校验）。把仓库或这些文件
打成 zip 手工导入会被判为“损坏/无效”。正确做法是在界面里新建插件、新建函数、粘贴下面的代码。
插件建好后再用简道云自带「导出」，就能得到可再分发/再导入的合法 zip。

## 安装

1. 简道云 → 开放平台 → 插件管理 → **新建自建插件** → **新建函数**（类型：后端函数）。
2. 把对应文件里 `======= 方法体开始 =======` 到 `======= 方法体结束 =======` 的整段代码，
   粘进函数代码框。
3. 改文件顶部 `CONFIG`：把 `FILL_*` 换成真实的表单字段 widget id、服务地址与密钥。
4. 按下表在函数里声明入参/出参（也见 `../../manifest/plugin.manifest.json`、
   `../../examples/io-samples.json`）。
5. 点「插件调试」跑通后，接到表单「前端事件」，并在「表单提交校验」里加：
   子表单「状态」≠「验证通过」时禁止提交。

| 文件 | 函数 | 入参 | 主要出参 |
| --- | --- | --- | --- |
| `invoiceRecognizeVerifyDedup.js` | 发票识别·去重·验真 | `imageUrl, dataId, rowId, priorVerifyCount` | `ok, status, invoiceNumber, …, note, duplicate` |
| `voucherSimilarityCheck.js` | 凭证相似度查重 | `imageUrl, dataId, rowId` | `ok, status, duplicate, similarity, note` |

## 运行时假设

- 代码是 **async 方法体**，入参通过 `params` 对象拿到（若你的入口参数名不同，改顶部
  `readParams()` 一行）。
- HTTP 用运行时的 `fetch`（Node18+）或 `axios`（后端函数环境通常自带）。
- 逻辑全部自包含：无 `require` 本地文件、无 `module.exports`。
