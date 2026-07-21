/*
 * 简道云 · 自建插件 · 后端函数：发票识别 · 去重 · 验真
 * =====================================================================
 * 这是一段“方法体”（不是模块）。请勿加 module.exports / require 本地文件。
 *
 * 安装：简道云 → 插件管理 → 新建自建插件 → 新建函数（类型：后端函数）→
 *       把本文件从「======= 方法体开始 =======」到文件末尾的内容整段粘进代码框。
 *
 * 入参声明（请求参数）——在函数“入参声明”里逐个添加，类型 any：
 *   imageUrl  (必填)  发票图片可访问 URL
 *   dataId    (选填)  当前报销单 dataId，编辑时排除自身
 *   rowId     (选填)  子表单当前行 id
 *   priorVerifyCount (选填) 已有查验次数
 *
 * 出参声明（返回参数）——在函数“出参声明”里逐个添加，类型 any：
 *   ok, status, invoiceType, invoiceCode, invoiceNumber, invoiceDate,
 *   invoiceAmount, taxAmount, amountWithTax, checkCode, sellerTaxNo,
 *   verifyCount, note, duplicate, matchedRecord
 *
 * 运行时假设：方法体是 async，入参可通过 params 对象拿到；可用 fetch 或 axios。
 * 若你的编辑器给的入口签名不同（例如参数名不是 params），把下面的 readParams() 改一行即可。
 * =====================================================================
 */

// ===================== 配置：按你的表单与服务填写 =====================
var CONFIG = {
  // 简道云数据接口（用于查历史已报销记录做去重）
  jdy: {
    apiKey: 'FILL_简道云APIKey',
    appId: '68ca0e2fb59e070714b68aa0',
    entryId: '6899902c9582f683ab885f8d',
    listUrl: 'https://api.jiandaoyun.com/api/v5/app/entry/data/list'
  },
  // 子表单「票据录入」及字段的 widget id
  fields: {
    subform: 'FILL_票据录入_widget',
    invoiceNumber: 'FILL_发票号码_widget',
    invoiceCode: 'FILL_发票代码_widget',
    recordNo: 'FILL_报销单编号_widget',    // 主表字段，用于回显命中记录
    flowStatus: 'FILL_流程状态_widget'      // 主表字段，去重只比对已报销
  },
  dedup: {
    statusIncludes: ['已完成', '审批通过', '已报销'], // 参与去重的流程状态；留空则不加过滤
    alsoMatchCode: true,     // 是否把发票代码纳入去重键
    scanLimit: 5000,
    pageSize: 100,
    excludeSelf: true
  },
  // 发票 OCR 识别服务（参考猫猫发票识别；填你所用服务的地址与鉴权）
  ocr: {
    endpoint: 'FILL_OCR识别接口地址',
    appKey: 'FILL_OCR_AppKey',
    appSecret: 'FILL_OCR_AppSecret',
    timeoutMs: 15000
  },
  // 发票验真服务
  verify: {
    requireVerify: true,
    endpoint: 'FILL_验真接口地址',
    appKey: 'FILL_验真_AppKey',
    appSecret: 'FILL_验真_AppSecret',
    timeoutMs: 15000
  },
  status: {
    verified: '验证通过',
    duplicateInvoice: '发票重复',
    verifyFailed: '验真失败',
    ocrFailed: '识别失败'
  }
};

// ============================ 方法体开始 ============================
var params = (typeof params !== 'undefined') ? params : {};       // eslint-disable-line
var P = readParams();
return await runInvoiceGuard(P);

function readParams() {
  // 兼容 params 对象 / 顶层变量两种注入方式
  var src = (typeof params !== 'undefined' && params) ? params : {};
  return {
    imageUrl: src.imageUrl,
    dataId: src.dataId,
    rowId: src.rowId,
    priorVerifyCount: Number(src.priorVerifyCount) || 0
  };
}

