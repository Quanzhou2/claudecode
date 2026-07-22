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
