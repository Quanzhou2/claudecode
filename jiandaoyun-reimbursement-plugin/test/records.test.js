'use strict';

const test = require('node:test');
const assert = require('node:assert');
const {
  extractFileUrls,
  getSubformRows,
  collectInvoiceEntries,
  collectVoucherImages,
} = require('../src/shared/records');

test('extractFileUrls: 数组对象/字符串/单对象', () => {
  assert.deepStrictEqual(extractFileUrls([{ url: 'a' }, { url: 'b' }]), ['a', 'b']);
  assert.deepStrictEqual(extractFileUrls('x'), ['x']);
  assert.deepStrictEqual(extractFileUrls({ url: 'y' }), ['y']);
  assert.deepStrictEqual(extractFileUrls(null), []);
});

test('getSubformRows: 非数组安全返回空', () => {
  assert.deepStrictEqual(getSubformRows({ sub: undefined }, 'sub'), []);
  assert.strictEqual(getSubformRows({ sub: [{ _id: '1' }] }, 'sub').length, 1);
});

test('collectInvoiceEntries: 展开所有行', () => {
  const records = [
    { _id: 'A', no: 'BX-001', sub: [{ _id: 'r1', num: '111', code: 'c1' }, { _id: 'r2', num: '222', code: 'c2' }] },
  ];
  const out = collectInvoiceEntries(records, {
    subformWidget: 'sub', numberWidget: 'num', codeWidget: 'code', recordNoWidget: 'no',
  });
  assert.strictEqual(out.length, 2);
  assert.deepStrictEqual(out[0], { dataId: 'A', recordNo: 'BX-001', rowId: 'r1', invoiceNumber: '111', invoiceCode: 'c1' });
});

test('collectVoucherImages: 仅收含图片的行', () => {
  const records = [
    { _id: 'A', no: 'BX-001', sub: [
      { _id: 'r1', att: [{ url: 'u1' }] },
      { _id: 'r2', att: [] },
    ] },
  ];
  const out = collectVoucherImages(records, { subformWidget: 'sub', attachmentWidget: 'att', recordNoWidget: 'no' });
  assert.strictEqual(out.length, 1);
  assert.deepStrictEqual(out[0].imageUrls, ['u1']);
});