async function runInvoiceGuard(p) {
  var S = CONFIG.status;
  var prior = Number(p.priorVerifyCount) || 0;
  var base = {
    ok: false, status: S.ocrFailed, invoiceType: '', invoiceCode: '', invoiceNumber: '',
    invoiceDate: '', invoiceAmount: null, taxAmount: null, amountWithTax: null,
    checkCode: '', sellerTaxNo: '', verifyCount: prior, note: '', duplicate: false, matchedRecord: null
  };
  if (!p.imageUrl) return Object.assign(base, { note: '未取到发票图片地址' });

  // 1) OCR 识别
  var inv;
  try {
    inv = await ocrRecognize(p.imageUrl);
  } catch (e) {
    return Object.assign(base, { note: '发票识别失败：' + e.message });
  }
  if (!inv.invoiceNumber) {
    return Object.assign(base, { note: '未能识别到有效发票号码，请上传清晰的发票图片' });
  }
  var filled = Object.assign({}, base, {
    invoiceType: inv.invoiceType, invoiceCode: inv.invoiceCode, invoiceNumber: inv.invoiceNumber,
    invoiceDate: inv.invoiceDate, invoiceAmount: inv.invoiceAmount, taxAmount: inv.taxAmount,
    amountWithTax: inv.amountWithTax, checkCode: inv.checkCode, sellerTaxNo: inv.sellerTaxNo,
    verifyCount: prior
  });

  // 2) 去重（先查重：命中即拦截，省去验真调用）
  var dup;
  try {
    var history = await queryHistoryInvoices();
    dup = checkDuplicate(inv, history, p.dataId);
  } catch (e) {
    return Object.assign(filled, { status: S.duplicateInvoice, note: '去重查询失败，暂不能提交：' + e.message });
  }
  if (dup.duplicate) {
    var m = dup.matched || {};
    return Object.assign(filled, {
      status: S.duplicateInvoice, duplicate: true,
      matchedRecord: { dataId: m.dataId, recordNo: m.recordNo, rowId: m.rowId },
      note: '发票重复：号码 ' + inv.invoiceNumber + ' 已在历史报销单' +
            (m.recordNo ? '「' + m.recordNo + '」' : '') + '中报销过，不能重复提交'
    });
  }

  // 3) 验真（仅对不重复的发票，查验次数 +1）
  filled.verifyCount = prior + 1;
  try {
    var v = await verifyInvoice(inv);
    if (CONFIG.verify.requireVerify && !v.authentic) {
      return Object.assign(filled, { status: S.verifyFailed, note: '未发现重复；但发票验真未通过：' + v.message + '（状态：' + v.invoiceStatus + '）' });
    }
    filled.note = '未发现重复；验真通过：' + v.invoiceStatus;
  } catch (e) {
    return Object.assign(filled, { status: S.verifyFailed, note: '未发现重复；但发票验真调用失败：' + e.message });
  }

  return Object.assign(filled, { ok: true, status: S.verified, note: filled.note + '，可提交' });
}

