'use strict';

/** 构造用于单测的精简配置，字段 widget 用短名。 */
function fakeConfig(overrides = {}) {
  const cfg = {
    statusValues: {
      pending: '待验证',
      verified: '验证通过',
      duplicateInvoice: '发票重复',
      verifyFailed: '验真失败',
      duplicateVoucher: '凭证重复',
      ocrFailed: '识别失败',
    },
    main: { fields: { recordNo: { widget: 'no' } } },
    subform: {
      widget: 'sub',
      fields: {
        invoiceNumber: { widget: 'num' },
        invoiceCode: { widget: 'code' },
        voucherAttachment: { widget: 'att' },
      },
    },
    invoice: {
      verify: { requireVerify: true },
      dedup: {
        statusField: 'FILL_flow',
        statusIncludes: ['已完成'],
        alsoMatchCode: true,
        pageSize: 100,
        scanLimit: 1000,
      },
    },
    voucher: {
      similarity: {
        threshold: 0.9,
        timeoutMs: 1000,
        maxCandidates: 50,
        prefilter: { enabled: false, topK: 8, hammingMaxDistance: 12 },
      },
    },
    runtime: { excludeSelf: true, logLevel: 'error' },
  };
  return deepMerge(cfg, overrides);
}

function deepMerge(a, b) {
  const out = Array.isArray(a) ? a.slice() : { ...a };
  for (const k of Object.keys(b || {})) {
    if (b[k] && typeof b[k] === 'object' && !Array.isArray(b[k]) && typeof out[k] === 'object') {
      out[k] = deepMerge(out[k], b[k]);
    } else {
      out[k] = b[k];
    }
  }
  return out;
}

const silentLogger = { error() {}, warn() {}, info() {}, debug() {} };

module.exports = { fakeConfig, silentLogger };
