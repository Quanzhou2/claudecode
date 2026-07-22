'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { runVoucherSimilarity } = require('../src/similarity/similarityBackend');
const { fakeConfig, silentLogger } = require('./helpers/fakeConfig');

function deps({ records, scoresByBatch }) {
  let call = 0;
  return {
    cfg: fakeConfig(),
    logger: silentLogger,
    http: { getArrayBuffer: async () => Buffer.from('fakeimg') },
    llm: {
      compareBatch: async (_newImg, cands) => {
        const scores = scoresByBatch[call] || cands.map(() => 0);
        call += 1;
        return { scores, bestIndex: 0, similarity: Math.max(0, ...scores), sameDocument: false, reason: 'test' };
      },
    },
    dataClient: { queryRecords: async () => records },
  };
}

test('voucher: 无历史凭证 -> ok=true', async () => {
  const d = deps({ records: [], scoresByBatch: [] });
  const r = await runVoucherSimilarity({ imageUrl: 'http://x/v.jpg' }, d);
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.status, '验证通过');
});

test('voucher: 相似度超阈值 -> ok=false, status=凭证重复', async () => {
  const records = [
    { _id: 'A', no: 'BX-001', sub: [{ _id: 'r1', att: [{ url: 'http://h/1.jpg' }] }] },
  ];
  const d = deps({ records, scoresByBatch: [[0.95]] });
  const r = await runVoucherSimilarity({ imageUrl: 'http://x/v.jpg', dataId: 'CUR' }, d);
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.status, '凭证重复');
  assert.strictEqual(r.duplicate, true);
  assert.strictEqual(r.matchedRecord.recordNo, 'BX-001');
  assert.ok(r.similarity >= 0.9);
});

test('voucher: 相似度低于阈值 -> ok=true', async () => {
  const records = [
    { _id: 'A', no: 'BX-001', sub: [{ _id: 'r1', att: [{ url: 'http://h/1.jpg' }] }] },
  ];
  const d = deps({ records, scoresByBatch: [[0.4]] });
  const r = await runVoucherSimilarity({ imageUrl: 'http://x/v.jpg', dataId: 'CUR' }, d);
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.status, '验证通过');
});

test('voucher: 排除当前记录自身的历史图片', async () => {
  const records = [
    { _id: 'CUR', no: 'BX-CUR', sub: [{ _id: 'r1', att: [{ url: 'http://h/self.jpg' }] }] },
  ];
  const d = deps({ records, scoresByBatch: [[0.99]] });
  const r = await runVoucherSimilarity({ imageUrl: 'http://x/v.jpg', dataId: 'CUR' }, d);
  assert.strictEqual(r.ok, true, '自身图片被排除后无可比对候选');
});

test('voucher: 多批比对，命中即停', async () => {
  // 12 张历史图，batchSize=topK=8 -> 需 2 批；第 1 批命中则不进入第 2 批
  const rows = [];
  for (let i = 0; i < 12; i++) rows.push({ _id: 'r' + i, att: [{ url: `http://h/${i}.jpg` }] });
  const records = [{ _id: 'A', no: 'BX-001', sub: rows }];
  const batch1 = new Array(8).fill(0);
  batch1[3] = 0.93;
  const d = deps({ records, scoresByBatch: [batch1, [0.99]] });
  const r = await runVoucherSimilarity({ imageUrl: 'http://x/v.jpg', dataId: 'CUR' }, d);
  assert.strictEqual(r.duplicate, true);
  assert.ok(Math.abs(r.similarity - 0.93) < 1e-9);
});

test('voucher: LLM 调用失败 -> 阻止提交', async () => {
  const records = [{ _id: 'A', no: 'BX-001', sub: [{ _id: 'r1', att: [{ url: 'http://h/1.jpg' }] }] }];
  const d = deps({ records, scoresByBatch: [] });
  d.llm.compareBatch = async () => { throw new Error('LLM 超时'); };
  const r = await runVoucherSimilarity({ imageUrl: 'http://x/v.jpg', dataId: 'CUR' }, d);
  assert.strictEqual(r.ok, false);
  assert.match(r.note, /相似度分析失败/);
});
