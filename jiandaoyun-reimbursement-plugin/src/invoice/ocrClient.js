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
