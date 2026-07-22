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