// ---------------- OCR / 验真 ----------------
async function ocrRecognize(imageUrl) {
  if (!CONFIG.ocr.endpoint || CONFIG.ocr.endpoint.indexOf('FILL_') === 0) throw new Error('未配置 OCR 识别接口地址');
  var raw = await httpPostJson(CONFIG.ocr.endpoint, { imageUrl: imageUrl, url: imageUrl }, ocrHeaders(CONFIG.ocr), CONFIG.ocr.timeoutMs);
  return mapOcr(raw);
}
function ocrHeaders(c) {
  var h = { 'Content-Type': 'application/json' };
  if (c.appKey) h['X-App-Key'] = c.appKey;
  if (c.appSecret) h['X-App-Secret'] = c.appSecret;
  return h;
}
function mapOcr(raw) {
  var r = unwrap(raw);
  var number = normNo(pick(r, ['invoiceNumber', 'InvoiceNum', 'InvoiceNumber', 'fphm', '发票号码', 'number']));
  return {
    invoiceType: pick(r, ['invoiceType', 'InvoiceType', 'fplx', '发票类型', 'type']) || '',
    invoiceCode: normNo(pick(r, ['invoiceCode', 'InvoiceCode', 'fpdm', '发票代码', 'code'])),
    invoiceNumber: number,
    invoiceDate: normDate(pick(r, ['invoiceDate', 'InvoiceDate', 'kprq', '开票日期', '票据日期', 'date'])),
    invoiceAmount: normAmt(pick(r, ['invoiceAmount', 'AmountWithoutTax', 'je', '金额', '不含税金额'])),
    taxAmount: normAmt(pick(r, ['taxAmount', 'TaxAmount', 'se', '税额'])),
    amountWithTax: normAmt(pick(r, ['amountWithTax', 'AmountWithTax', 'jshj', '价税合计', 'total'])),
    checkCode: (pick(r, ['checkCode', 'CheckCode', 'jym', '校验码']) || '').toString().trim(),
    sellerTaxNo: (pick(r, ['sellerTaxNo', 'SellerTaxID', 'xfsh', '销方税号']) || '').toString().trim()
  };
}
async function verifyInvoice(inv) {
  if (!CONFIG.verify.requireVerify) return { authentic: true, invoiceStatus: '未查验', message: '未开启验真' };
  if (!CONFIG.verify.endpoint || CONFIG.verify.endpoint.indexOf('FILL_') === 0) throw new Error('未配置验真接口地址');
  var body = {
    invoiceCode: inv.invoiceCode || '', invoiceNumber: inv.invoiceNumber,
    invoiceDate: inv.invoiceDate || '', checkCode: inv.checkCode || '',
    amount: inv.invoiceAmount != null ? String(inv.invoiceAmount) : ''
  };
  var raw = await httpPostJson(CONFIG.verify.endpoint, body, ocrHeaders(CONFIG.verify), CONFIG.verify.timeoutMs);
  var r = (raw && (raw.data || raw.result || raw.Response)) || raw || {};
  var st = String(r.invoiceStatus || r.status || r.state || r.checkResult || r.result || '');
  var codeOk = r.code === 0 || r.code === '0000' || r.success === true || r.errcode === 0;
  var abnormal = /作废|红冲|异常|查无|不一致|失败|无效/.test(st);
  var normal = /正常|一致|已开|成功|验证通过/.test(st);
  var authentic = abnormal ? false : (normal || codeOk);
  return { authentic: authentic, invoiceStatus: st || (authentic ? '正常' : '未知'), message: String(r.message || r.msg || st || (authentic ? '查验通过' : '查验未通过')) };
}

// ---------------- 去重（自研）----------------
async function queryHistoryInvoices() {
  var f = CONFIG.fields, d = CONFIG.dedup;
  var wanted = [f.subform, f.recordNo, f.flowStatus].filter(function (w) { return w && w.indexOf('FILL_') !== 0; });
  var filter;
  if (f.flowStatus && f.flowStatus.indexOf('FILL_') !== 0 && d.statusIncludes && d.statusIncludes.length) {
    filter = { rel: 'and', cond: [{ field: f.flowStatus, type: 'text', method: 'in', value: d.statusIncludes }] };
  }
  var out = [], cursor = '', limit = Math.min(d.pageSize || 100, 100);
  var maxPages = Math.ceil((d.scanLimit || 5000) / limit) + 1;
  for (var page = 0; page < maxPages; page++) {
    var reqBody = { app_id: CONFIG.jdy.appId, entry_id: CONFIG.jdy.entryId, limit: limit };
    if (wanted.length) reqBody.fields = wanted;
    if (filter) reqBody.filter = filter;
    if (cursor) reqBody.data_id = cursor;
    var resp = await httpPostJson(CONFIG.jdy.listUrl, reqBody, { 'Content-Type': 'application/json', Authorization: 'Bearer ' + CONFIG.jdy.apiKey }, 15000);
    var rows = (resp && resp.data) || [];
    for (var i = 0; i < rows.length; i++) { out.push(rows[i]); if (out.length >= (d.scanLimit || 5000)) return out; }
    if (rows.length < limit) break;
    cursor = rows[rows.length - 1]._id;
    if (!cursor) break;
  }
  return out;
}
function checkDuplicate(inv, records, selfDataId) {
  var f = CONFIG.fields, also = CONFIG.dedup.alsoMatchCode;
  var index = {};
  for (var i = 0; i < records.length; i++) {
    var rec = records[i];
    var sub = Array.isArray(rec[f.subform]) ? rec[f.subform] : [];
    for (var j = 0; j < sub.length; j++) {
      var row = sub[j];
      var key = dedupKey({ invoiceNumber: row[f.invoiceNumber], invoiceCode: row[f.invoiceCode] }, also);
      if (!key) continue;
      if (!index[key]) index[key] = { dataId: rec._id, recordNo: f.recordNo ? rec[f.recordNo] : undefined, rowId: row._id };
      var numOnly = normNo(row[f.invoiceNumber]);
      if (also && numOnly && !index[numOnly]) index[numOnly] = { dataId: rec._id, recordNo: f.recordNo ? rec[f.recordNo] : undefined, rowId: row._id };
    }
  }
  var candKey = dedupKey(inv, also);
  var hit = candKey && index[candKey];
  if (!hit && also) { var n = normNo(inv.invoiceNumber); hit = n && index[n]; }
  if (hit && CONFIG.dedup.excludeSelf && selfDataId && hit.dataId === selfDataId) hit = null;
  return { duplicate: !!hit, matched: hit || null };
}
function dedupKey(inv, alsoMatchCode) {
  var num = normNo(inv && inv.invoiceNumber);
  if (!num) return '';
  if (alsoMatchCode) { var code = normNo(inv && inv.invoiceCode); return code ? code + ':' + num : num; }
  return num;
}

