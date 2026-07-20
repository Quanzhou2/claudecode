'use strict';
/* 自动生成，请勿手改。源码见 src/invoice/invoiceBackend.js。构建：npm run build */
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
__modules["src/invoice/invoiceBackend.js"] = function(module, exports, require){
'use strict';

/**
 * 后端函数：发票识别 · 验真 · 去重。
 * 简道云自建插件「后端函数」入口：module.exports = async function(params, context)
 *
 * 流程：
 *   1) OCR 识别发票图片，提取发票号码等要素；
 *   2) 发票真伪查验（验真）；
 *   3) 与历史「已报销/审批通过」记录中的发票号码去重；
 *   4) 返回可回填字段 + 状态 + 说明；任一环节失败则 ok=false，供表单提交校验拦截。
 */

const { getConfig } = require('../shared/config');
const { createHttpClient } = require('../shared/httpClient');
const { createLogger } = require('../shared/logger');
const { createOcrClient } = require('./ocrClient');
const { createVerifyClient } = require('./verifyClient');
const { dedupInvoice } = require('./dedup');
const {
  createJdyDataClient,
  buildStatusFilter,
} = require('../shared/jdyDataClient');
const { collectInvoiceEntries } = require('../shared/records');

/**
 * 核心逻辑（依赖注入，便于测试）。
 * @param {object} params { imageUrl, dataId, rowId, priorVerifyCount }
 * @param {object} deps { cfg, ocr, verify, dataClient, logger }
 */
async function runInvoiceGuard(params, deps) {
  const { cfg, ocr, verify, dataClient, logger } = deps;
  const S = cfg.statusValues;
  const priorCount = Number(params.priorVerifyCount) || 0;

  const base = {
    ok: false,
    status: S.pending,
    invoiceType: '',
    invoiceCode: '',
    invoiceNumber: '',
    invoiceDate: '',
    invoiceAmount: null,
    taxAmount: null,
    amountWithTax: null,
    checkCode: '',
    sellerTaxNo: '',
    verifyCount: priorCount,
    note: '',
    duplicate: false,
    matchedRecord: null,
  };

  // 1) OCR 识别
  let inv;
  try {
    inv = await ocr.recognize(params.imageUrl);
  } catch (e) {
    logger.error('OCR 失败', e.message);
    return { ...base, status: S.ocrFailed, note: `发票识别失败：${e.message}` };
  }
  if (!inv.recognized || !inv.invoiceNumber) {
    return {
      ...base,
      status: S.ocrFailed,
      note: '未能识别到有效发票号码，请上传清晰的发票图片',
    };
  }

  const filled = {
    ...base,
    invoiceType: inv.invoiceType,
    invoiceCode: inv.invoiceCode,
    invoiceNumber: inv.invoiceNumber,
    invoiceDate: inv.invoiceDate,
    invoiceAmount: inv.invoiceAmount,
    taxAmount: inv.taxAmount,
    amountWithTax: inv.amountWithTax,
    checkCode: inv.checkCode,
    sellerTaxNo: inv.sellerTaxNo,
    // 查验次数在真正调用验真时才 +1；重复票不进入验真，故此处先保持原值
    verifyCount: priorCount,
  };

  // 2) 去重（先查重：命中即拦截，省去后续验真调用）
  let historyEntries = [];
  try {
    const dedupCfg = cfg.invoice.dedup;
    const subformWidget = cfg.subform.widget;
    const numberWidget = cfg.subform.fields.invoiceNumber.widget;
    const codeWidget = cfg.subform.fields.invoiceCode.widget;
    const recordNoWidget =
      cfg.main.fields.recordNo && cfg.main.fields.recordNo.widget;

    const records = await dataClient.queryRecords({
      fields: [subformWidget, recordNoWidget, dedupCfg.statusField].filter(
        (w) => w && !String(w).startsWith('FILL_')
      ),
      filter: buildStatusFilter(dedupCfg),
      limit: dedupCfg.pageSize,
      scanLimit: dedupCfg.scanLimit,
    });
    historyEntries = collectInvoiceEntries(records, {
      subformWidget,
      numberWidget,
      codeWidget,
      recordNoWidget,
    });
  } catch (e) {
    logger.error('查询历史记录失败', e.message);
    return {
      ...filled,
      status: S.duplicateInvoice,
      note: `去重查询失败，暂不能提交：${e.message}`,
    };
  }

  const dup = dedupInvoice(
    { invoiceNumber: inv.invoiceNumber, invoiceCode: inv.invoiceCode },
    historyEntries,
    {
      alsoMatchCode: cfg.invoice.dedup.alsoMatchCode,
      selfDataId: cfg.runtime.excludeSelf ? params.dataId : undefined,
    }
  );

  if (dup.duplicate) {
    const m = dup.matched || {};
    return {
      ...filled,
      status: S.duplicateInvoice,
      duplicate: true,
      matchedRecord: {
        dataId: m.dataId,
        recordNo: m.recordNo,
        rowId: m.rowId,
      },
      note: `发票重复：号码 ${inv.invoiceNumber} 已在历史报销单${
        m.recordNo ? `「${m.recordNo}」` : ''
      }中报销过，不能重复提交`,
    };
  }

  // 3) 验真（不重复才验真，真正查验一次，查验次数 +1）
  filled.verifyCount = priorCount + 1;
  try {
    const v = await verify.verify(inv);
    if (cfg.invoice.verify.requireVerify && !v.authentic) {
      return {
        ...filled,
        status: S.verifyFailed,
        note: `未发现重复；但发票验真未通过：${v.message}（状态：${v.invoiceStatus}）`,
      };
    }
    filled.note = `未发现重复；验真通过：${v.invoiceStatus}`;
  } catch (e) {
    logger.error('验真失败', e.message);
    return {
      ...filled,
      status: S.verifyFailed,
      note: `未发现重复；但发票验真调用失败：${e.message}`,
    };
  }

  // 4) 全部通过
  return {
    ...filled,
    ok: true,
    status: cfg.statusValues.verified,
    note: `${filled.note}，可提交`,
  };
}

/** 简道云后端函数入口。 */
async function main(params, _context) {
  const cfg = getConfig();
  const logger = createLogger(cfg.runtime.logLevel);
  const http = createHttpClient({ timeoutMs: 15000 });
  const ocr = createOcrClient(cfg.invoice.ocr, http, logger);
  const verify = createVerifyClient(cfg.invoice.verify, http, logger);
  const dataClient = createJdyDataClient(cfg, http);
  return runInvoiceGuard(params, { cfg, ocr, verify, dataClient, logger });
}

module.exports = main;
module.exports.main = main;
module.exports.runInvoiceGuard = runInvoiceGuard;

};
__modules["src/shared/records.js"] = function(module, exports, require){
'use strict';

/**
 * 纯函数：从简道云记录里抽取子表单行、字段值、文件 URL。
 * JDY 记录结构：record[subformWidget] = [ { _id, <widget>: value, ... }, ... ]
 * 文件/图片字段值：[ { name, url }, ... ]
 */

/** 取记录主表某字段的原始值。 */
function getFieldValue(record, widget) {
  if (!record || !widget) return undefined;
  return record[widget];
}

/** 取子表单行数组。 */
function getSubformRows(record, subformWidget) {
  const v = record && record[subformWidget];
  return Array.isArray(v) ? v : [];
}

/** 从文件/图片字段值里抽取所有 url。value 可能是数组或单对象。 */
function extractFileUrls(value) {
  if (!value) return [];
  const arr = Array.isArray(value) ? value : [value];
  const urls = [];
  for (const item of arr) {
    if (!item) continue;
    if (typeof item === 'string') {
      urls.push(item);
    } else if (typeof item === 'object' && item.url) {
      urls.push(item.url);
    }
  }
  return urls;
}

/**
 * 收集历史记录里所有子表单行的发票号码信息。
 * @param {object[]} records
 * @param {object} map { subformWidget, numberWidget, codeWidget, recordNoWidget }
 * @returns {Array<{dataId, recordNo, rowId, invoiceNumber, invoiceCode}>}
 */
function collectInvoiceEntries(records, map) {
  const out = [];
  for (const rec of records) {
    const dataId = rec._id;
    const recordNo = map.recordNoWidget ? rec[map.recordNoWidget] : undefined;
    const rows = getSubformRows(rec, map.subformWidget);
    for (const row of rows) {
      out.push({
        dataId,
        recordNo,
        rowId: row._id,
        invoiceNumber: map.numberWidget ? row[map.numberWidget] : undefined,
        invoiceCode: map.codeWidget ? row[map.codeWidget] : undefined,
      });
    }
  }
  return out;
}

/**
 * 收集历史记录里所有子表单行的凭证附件图片。
 * @param {object[]} records
 * @param {object} map { subformWidget, attachmentWidget, recordNoWidget }
 * @returns {Array<{dataId, recordNo, rowId, imageUrls:string[]}>}
 */
function collectVoucherImages(records, map) {
  const out = [];
  for (const rec of records) {
    const dataId = rec._id;
    const recordNo = map.recordNoWidget ? rec[map.recordNoWidget] : undefined;
    const rows = getSubformRows(rec, map.subformWidget);
    for (const row of rows) {
      const imageUrls = extractFileUrls(row[map.attachmentWidget]);
      if (imageUrls.length) {
        out.push({ dataId, recordNo, rowId: row._id, imageUrls });
      }
    }
  }
  return out;
}

module.exports = {
  getFieldValue,
  getSubformRows,
  extractFileUrls,
  collectInvoiceEntries,
  collectVoucherImages,
};

};
__modules["src/shared/jdyDataClient.js"] = function(module, exports, require){
'use strict';

/**
 * 简道云数据接口客户端（v5）。用于查询「费用报销单」历史记录做去重比对。
 * 文档：查询多条数据 POST /api/v5/app/entry/data/list（游标分页，limit<=100）。
 *
 * 依赖注入 httpClient，便于测试。
 */

/**
 * @param {object} cfg getConfig() 的返回
 * @param {object} http createHttpClient() 的返回
 */
function createJdyDataClient(cfg, http) {
  const { appId, entryId, apiBase, apiVersion, apiKey } = cfg.dataset;
  const listUrl = `${apiBase}/${apiVersion}/app/entry/data/list`;

  function authHeaders() {
    return {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    };
  }

  /**
   * 分页拉取满足 filter 的记录。
   * @param {object} params
   * @param {string[]} [params.fields] 只取这些 widget 字段，减小体积
   * @param {object}   [params.filter] JDY filter 对象 { rel, cond:[...] }
   * @param {number}   [params.limit=100] 每页
   * @param {number}   [params.scanLimit=5000] 最多扫描总数
   * @returns {Promise<object[]>} 记录数组
   */
  async function queryRecords(params = {}) {
    const limit = Math.min(params.limit || 100, 100);
    const scanLimit = params.scanLimit || 5000;
    const out = [];
    let cursor = '';
    // 防御性：最多翻 (scanLimit/limit)+1 页
    const maxPages = Math.ceil(scanLimit / limit) + 1;
    for (let page = 0; page < maxPages; page++) {
      const body = {
        app_id: appId,
        entry_id: entryId,
        limit,
      };
      if (params.fields && params.fields.length) body.fields = params.fields;
      if (params.filter) body.filter = params.filter;
      if (cursor) body.data_id = cursor;

      const resp = await http.postJson(listUrl, body, {
        headers: authHeaders(),
      });
      const rows = (resp && resp.data) || [];
      for (const r of rows) {
        out.push(r);
        if (out.length >= scanLimit) return out;
      }
      if (rows.length < limit) break; // 最后一页
      cursor = rows[rows.length - 1]._id;
      if (!cursor) break;
    }
    return out;
  }

  return { queryRecords, listUrl };
}

/**
 * 构造「只查已报销/审批通过」的 filter。
 * @param {object} dedupCfg cfg.invoice.dedup
 * @returns {object|undefined}
 */
function buildStatusFilter(dedupCfg) {
  if (!dedupCfg || !dedupCfg.statusField || dedupCfg.statusField.startsWith('FILL_')) {
    return undefined; // 未配置流程状态字段则不加过滤
  }
  const includes = dedupCfg.statusIncludes || [];
  if (!includes.length) return undefined;
  return {
    rel: 'and',
    cond: [
      { field: dedupCfg.statusField, type: 'text', method: 'in', value: includes },
    ],
  };
}

module.exports = { createJdyDataClient, buildStatusFilter };

};
__modules["src/invoice/dedup.js"] = function(module, exports, require){
'use strict';

const { invoiceDedupKey, normalizeInvoiceNumber } = require('../shared/normalize');

/**
 * 纯函数：发票去重。自研逻辑。
 *
 * 把历史发票条目建成 key -> 记录 的索引，再判断待校验发票是否命中。
 * key 由「发票代码 + 发票号码」归一化组合而成（数电票无代码时仅用号码）。
 */

/**
 * 构建历史发票索引。
 * @param {Array<{dataId,recordNo,rowId,invoiceNumber,invoiceCode}>} entries
 * @param {boolean} alsoMatchCode
 * @returns {Map<string, object>} key -> 首次出现的历史条目
 */
function buildInvoiceIndex(entries, alsoMatchCode) {
  const idx = new Map();
  for (const e of entries) {
    const key = invoiceDedupKey(e, alsoMatchCode);
    if (!key) continue;
    if (!idx.has(key)) idx.set(key, e);
  }
  return idx;
}

/**
 * 判断待校验发票是否与历史重复。
 * @param {{invoiceNumber:string, invoiceCode?:string}} candidate
 * @param {Map<string,object>} index buildInvoiceIndex 的结果
 * @param {object} opts { alsoMatchCode, selfDataId }
 * @returns {{duplicate:boolean, matched?:object, key:string}}
 */
function checkInvoiceDuplicate(candidate, index, opts = {}) {
  const { alsoMatchCode = true, selfDataId } = opts;
  const key = invoiceDedupKey(candidate, alsoMatchCode);
  if (!key) return { duplicate: false, key: '' };

  const hit = index.get(key);
  if (!hit) {
    // alsoMatchCode 情况下，历史里可能只登记了号码（无代码）；退一步只按号码比。
    if (alsoMatchCode) {
      const numOnly = normalizeInvoiceNumber(candidate.invoiceNumber);
      const hit2 = index.get(numOnly);
      if (hit2 && !isSelf(hit2, selfDataId)) {
        return { duplicate: true, matched: hit2, key: numOnly };
      }
    }
    return { duplicate: false, key };
  }
  if (isSelf(hit, selfDataId)) {
    return { duplicate: false, key };
  }
  return { duplicate: true, matched: hit, key };
}

function isSelf(entry, selfDataId) {
  return selfDataId && entry && entry.dataId === selfDataId;
}

/**
 * 便捷组合：一步完成建索引 + 查重。
 * @param {object} candidate
 * @param {Array} historyEntries
 * @param {object} opts
 */
function dedupInvoice(candidate, historyEntries, opts = {}) {
  const alsoMatchCode = opts.alsoMatchCode !== false;
  const index = buildInvoiceIndex(historyEntries, alsoMatchCode);
  return checkInvoiceDuplicate(candidate, index, { ...opts, alsoMatchCode });
}

module.exports = {
  buildInvoiceIndex,
  checkInvoiceDuplicate,
  dedupInvoice,
};

};
__modules["src/shared/normalize.js"] = function(module, exports, require){
'use strict';

/**
 * 纯函数：发票号码/代码归一化与工具方法。无副作用，便于单测。
 */

/**
 * 归一化发票号码/发票代码，作为去重比对的 key。
 * - 去除所有空白字符、连字符、全角空格
 * - 全角数字转半角
 * - 去掉前后不可见字符
 * - 统一为大写（数电票号码可能含字母）
 * @param {string} value
 * @returns {string} 归一化后的号码；无效输入返回空串
 */
function normalizeInvoiceNumber(value) {
  if (value === null || value === undefined) return '';
  let s = String(value);
  // 全角数字/字母 -> 半角
  s = s.replace(/[０-９Ａ-Ｚａ-ｚ]/g, (ch) =>
    String.fromCharCode(ch.charCodeAt(0) - 0xfee0)
  );
  // 去除空白、连字符、下划线、点
  s = s.replace(/[\s　\-_.]/g, '');
  return s.trim().toUpperCase();
}

/**
 * 组合去重键：发票代码 + 发票号码。数电票通常无发票代码，仅用号码。
 * @param {{invoiceCode?: string, invoiceNumber?: string}} inv
 * @param {boolean} alsoMatchCode 是否把发票代码纳入 key
 * @returns {string}
 */
function invoiceDedupKey(inv, alsoMatchCode) {
  const num = normalizeInvoiceNumber(inv && inv.invoiceNumber);
  if (!num) return '';
  if (alsoMatchCode) {
    const code = normalizeInvoiceNumber(inv && inv.invoiceCode);
    return code ? `${code}:${num}` : num;
  }
  return num;
}

/**
 * 金额归一化为数字（去掉 ￥、千分位逗号）。无法解析返回 null。
 * @param {string|number} value
 * @returns {number|null}
 */
function normalizeAmount(value) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  const cleaned = String(value).replace(/[^0-9.\-]/g, '');
  if (cleaned === '' || cleaned === '-' || cleaned === '.') return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

/**
 * 归一化日期为 YYYY-MM-DD。支持 20240131 / 2024年01月31日 / 2024-1-31 等。
 * @param {string} value
 * @returns {string} 无法解析返回原值 trim
 */
function normalizeDate(value) {
  if (!value) return '';
  const s = String(value).trim();
  const digits = s.replace(/[^0-9]/g, '');
  if (digits.length === 8) {
    return `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}`;
  }
  const m = s.match(/(\d{4})\D+(\d{1,2})\D+(\d{1,2})/);
  if (m) {
    const mm = m[2].padStart(2, '0');
    const dd = m[3].padStart(2, '0');
    return `${m[1]}-${mm}-${dd}`;
  }
  return s;
}

module.exports = {
  normalizeInvoiceNumber,
  invoiceDedupKey,
  normalizeAmount,
  normalizeDate,
};

};
__modules["src/invoice/verifyClient.js"] = function(module, exports, require){
'use strict';

/**
 * 发票查验（验真）适配器：校验发票号码真伪与状态。
 *
 * 参考猫猫发票识别插件的验真方式：以发票代码/号码/开票日期/校验码或金额为查验要素，
 * 调用税务查验通道（如猫猫/诺诺/百望等）返回真伪与发票状态（正常/作废/红冲）。
 * 做成可插拔 provider，默认 maomao。
 */

/**
 * @param {object} verifyCfg cfg.invoice.verify
 * @param {object} http
 * @param {object} [logger]
 */
function createVerifyClient(verifyCfg, http, logger = console) {
  /**
   * @param {object} inv 归一化后的发票字段（ocrClient.recognize 的结果）
   * @returns {Promise<{authentic:boolean, invoiceStatus:string, message:string, raw:any}>}
   */
  async function verify(inv) {
    if (!verifyCfg.requireVerify) {
      return { authentic: true, invoiceStatus: '未查验', message: '未开启验真', raw: null };
    }
    if (!verifyCfg.endpoint) {
      throw new Error(
        '验真: 未配置查验服务地址（INVOICE_VERIFY_ENDPOINT）。'
      );
    }
    // 数电票通常无发票代码；查验要素以号码为主。
    if (!inv || !inv.invoiceNumber) {
      return {
        authentic: false,
        invoiceStatus: '要素缺失',
        message: '缺少发票号码，无法查验',
        raw: null,
      };
    }

    const headers = { 'Content-Type': 'application/json' };
    if (verifyCfg.appKey) headers['X-App-Key'] = verifyCfg.appKey;
    if (verifyCfg.appSecret) headers['X-App-Secret'] = verifyCfg.appSecret;

    const body = {
      invoiceCode: inv.invoiceCode || '',
      invoiceNumber: inv.invoiceNumber,
      invoiceDate: inv.invoiceDate || '',
      checkCode: inv.checkCode || '',
      amount: inv.invoiceAmount != null ? String(inv.invoiceAmount) : '',
    };

    const raw = await http.postJson(verifyCfg.endpoint, body, {
      headers,
      timeoutMs: verifyCfg.timeoutMs,
    });
    const result = interpretVerify(raw);
    if (logger && logger.debug) logger.debug('Verify result:', result);
    return { ...result, raw };
  }

  return { verify };
}

/**
 * 解释查验返回。兼容多种返回结构；判定 authentic 与发票状态。
 * @returns {{authentic:boolean, invoiceStatus:string, message:string}}
 */
function interpretVerify(raw) {
  const r = (raw && (raw.data || raw.result || raw.Response)) || raw || {};

  // 明确的成功/真伪标志
  const codeOk =
    r.code === 0 || r.code === '0000' || r.success === true || r.errcode === 0;

  // 常见状态字段：正常/作废/红冲/查无此票
  const statusRaw =
    r.invoiceStatus || r.status || r.state || r.checkResult || r.result || '';
  const statusStr = String(statusRaw);

  const abnormal = /作废|红冲|异常|查无|不一致|失败|无效/.test(statusStr);
  const normal = /正常|一致|已开|成功|验证通过|true/i.test(statusStr);

  let authentic;
  if (abnormal) authentic = false;
  else if (normal || codeOk) authentic = true;
  else authentic = Boolean(codeOk);

  const message =
    r.message || r.msg || r.desc || statusStr || (authentic ? '查验通过' : '查验未通过');

  return {
    authentic,
    invoiceStatus: statusStr || (authentic ? '正常' : '未知'),
    message: String(message),
  };
}

module.exports = { createVerifyClient, interpretVerify };

};
__modules["src/invoice/ocrClient.js"] = function(module, exports, require){
'use strict';

const {
  normalizeInvoiceNumber,
  normalizeAmount,
  normalizeDate,
} = require('../shared/normalize');

/**
 * 发票 OCR 识别适配器。
 *
 * 识别与验真方式参考「重庆猫猫智能科技有限公司」的发票识别插件：给定发票图片 URL，
 * 调用识别服务返回发票代码/号码/日期/金额/税额/校验码/销方税号等字段。
 * 这里做成可插拔 provider，默认 maomao，同时兼容百度/腾讯/华为等返回结构。
 *
 * 依赖注入 http（createHttpClient 的返回）。
 */

/**
 * @param {object} ocrCfg cfg.invoice.ocr（含 provider/endpoint/appKey/appSecret/timeoutMs）
 * @param {object} http
 * @param {object} [logger]
 */
function createOcrClient(ocrCfg, http, logger = console) {
  async function recognize(imageUrl) {
    if (!imageUrl) throw new Error('OCR: imageUrl 为空');
    if (!ocrCfg.endpoint) {
      throw new Error(
        'OCR: 未配置识别服务地址（INVOICE_OCR_ENDPOINT）。请填入所选发票识别服务的接口地址。'
      );
    }
    const payload = buildRequest(ocrCfg, imageUrl);
    const raw = await http.postJson(ocrCfg.endpoint, payload.body, {
      headers: payload.headers,
      timeoutMs: ocrCfg.timeoutMs,
    });
    const mapped = mapOcrResult(raw, ocrCfg.provider);
    if (logger && logger.debug) logger.debug('OCR mapped:', mapped);
    return mapped;
  }

  return { recognize };
}

/** 构造不同 provider 的请求体与鉴权头。 */
function buildRequest(ocrCfg, imageUrl) {
  const headers = { 'Content-Type': 'application/json' };
  // 大多数第三方识别服务用 appKey/appSecret 走 header 或签名；此处提供通用形式，
  // 具体 provider 可按其文档在部署时调整。
  if (ocrCfg.appKey) headers['X-App-Key'] = ocrCfg.appKey;
  if (ocrCfg.appSecret) headers['X-App-Secret'] = ocrCfg.appSecret;

  switch (ocrCfg.provider) {
    case 'baidu':
      return { headers, body: { url: imageUrl } };
    case 'tencent':
      return { headers, body: { ImageUrl: imageUrl } };
    case 'huawei':
      return { headers, body: { image_url: imageUrl } };
    case 'maomao':
    case 'custom':
    default:
      // 猫猫/通用：传图片地址
      return { headers, body: { imageUrl, url: imageUrl, type: 'vat_invoice' } };
  }
}

/**
 * 把不同 provider 的返回结构统一成标准发票字段。
 * 通过多候选 key 兼容常见返回（中文/英文/百度/腾讯/华为）。
 * @returns {{invoiceType,invoiceCode,invoiceNumber,invoiceDate,invoiceAmount,taxAmount,amountWithTax,checkCode,sellerTaxNo,raw,recognized}}
 */
function mapOcrResult(raw, provider) {
  // 尽量下钻到承载字段的对象
  const r = unwrap(raw);

  const invoiceCode = pick(r, [
    'invoiceCode', 'InvoiceCode', 'fpdm', '发票代码', 'code',
  ]);
  const invoiceNumber = pick(r, [
    'invoiceNumber', 'InvoiceNum', 'InvoiceNumber', 'fphm', '发票号码', 'number', 'serialNumber',
  ]);
  const invoiceDate = pick(r, [
    'invoiceDate', 'InvoiceDate', 'kprq', '开票日期', '票据日期', 'date',
  ]);
  const invoiceType = pick(r, [
    'invoiceType', 'InvoiceType', 'fplx', '发票类型', 'type', 'title',
  ]);
  const invoiceAmount = pick(r, [
    'invoiceAmount', 'AmountWithoutTax', 'TotalAmount', 'je', '金额', '不含税金额', 'amount',
  ]);
  const taxAmount = pick(r, [
    'taxAmount', 'TaxAmount', 'Tax', 'se', '税额',
  ]);
  const amountWithTax = pick(r, [
    'amountWithTax', 'AmountWithTax', 'jshj', '价税合计', 'total', 'totalAmount',
  ]);
  const checkCode = pick(r, [
    'checkCode', 'CheckCode', 'jym', '校验码',
  ]);
  const sellerTaxNo = pick(r, [
    'sellerTaxNo', 'SellerTaxID', 'SellerRegisterNum', 'xfsh', '销方税号', '销售方纳税人识别号',
  ]);

  const number = normalizeInvoiceNumber(invoiceNumber);

  return {
    provider,
    invoiceType: invoiceType || '',
    invoiceCode: normalizeInvoiceNumber(invoiceCode),
    invoiceNumber: number,
    invoiceDate: normalizeDate(invoiceDate),
    invoiceAmount: normalizeAmount(invoiceAmount),
    taxAmount: normalizeAmount(taxAmount),
    amountWithTax: normalizeAmount(amountWithTax),
    checkCode: checkCode ? String(checkCode).trim() : '',
    sellerTaxNo: sellerTaxNo ? String(sellerTaxNo).trim() : '',
    recognized: Boolean(number),
    raw,
  };
}

/** 从常见外层包裹里取出真正的数据对象。 */
function unwrap(raw) {
  if (!raw || typeof raw !== 'object') return {};
  // 常见包裹：{ data: {...} } / { result: {...} } / { words_result: {...} } / { Response: { VatInvoiceInfos } }
  const candidates = [
    raw.data && raw.data.result,
    raw.data,
    raw.result,
    raw.words_result,
    raw.Response,
    raw.invoice,
    raw,
  ];
  for (const c of candidates) {
    if (c && typeof c === 'object') return flattenWords(c);
  }
  return raw;
}

/** 百度 words_result 里常见 {字段:{words:'值'}} 结构，拍平成 {字段:'值'}。 */
function flattenWords(obj) {
  const out = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v && typeof v === 'object' && typeof v.words === 'string') {
      out[k] = v.words;
    } else {
      out[k] = v;
    }
  }
  return out;
}

