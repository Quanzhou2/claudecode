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
    verifyCount: priorCount + 1,
  };

  // 2) 验真
  try {
    const v = await verify.verify(inv);
    if (cfg.invoice.verify.requireVerify && !v.authentic) {
      return {
        ...filled,
        status: S.verifyFailed,
        note: `发票验真未通过：${v.message}（状态：${v.invoiceStatus}）`,
      };
    }
    filled.note = `验真通过：${v.invoiceStatus}`;
  } catch (e) {
    logger.error('验真失败', e.message);
    return {
      ...filled,
      status: S.verifyFailed,
      note: `发票验真调用失败：${e.message}`,
    };
  }

  // 3) 去重（与历史已报销记录比对）
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
      status: S.verifyFailed,
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

  // 4) 全部通过
  return {
    ...filled,
    ok: true,
    status: cfg.statusValues.verified,
    note: `${filled.note}；未发现重复，可提交`,
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