// ---------------- 通用工具 ----------------
function normNo(v) {
  if (v == null) return '';
  var s = String(v).replace(/[０-９Ａ-Ｚａ-ｚ]/g, function (ch) { return String.fromCharCode(ch.charCodeAt(0) - 0xfee0); });
  return s.replace(/[\s　\-_.]/g, '').trim().toUpperCase();
}
function normAmt(v) {
  if (v == null || v === '') return null;
  if (typeof v === 'number') return isFinite(v) ? v : null;
  var c = String(v).replace(/[^0-9.\-]/g, '');
  if (c === '' || c === '-' || c === '.') return null;
  var n = Number(c); return isFinite(n) ? n : null;
}
function normDate(v) {
  if (!v) return '';
  var s = String(v).trim(), digits = s.replace(/[^0-9]/g, '');
  if (digits.length === 8) return digits.slice(0, 4) + '-' + digits.slice(4, 6) + '-' + digits.slice(6, 8);
  var m = s.match(/(\d{4})\D+(\d{1,2})\D+(\d{1,2})/);
  if (m) return m[1] + '-' + ('0' + m[2]).slice(-2) + '-' + ('0' + m[3]).slice(-2);
  return s;
}
function unwrap(raw) {
  if (!raw || typeof raw !== 'object') return {};
  var cands = [raw.data && raw.data.result, raw.data, raw.result, raw.words_result, raw.Response, raw.invoice, raw];
  for (var i = 0; i < cands.length; i++) { var c = cands[i]; if (c && typeof c === 'object') return flattenWords(c); }
  return raw;
}
function flattenWords(obj) {
  var out = {};
  for (var k in obj) { if (!Object.prototype.hasOwnProperty.call(obj, k)) continue; var v = obj[k]; out[k] = (v && typeof v === 'object' && typeof v.words === 'string') ? v.words : v; }
  return out;
}
function pick(obj, keys) {
  if (!obj || typeof obj !== 'object') return undefined;
  for (var i = 0; i < keys.length; i++) { var k = keys[i]; if (obj[k] != null && obj[k] !== '') return obj[k]; }
  return undefined;
}

// ---------------- HTTP（fetch 优先，回退 axios）----------------
async function httpPostJson(url, body, headers, timeoutMs) {
  if (typeof fetch === 'function') {
    var res = await fetch(url, { method: 'POST', headers: headers, body: JSON.stringify(body) });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  }
  var axios = require('axios');
  var r = await axios({ url: url, method: 'POST', headers: headers, data: body, timeout: timeoutMs, validateStatus: function () { return true; } });
  if (r.status < 200 || r.status >= 300) throw new Error('HTTP ' + r.status);
  return typeof r.data === 'string' ? JSON.parse(r.data) : r.data;
}
// ============================ 方法体结束 ============================