/** 在对象里按多个候选 key 取第一个非空值。 */
function pick(obj, keys) {
  if (!obj || typeof obj !== 'object') return undefined;
  for (const k of keys) {
    if (obj[k] !== undefined && obj[k] !== null && obj[k] !== '') return obj[k];
  }
  return undefined;
}

module.exports = { createOcrClient, mapOcrResult };

};
__modules["src/shared/logger.js"] = function(module, exports, require){
'use strict';

const LEVELS = { error: 0, warn: 1, info: 2, debug: 3 };

/**
 * 极简分级日志。简道云后端函数支持 console.*，执行日志里可见。
 * @param {string} level
 */
function createLogger(level = 'info') {
  const threshold = LEVELS[level] === undefined ? LEVELS.info : LEVELS[level];
  const emit = (lvl, method) => (...args) => {
    if (LEVELS[lvl] <= threshold) {
      // eslint-disable-next-line no-console
      (console[method] || console.log)(`[reimb-guard][${lvl}]`, ...args);
    }
  };
  return {
    error: emit('error', 'error'),
    warn: emit('warn', 'warn'),
    info: emit('info', 'log'),
    debug: emit('debug', 'log'),
  };
}

module.exports = { createLogger };

};
__modules["src/shared/httpClient.js"] = function(module, exports, require){
'use strict';

/**
 * 统一 HTTP 客户端：优先用运行时自带的 fetch（Node18+ / 简道云后端函数），
 * 无 fetch 时回退到 axios。内置超时 + 指数退避重试。
 *
 * 设计成可注入（tests 里传入 fakeFetch），避免真实网络。
 */

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function resolveFetch(injected) {
  if (injected) return injected;
  if (typeof fetch === 'function') return fetch;
  try {
    // 简道云后端函数环境通常内置 axios
    // eslint-disable-next-line global-require
    const axios = require('axios');
    return axiosAsFetch(axios);
  } catch (_e) {
    throw new Error(
      'No fetch available and axios not installed. Provide a fetch implementation.'
    );
  }
}

/** 把 axios 适配成 fetch-like，只覆盖本插件用到的能力。 */
function axiosAsFetch(axios) {
  return async function fetchLike(url, opts = {}) {
    const res = await axios({
      url,
      method: opts.method || 'GET',
      headers: opts.headers,
      data: opts.body,
      timeout: opts.timeoutMs,
      responseType: opts.responseType === 'arraybuffer' ? 'arraybuffer' : 'text',
      // axios 抛错 by default on non-2xx；这里统一交给上层判断
      validateStatus: () => true,
    });
    return {
      ok: res.status >= 200 && res.status < 300,
      status: res.status,
      async json() {
        return typeof res.data === 'string' ? JSON.parse(res.data) : res.data;
      },
      async text() {
        return typeof res.data === 'string' ? res.data : JSON.stringify(res.data);
      },
      async arrayBuffer() {
        return res.data;
      },
    };
  };
}

/**
 * @param {object} options
 * @param {number} [options.retries=2]
 * @param {number} [options.timeoutMs=15000]
 * @param {Function} [options.fetchImpl] 注入的 fetch（测试用）
 * @param {Function} [options.onRetry]
 */
function createHttpClient(options = {}) {
  const {
    retries = 2,
    timeoutMs = 15000,
    fetchImpl,
    onRetry = () => {},
  } = options;
  const doFetch = resolveFetch(fetchImpl);

  async function request(url, opts = {}) {
    const perCallTimeout = opts.timeoutMs || timeoutMs;
    let lastErr;
    for (let attempt = 0; attempt <= retries; attempt++) {
      const controller =
        typeof AbortController === 'function' ? new AbortController() : null;
      const timer = controller
        ? setTimeout(() => controller.abort(), perCallTimeout)
        : null;
      try {
        const res = await doFetch(url, {
          ...opts,
          timeoutMs: perCallTimeout,
          signal: controller ? controller.signal : undefined,
        });
        if (timer) clearTimeout(timer);
        // 5xx / 429 视为可重试
        if ((res.status >= 500 || res.status === 429) && attempt < retries) {
          lastErr = new Error(`HTTP ${res.status}`);
          throw lastErr;
        }
        return res;
      } catch (err) {
        if (timer) clearTimeout(timer);
        lastErr = err;
        if (attempt < retries) {
          const backoff = 2 ** attempt * 500;
          onRetry(attempt + 1, err);
          await sleep(backoff);
          continue;
        }
        throw lastErr;
      }
    }
    throw lastErr;
  }

  async function getJson(url, opts = {}) {
    const res = await request(url, { ...opts, method: opts.method || 'GET' });
    if (!res.ok) {
      const body = await safeText(res);
      throw new Error(`GET ${url} -> ${res.status} ${body.slice(0, 300)}`);
    }
    return res.json();
  }

  async function postJson(url, payload, opts = {}) {
    const res = await request(url, {
      ...opts,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = await safeText(res);
      throw new Error(`POST ${url} -> ${res.status} ${body.slice(0, 300)}`);
    }
    return res.json();
  }

  async function getArrayBuffer(url, opts = {}) {
    const res = await request(url, {
      ...opts,
      method: 'GET',
      responseType: 'arraybuffer',
    });
    if (!res.ok) throw new Error(`GET(bin) ${url} -> ${res.status}`);
    return res.arrayBuffer();
  }

  return { request, getJson, postJson, getArrayBuffer };
}

async function safeText(res) {
  try {
    return await res.text();
  } catch (_e) {
    return '';
  }
}

module.exports = { createHttpClient, sleep };

};
__modules["src/shared/config.js"] = function(module, exports, require){
'use strict';

const fs = require('fs');
const path = require('path');

/**
 * 加载 plugin.config.json 并解析密钥（通过 *Env 指向的环境变量读取）。
 * 简道云自建插件里，密钥应放在插件的「密钥/环境变量」配置项中，这里统一从 env 读取。
 */

let cached = null;
let embedded = null;

/**
 * 供打包版本使用：把 plugin.config.json 的内容直接内联进来，
 * 避免运行时读文件（简道云代码框里没有文件系统）。
 */
function setEmbeddedConfig(raw) {
  embedded = raw;
  cached = null;
}

function loadRawConfig(configPath) {
  if (embedded && !configPath) return embedded;
  const p =
    configPath || path.join(__dirname, '..', '..', 'plugin.config.json');
  const text = fs.readFileSync(p, 'utf8');
  return JSON.parse(text);
}

function env(name, fallback = '') {
  return process.env[name] !== undefined ? process.env[name] : fallback;
}

/**
 * 返回带密钥解析的配置对象。可传入 overrides（测试注入）。
 * @param {object} [opts]
 * @param {string} [opts.configPath]
 * @param {object} [opts.raw] 直接注入配置对象（跳过读文件）
 * @param {object} [opts.env] 注入 env 映射
 */
function getConfig(opts = {}) {
  if (cached && !opts.raw && !opts.configPath && !opts.env) return cached;
  const raw = opts.raw || loadRawConfig(opts.configPath);
  const readEnv = (name, fallback) =>
    opts.env ? (opts.env[name] !== undefined ? opts.env[name] : fallback) : env(name, fallback);

  const cfg = {
    raw,
    dataset: {
      appId: raw.dataset.appId,
      entryId: raw.dataset.entryId,
      apiBase: raw.dataset.apiBase,
      apiVersion: raw.dataset.apiVersion,
      apiKey: readEnv(raw.dataset.apiKeyEnv, ''),
    },
    main: raw.main || { fields: {} },
    subform: raw.subform,
    statusValues: raw.statusValues,
    invoice: {
      ocr: {
        ...raw.invoice.ocr,
        endpoint: readEnv(raw.invoice.ocr.endpointEnv, ''),
        appKey: readEnv(raw.invoice.ocr.appKeyEnv, ''),
        appSecret: readEnv(raw.invoice.ocr.appSecretEnv, ''),
      },
      verify: {
        ...raw.invoice.verify,
        endpoint: readEnv(raw.invoice.verify.endpointEnv, ''),
        appKey: readEnv(raw.invoice.verify.appKeyEnv, ''),
        appSecret: readEnv(raw.invoice.verify.appSecretEnv, ''),
      },
      dedup: raw.invoice.dedup,
    },
    voucher: {
      similarity: {
        ...raw.voucher.similarity,
        endpoint: readEnv(raw.voucher.similarity.endpointEnv, ''),
        apiKey: readEnv(raw.voucher.similarity.apiKeyEnv, ''),
      },
    },
    runtime: raw.runtime,
  };

  if (!opts.raw && !opts.configPath && !opts.env) cached = cfg;
  return cfg;
}

/** 便捷取子表单字段的 widget id。 */
function fieldWidget(cfg, role) {
  const f = cfg.subform.fields[role];
  return f ? f.widget : undefined;
}

module.exports = { getConfig, fieldWidget, loadRawConfig, setEmbeddedConfig };

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
var __entry = __load("src/invoice/invoiceBackend.js");
module.exports = __entry;
if (typeof module.exports === 'function') { module.exports.main = module.exports.main || module.exports; }
