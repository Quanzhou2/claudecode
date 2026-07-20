'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { mapOcrResult } = require('../src/invoice/ocrClient');
const { interpretVerify } = require('../src/invoice/verifyClient');

test('mapOcrResult: 百度 words_result 结构', () => {
  const raw = {
    words_result: {
      InvoiceCode: { words: '011002000111' },
      InvoiceNum: { words: '1234-5678' },
      InvoiceDate: { words: '2024年01月31日' },
      AmountWithTax: { words: '￥1,130.00' },
      TotalTax: { words: '130.00' },
    },
  };
  const m = mapOcrResult(raw, 'baidu');
  assert.strictEqual(m.invoiceCode, '011002000111');
  assert.strictEqual(m.invoiceNumber, '12345678');
  assert.strictEqual(m.invoiceDate, '2024-01-31');
  assert.strictEqual(m.amountWithTax, 1130.0);
  assert.strictEqual(m.recognized, true);
});

test('mapOcrResult: 通用中文键 + data 包裹', () => {
  const raw = { data: { 发票代码: '144', 发票号码: '99887766', 价税合计: '200', 销方税号: '91500' } };
  const m = mapOcrResult(raw, 'maomao');
  assert.strictEqual(m.invoiceNumber, '99887766');
  assert.strictEqual(m.sellerTaxNo, '91500');
  assert.strictEqual(m.amountWithTax, 200);
});

test('mapOcrResult: 无号码 -> recognized=false', () => {
  const m = mapOcrResult({ data: {} }, 'maomao');
  assert.strictEqual(m.recognized, false);
});

test('interpretVerify: 正常发票判真', () => {
  const r = interpretVerify({ code: 0, data: { invoiceStatus: '正常' } });
  assert.strictEqual(r.authentic, true);
});

test('interpretVerify: 作废发票判假', () => {
  const r = interpretVerify({ code: 0, data: { status: '已作废' } });
  assert.strictEqual(r.authentic, false);
});

test('interpretVerify: 查无此票判假', () => {
  const r = interpretVerify({ result: { checkResult: '查无此票' } });
  assert.strictEqual(r.authentic, false);
});
