'use strict';
/* 自动生成，请勿手改。源码见 src/frontend/attachmentFrontend.js。构建：npm run build */
var __modules = {};
var __cache = {};
function __load(id){
  if (__cache[id]) return __cache[id].exports;
  var module = { exports: {} };
  __cache[id] = module;
  __modules[id].call(module.exports, module, module.exports, __mkReq(id));
  return module.exports;
}
function __mkReq(fromId){
  var base = fromId.split('/').slice(0, -1);
  return function(spec){
    if (spec.charAt(0) !== '.') return require(spec);
    var parts = base.slice();
    spec.split('/').forEach(function(seg){
      if (seg === '.' || seg === '') return;
      if (seg === '..') parts.pop(); else parts.push(seg);
    });
    var id = parts.join('/');
    if (!__modules[id] && __modules[id + '.js']) id = id + '.js';
    if (!__modules[id] && __modules[id + '/index.js']) id = id + '/index.js';
    return __load(id);
  };
}
__modules["src/frontend/attachmentFrontend.js"] = function(module, exports, require){
'use strict';

/**
 * 前端扩展：付款凭证附件字段变化时的处理。
 * api / cfg 约定同 invoiceFrontend.js。
 */

const { firstUrl } = require('./invoiceFrontend');

/**
 * @param {import('./invoiceFrontend').FrontApi} api
 * @param {object} cfg
 */
function createVoucherHandler(api, cfg) {
  const roles = cfg.subform.fields;

  /**
   * 附件（付款凭证）上传/变化时触发。
   * @param {object} evt { rowId, value }
   */
  return async function onVoucherChange(evt) {
    const rowId = evt.rowId;
    const imageUrl = firstUrl(evt.value) || api.getRowValue(rowId, 'voucherAttachment');
    if (!imageUrl) return;

    api.toast('正在比对付款凭证是否重复…', 'loading');

    let ret;
    try {
      ret = await api.invoke('voucherSimilarityCheck', {
        imageUrl,
        rowId,
        dataId: api.currentDataId ? api.currentDataId() : undefined,
      });
    } catch (e) {
      api.toast(`凭证查重失败：${e.message}`, 'error');
      api.setRowValues(rowId, {
        [roles.status.widget]: cfg.statusValues.ocrFailed,
        [roles.recognizeNote.widget]: String(e.message),
      });
      return;
    }

    // 把查重结论写入状态/说明（供提交校验拦截）
    api.setRowValues(rowId, {
      [roles.status.widget]: ret.duplicate ? cfg.statusValues.duplicateVoucher : ret.status,
      [roles.recognizeNote.widget]: ret.note,
    });

    if (ret.ok) {
      api.toast('付款凭证未发现重复', 'success');
    } else {
      api.toast(ret.note || '付款凭证疑似重复，无法提交', 'error');
    }
  };
}

module.exports = { createVoucherHandler };

};
__modules["src/frontend/invoiceFrontend.js"] = function(module, exports, require){
'use strict';

/**
 * 前端扩展：发票字段变化时的处理。
 *
 * 说明：简道云不同版本前端扩展的上下文 API 名称可能不同，这里把与平台耦合的能力
 * 抽象成一个 `api` 适配对象（见下方 JSDoc）。部署时按你所在版本的前端事件上下文
 * 实现这几个方法即可（大多可直接映射到「前端事件」提供的入参/回调）。
 *
 * @typedef {Object} FrontApi
 * @property {(rowId:string, role:string)=>any}       getRowValue  取子表单当前行某 role 字段值
 * @property {(rowId:string, patch:object)=>void}     setRowValues 按 role 批量回填当前行字段
 * @property {(fnKey:string, params:object)=>Promise} invoke       调用后端函数（返回其 returnParams）
 * @property {(msg:string, type?:string)=>void}       toast        顶部消息提示
 * @property {()=>string}                             currentDataId 当前记录 dataId（编辑态）
 */

/**
 * 生成发票字段变化处理器。
 * @param {FrontApi} api
 * @param {object} cfg 已解析配置（getConfig 的结果或其精简子集）
 */
function createInvoiceHandler(api, cfg) {
  const roles = cfg.subform.fields;

  /**
   * 发票图片上传/变化时触发。
   * @param {object} evt { rowId, value } value 为图片字段值（数组或 url）
   */
  return async function onInvoiceImageChange(evt) {
    const rowId = evt.rowId;
    const imageUrl = firstUrl(evt.value) || api.getRowValue(rowId, 'invoiceImage');
    if (!imageUrl) return;

    api.toast('正在识别并查验发票…', 'loading');

    const priorVerifyCount = Number(api.getRowValue(rowId, 'verifyCount')) || 0;
    let ret;
    try {
      ret = await api.invoke('invoiceRecognizeVerifyDedup', {
        imageUrl,
        rowId,
        dataId: api.currentDataId ? api.currentDataId() : undefined,
        priorVerifyCount,
      });
    } catch (e) {
      api.toast(`发票处理失败：${e.message}`, 'error');
      api.setRowValues(rowId, { [roles.status.widget]: cfg.statusValues.ocrFailed, [roles.recognizeNote.widget]: String(e.message) });
      return;
    }

    // 回填识别结果（若已在「字段存储关系」里配置回填，可省略此步）
    api.setRowValues(rowId, {
      [roles.invoiceType.widget]: ret.invoiceType,
      [roles.invoiceCode.widget]: ret.invoiceCode,
      [roles.invoiceNumber.widget]: ret.invoiceNumber,
      [roles.invoiceDate.widget]: ret.invoiceDate,
      [roles.invoiceAmount.widget]: ret.invoiceAmount,
      [roles.taxAmount.widget]: ret.taxAmount,
      [roles.amountWithTax.widget]: ret.amountWithTax,
      [roles.checkCode.widget]: ret.checkCode,
      [roles.sellerTaxNo.widget]: ret.sellerTaxNo,
      [roles.verifyCount.widget]: ret.verifyCount,
      [roles.status.widget]: ret.status,
      [roles.recognizeNote.widget]: ret.note,
    });

    if (ret.ok) {
      api.toast('发票验真通过，未发现重复', 'success');
    } else {
      // 失败提示（阻止提交由「表单提交校验」保证：状态需为验证通过）
      api.toast(ret.note || '发票校验未通过，无法提交', 'error');
    }
  };
}

function firstUrl(value) {
  if (!value) return '';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    const item = value[0];
    if (!item) return '';
    return typeof item === 'string' ? item : item.url || '';
  }
  if (typeof value === 'object' && value.url) return value.url;
  return '';
}

