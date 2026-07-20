'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { runInvoiceGuard } = require('../src/invoice/invoiceBackend');
const { fakeConfig, silentLogger } = require('./helpers/fakeConfig');

function deps({ recognize, verify, records }) {
  return {
    cfg: fakeConfig(),
    logger: silentLogger,
    ocr: { recognize: async () => recognize },
    verify: { verify: async () => verify },
    dataClient: { queryRecords: async () => records },
  };
}

const goodInvoice = {
  recognized: true,
  invoiceType: '增值税专用发票',
  invoiceCode: '011002000111',
  invoiceNumber: '12345678',
  invoiceDate: '2024-01-31',
  invoiceAmount: 1000,
  taxAmount: 130,
  amountWithTax: 1130,
  checkCode: '1234',
  sellerTaxNo: '91500',
};

test('invoice: 识别失败 -> ok=false, status=识别失败', async () => {
  const d = deps({ recognize: { recognized: false, invoiceNumber: '' }, verify: {}, records: [] });
  const r = await runInvoiceGuard({ imageUrl: 'http://x/inv.jpg' }, d);
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.status, '识别失败');
});

test('invoice: 验真失败 -> ok=false, status=验真失败', async () => {
  const d = deps({
    recognize: goodInvoice,
    verify: { authentic: false, invoiceStatus: '作废', message: '发票已作废' },
    records: [],
  });
  const r = await runInvoiceGuard({ imageUrl: 'http://x/inv.jpg' }, d);
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.status, '验真失败');
  assert.strictEqual(r.invoiceNumber, '12345678', '失败也应回填已识别字段');
  assert.strictEqual(r.verifyCount, 1);
});

test('invoice: 命中历史重复 -> ok=false, status=发票重复', async () => {
  const d = deps({
    recognize: goodInvoice,
    verify: { authentic: true, invoiceStatus: '正常', message: 'ok' },
    records: [
      { _id: 'A', no: 'BX-001', sub: [{ _id: 'r1', num: '12345678', code: '011002000111' }] },
    ],
  });
  const r = await runInvoiceGuard({ imageUrl: 'http://x/inv.jpg', dataId: 'CUR' }, d);
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.status, '发票重复');
  assert.strictEqual(r.duplicate, true);
  assert.strictEqual(r.matchedRecord.recordNo, 'BX-001');
});

test('invoice: 全部通过 -> ok=true, status=验证通过', async () => {
  const d = deps({
    recognize: goodInvoice,
    verify: { authentic: true, invoiceStatus: '正常', message: 'ok' },
    records: [
      { _id: 'B', no: 'BX-002', sub: [{ _id: 'r2', num: '99999999', code: '011002000111' }] },
    ],
  });
  const r = await runInvoiceGuard({ imageUrl: 'http://x/inv.jpg', dataId: 'CUR' }, d);
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.status, '验证通过');
  assert.strictEqual(r.invoiceNumber, '12345678');
});

test('invoice: 编辑当前记录自身不判重', async () => {
  const d = deps({
    recognize: goodInvoice,
    verify: { authentic: true, invoiceStatus: '正常', message: 'ok' },
    records: [
      { _id: 'CUR', no: 'BX-CUR', sub: [{ _id: 'r1', num: '12345678', code: '011002000111' }] },
    ],
  });
  const r = await runInvoiceGuard({ imageUrl: 'http://x/inv.jpg', dataId: 'CUR' }, d);
  assert.strictEqual(r.ok, true, '与自身历史行比对不应判重');
});

test('invoice: 去重查询抛错 -> 阻止提交', async () => {
  const d = {
    cfg: fakeConfig(),
    logger: silentLogger,
    ocr: { recognize: async () => goodInvoice },
    verify: { verify: async () => ({ authentic: true, invoiceStatus: '正常', message: 'ok' }) },
    dataClient: { queryRecords: async () => { throw new Error('网络错误'); } },
  };
  const r = await runInvoiceGuard({ imageUrl: 'http://x/inv.jpg' }, d);
  assert.strictEqual(r.ok, false);
  assert.match(r.note, /去重查询失败/);
});
