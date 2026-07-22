'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { validateSubmission } = require('../src/frontend/submitValidation');

const opts = { statusWidget: 'st', noteWidget: 'nt', verifiedValue: '验证通过' };

test('validateSubmission: 全部通过则放行', () => {
  const rows = [{ st: '验证通过' }, { st: '验证通过' }];
  const r = validateSubmission(rows, opts);
  assert.strictEqual(r.pass, true);
});

test('validateSubmission: 有一行未通过则拦截并给出行号与原因', () => {
  const rows = [
    { st: '验证通过' },
    { st: '发票重复', nt: '号码 123 已报销过' },
  ];
  const r = validateSubmission(rows, opts);
  assert.strictEqual(r.pass, false);
  assert.strictEqual(r.badRowIndex, 1);
  assert.match(r.message, /第 2 行/);
  assert.match(r.message, /已报销过/);
});

test('validateSubmission: 空子表单放行', () => {
  assert.strictEqual(validateSubmission([], opts).pass, true);
});
