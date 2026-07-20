'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { prefilterByHash, decideDuplicate } = require('../src/similarity/similarity');
const { parseModelJson } = require('../src/similarity/llmSimilarityClient');

test('prefilterByHash: 按汉明距离升序并截断 topK', () => {
  const newHash = '0000000000000000';
  const cands = [
    { id: 'far', hash: 'ffffffffffffffff' }, // dist 64
    { id: 'near', hash: '0000000000000001' }, // dist 1
    { id: 'mid', hash: '000000000000000f' }, // dist 4
  ];
  const out = prefilterByHash(newHash, cands, { hammingMaxDistance: 12, topK: 2 });
  assert.strictEqual(out.length, 2);
  assert.strictEqual(out[0].id, 'near');
  assert.strictEqual(out[1].id, 'mid');
});

test('prefilterByHash: 无新图哈希则不粗筛，仅截断', () => {
  const cands = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
  const out = prefilterByHash('', cands, { topK: 2 });
  assert.deepStrictEqual(out.map((c) => c.id), ['a', 'b']);
});

test('prefilterByHash: 缺哈希的候选也保留待 LLM 复核', () => {
  const out = prefilterByHash('0000000000000000', [{ id: 'x' }], { topK: 5 });
  assert.strictEqual(out.length, 1);
  assert.strictEqual(out[0].id, 'x');
});

test('decideDuplicate: 超过阈值判重并返回最相似项', () => {
  const scored = [
    { similarity: 0.4, candidate: { id: 'a' } },
    { similarity: 0.95, candidate: { id: 'b' } },
    { similarity: 0.6, candidate: { id: 'c' } },
  ];
  const d = decideDuplicate(scored, 0.9);
  assert.strictEqual(d.duplicate, true);
  assert.strictEqual(d.maxSimilarity, 0.95);
  assert.strictEqual(d.matched.id, 'b');
});

test('decideDuplicate: 未过阈值不判重', () => {
  const d = decideDuplicate([{ similarity: 0.5, candidate: { id: 'a' } }], 0.9);
  assert.strictEqual(d.duplicate, false);
  assert.strictEqual(d.matched, null);
});

test('decideDuplicate: 空结果安全', () => {
  const d = decideDuplicate([], 0.9);
  assert.strictEqual(d.duplicate, false);
  assert.strictEqual(d.maxSimilarity, 0);
});

test('parseModelJson: 从含噪输出中解析 JSON', () => {
  const text = '这是分析结果：{"scores":[0.1,0.92],"bestIndex":1,"similarity":0.92,"sameDocument":true,"reason":"金额单号一致"} 完毕';
  const p = parseModelJson(text, 2);
  assert.strictEqual(p.similarity, 0.92);
  assert.strictEqual(p.bestIndex, 1);
  assert.strictEqual(p.sameDocument, true);
});

test('parseModelJson: 无 similarity 时取 scores 最大值', () => {
  const p = parseModelJson('{"scores":[0.3,0.7,0.5]}', 3);
  assert.strictEqual(p.similarity, 0.7);
});

test('parseModelJson: 不可解析时安全兜底', () => {
  const p = parseModelJson('模型抽风了', 2);
  assert.deepStrictEqual(p.scores, [0, 0]);
  assert.strictEqual(p.similarity, 0);
  assert.strictEqual(p.sameDocument, false);
});

test('parseModelJson: 分值裁剪到 0~1', () => {
  const p = parseModelJson('{"scores":[1.5,-0.2],"similarity":1.5}', 2);
  assert.strictEqual(p.similarity, 1);
  assert.deepStrictEqual(p.scores, [1, 0]);
});
