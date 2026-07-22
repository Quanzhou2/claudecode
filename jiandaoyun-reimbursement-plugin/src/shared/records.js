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