module.exports = { createInvoiceHandler, firstUrl };

};
__load("src/shared/config.js").setEmbeddedConfig({
  "$comment": "简道云自建插件运行配置。部署时把 <FILL_*> 占位符替换为真实值。字段 role -> _widget_ 的映射既可以在此处填写（供后端函数按 role 读写），也可以直接在简道云「前端事件 >> 字段存储关系」里配置回填。",

  "dataset": {
    "$comment": "费用报销单本身所在的应用/表单，用于查询历史已报销记录做去重。app_id / entry_id 已从上传的表单地址中解析。",
    "appId": "68ca0e2fb59e070714b68aa0",
    "entryId": "6899902c9582f683ab885f8d",
    "apiBase": "https://api.jiandaoyun.com/api",
    "apiVersion": "v5",
    "apiKeyEnv": "JDY_API_KEY"
  },

  "main": {
    "$comment": "主表（费用报销单本身）字段映射，供去重结果里回显命中的历史报销单。",
    "fields": {
      "recordNo":    { "label": "报销单编号", "widget": "FILL_报销单编号_widget", "type": "text" },
      "flowStatus":  { "label": "流程状态",   "widget": "FILL_流程状态_widget",   "type": "text" }
    }
  },

  "subform": {
    "$comment": "发票信息子表单（表单里名为「票据录入」）的字段 role -> widget 映射。",
    "widget": "FILL_票据录入_widget",
    "fields": {
      "invoiceImage":   { "label": "发票",     "widget": "FILL_发票_widget",     "type": "image" },
      "invoiceType":    { "label": "发票类型", "widget": "FILL_发票类型_widget", "type": "text" },
      "invoiceCode":    { "label": "发票代码", "widget": "FILL_发票代码_widget", "type": "text" },
      "invoiceNumber":  { "label": "发票号码", "widget": "FILL_发票号码_widget", "type": "text" },
      "invoiceDate":    { "label": "票据日期", "widget": "FILL_票据日期_widget", "type": "text" },
      "invoiceAmount":  { "label": "发票金额", "widget": "FILL_发票金额_widget", "type": "number" },
      "taxAmount":      { "label": "税额",     "widget": "FILL_税额_widget",     "type": "number" },
      "amountWithTax":  { "label": "价税合计", "widget": "FILL_价税合计_widget", "type": "number" },
      "checkCode":      { "label": "校验码",   "widget": "FILL_校验码_widget",   "type": "text" },
      "sellerTaxNo":    { "label": "销方税号", "widget": "FILL_销方税号_widget", "type": "text" },
      "status":         { "label": "状态",     "widget": "FILL_状态_widget",     "type": "text" },
      "verifyCount":    { "label": "查验次数", "widget": "FILL_查验次数_widget", "type": "number" },
      "recognizeNote":  { "label": "识别说明", "widget": "FILL_识别说明_widget", "type": "text" },
      "voucherAttachment": { "label": "附件",  "widget": "FILL_附件_widget",     "type": "upload" }
    }
  },

  "statusValues": {
    "$comment": "写回「状态」字段的枚举值。表单提交校验只放行 verified。",
    "pending": "待验证",
    "verified": "验证通过",
    "duplicateInvoice": "发票重复",
    "verifyFailed": "验真失败",
    "duplicateVoucher": "凭证重复",
    "ocrFailed": "识别失败"
  },

  "invoice": {
    "ocr": {
      "$comment": "发票 OCR 识别提供方（参考重庆猫猫智能科技有限公司发票识别插件的识别方式，做成可插拔适配器）。provider 取值：maomao | baidu | tencent | huawei | custom。",
      "provider": "maomao",
      "endpointEnv": "INVOICE_OCR_ENDPOINT",
      "appKeyEnv": "INVOICE_OCR_APP_KEY",
      "appSecretEnv": "INVOICE_OCR_APP_SECRET",
      "timeoutMs": 15000
    },
    "verify": {
      "$comment": "发票查验（验真）提供方，校验发票号码真伪与状态。provider：maomao | nuonuo | baiwang | custom。",
      "provider": "maomao",
      "endpointEnv": "INVOICE_VERIFY_ENDPOINT",
      "appKeyEnv": "INVOICE_VERIFY_APP_KEY",
      "appSecretEnv": "INVOICE_VERIFY_APP_SECRET",
      "timeoutMs": 15000,
      "requireVerify": true
    },
    "dedup": {
      "$comment": "去重范围：只与「已报销/审批通过」的历史记录比对。statusFilter 为流程状态字段与放行值；scanLimit 为最多扫描的历史记录数。",
      "statusField": "FILL_流程状态_widget",
      "statusIncludes": ["已完成", "审批通过", "已报销"],
      "scanLimit": 5000,
      "pageSize": 100,
      "matchOn": ["invoiceNumber"],
      "alsoMatchCode": true
    }
  },

  "voucher": {
    "similarity": {
      "$comment": "付款凭证图片相似度分析（LLM 多模态，OpenAI 兼容接口）。provider：openai-compatible | custom。endpoint 指向兼容 /chat/completions 的服务，model 用其提供的视觉模型名。threshold 超过则判重。",
      "provider": "openai-compatible",
      "endpointEnv": "LLM_SIMILARITY_ENDPOINT",
      "apiKeyEnv": "LLM_SIMILARITY_API_KEY",
      "model": "gpt-4o",
      "threshold": 0.9,
      "timeoutMs": 30000,
      "maxCandidates": 60,
      "prefilter": {
        "$comment": "感知哈希（dHash）预筛，先用汉明距离粗筛，减少送入 LLM 的候选数量。启用需运行环境能解码图片为灰度网格。",
        "enabled": true,
        "hammingMaxDistance": 12,
        "topK": 8
      }
    }
  },

  "runtime": {
    "$comment": "现场记录编辑时，需排除当前记录自身，避免与自己比对。dataIdParam 为前端事件传入当前 dataId 的请求参数名。",
    "excludeSelf": true,
    "logLevel": "info"
  }
}
);
var __entry = __load("src/frontend/attachmentFrontend.js");
module.exports = __entry;
