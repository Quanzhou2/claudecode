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
const { createLlmOcrClient } = require('./llmOcrClient');
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
  // 发票识别：默认多模态 LLM（llm/claude）；旧的第三方 OCR 接口按 provider 回退
  const ocrProvider = cfg.invoice.ocr.provider;
  const ocr = (ocrProvider === 'llm' || ocrProvider === 'claude')
    ? createLlmOcrClient(cfg.invoice.ocr, http, logger)
    : createOcrClient(cfg.invoice.ocr, http, logger);
  const verify = createVerifyClient(cfg.invoice.verify, http, logger);
  const dataClient = createJdyDataClient(cfg, http);
  return runInvoiceGuard(params, { cfg, ocr, verify, dataClient, logger });
}

module.exports = main;
module.exports.main = main;
module.exports.runInvoiceGuard = runInvoiceGuard;
