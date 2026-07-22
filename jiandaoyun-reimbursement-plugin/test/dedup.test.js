'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { dedupInvoice, buildInvoiceIndex, checkInvoiceDuplicate } = require('../src/invoice/dedup');

const history = [
  { dataId: 'A', recordNo: 'BX-001', rowId: 'r1', invoiceCode: '011002000111', invoiceNumber: '12345678' },
  { dataId: 'B', recordNo: 'BX-002', rowId: 'r2', invoiceCode: '', invoiceNumber: '23312000000012345678' },
];

test('dedupInvoice: 命中重复（代码+号码一致）', () => {
  const r = dedupInvoice({ invoiceCode: '011002000111', invoiceNumber: '12345678' }, history, { alsoMatchCode: true });
  assert.strictEqual(r.duplicate, true);
  assert.strictEqual(r.matched.recordNo, 'BX-001');
});

test('dedupInvoice: 号码不同不判重', () => {
  const r = dedupInvoice({ invoiceCode: '011002000111', invoiceNumber: '99999999' }, history, { alsoMatchCode: true });
  assert.strictEqual(r.duplicate, false);
});

test('dedupInvoice: 数电票仅号码命中', () => {
  const r = dedupInvoice({ invoiceNumber: '23312000000012345678' }, history, { alsoMatchCode: true });
  assert.strictEqual(r.duplicate, true);
  assert.strictEqual(r.matched.recordNo, 'BX-002');
});

test('dedupInvoice: 号码带空格/连字符也能归一命中', () => {
  const r = dedupInvoice({ invoiceCode: '011002000111', invoiceNumber: '1234-5678' }, history, { alsoMatchCode: true });
  assert.strictEqual(r.duplicate, true);
});

test('checkInvoiceDuplicate: excludeSelf 排除本记录', () => {
  const idx = buildInvoiceIndex(history, true);
  const r = checkInvoiceDuplicate(
    { invoiceCode: '011002000111', invoiceNumber: '12345678' },
    idx,
    { alsoMatchCode: true, selfDataId: 'A' }
  );
  assert.strictEqual(r.duplicate, false, '与自身记录比对不应判重');
});

test('checkInvoiceDuplicate: 历史仅登记号码时，候选带代码也能按号码回退命中', () => {
  const idx = buildInvoiceIndex(
    [{ dataId: 'C', recordNo: 'BX-003', rowId: 'r3', invoiceNumber: '55556666' }],
    true
  );
  const r = checkInvoiceDuplicate(
    { invoiceCode: '011002000111', invoiceNumber: '55556666' },
    idx,
    { alsoMatchCode: true }
  );
  assert.strictEqual(r.duplicate, true);
  assert.strictEqual(r.matched.recordNo, 'BX-003');
});

test('dedupInvoice: 空号码不判重', () => {
  const r = dedupInvoice({ invoiceNumber: '' }, history, { alsoMatchCode: true });
  assert.strictEqual(r.duplicate, false);
});
