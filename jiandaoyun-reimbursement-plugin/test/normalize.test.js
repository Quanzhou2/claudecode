'use strict';

const test = require('node:test');
const assert = require('node:assert');
const {
  normalizeInvoiceNumber,
  invoiceDedupKey,
  normalizeAmount,
  normalizeDate,
} = require('../src/shared/normalize');

test('normalizeInvoiceNumber: 去空白/连字符并大写', () => {
  assert.strictEqual(normalizeInvoiceNumber('  044 001-9000 123 '), '0440019000123');
  assert.strictEqual(normalizeInvoiceNumber('abc123'), 'ABC123');
});

test('normalizeInvoiceNumber: 全角转半角', () => {
  assert.strictEqual(normalizeInvoiceNumber('１２３４５'), '12345');
});

test('normalizeInvoiceNumber: 空/无效返回空串', () => {
  assert.strictEqual(normalizeInvoiceNumber(null), '');
  assert.strictEqual(normalizeInvoiceNumber(undefined), '');
  assert.strictEqual(normalizeInvoiceNumber(''), '');
});

test('invoiceDedupKey: 组合代码+号码', () => {
  const k = invoiceDedupKey({ invoiceCode: '011002000111', invoiceNumber: '12345678' }, true);
  assert.strictEqual(k, '011002000111:12345678');
});

test('invoiceDedupKey: 数电票无代码时仅用号码', () => {
  const k = invoiceDedupKey({ invoiceNumber: '23312000000012345678' }, true);
  assert.strictEqual(k, '23312000000012345678');
});

test('invoiceDedupKey: alsoMatchCode=false 只用号码', () => {
  const k = invoiceDedupKey({ invoiceCode: '011002000111', invoiceNumber: '12345678' }, false);
  assert.strictEqual(k, '12345678');
});

test('normalizeAmount: 去￥与千分位', () => {
  assert.strictEqual(normalizeAmount('￥1,234.56'), 1234.56);
  assert.strictEqual(normalizeAmount(88.8), 88.8);
  assert.strictEqual(normalizeAmount(''), null);
  assert.strictEqual(normalizeAmount('abc'), null);
});

test('normalizeDate: 多格式统一为 YYYY-MM-DD', () => {
  assert.strictEqual(normalizeDate('20240131'), '2024-01-31');
  assert.strictEqual(normalizeDate('2024年1月3日'), '2024-01-03');
  assert.strictEqual(normalizeDate('2024-1-3'), '2024-01-03');
});
