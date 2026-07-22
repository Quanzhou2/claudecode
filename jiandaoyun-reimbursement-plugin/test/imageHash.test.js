'use strict';

const test = require('node:test');
const assert = require('node:assert');
const {
  dHashFromGrayGrid,
  hammingDistance,
  similarityFromHamming,
  bitsToHex,
} = require('../src/similarity/imageHash');

// 构造 8x9 灰度网格：左->右递增，dHash 每位都是 1 -> 全 f
function ascendingGrid() {
  const grid = [];
  for (let y = 0; y < 8; y++) {
    const row = [];
    for (let x = 0; x < 9; x++) row.push(x * 10);
    grid.push(row);
  }
  return grid;
}

test('dHashFromGrayGrid: 递增行 -> 全 1 位', () => {
  const h = dHashFromGrayGrid(ascendingGrid());
  assert.strictEqual(h, 'ffffffffffffffff'); // 64 位全 1 -> 16 个 f
});

test('bitsToHex: 基本正确', () => {
  assert.strictEqual(bitsToHex([1, 1, 1, 1]), 'f');
  assert.strictEqual(bitsToHex([0, 0, 0, 1]), '1');
  assert.strictEqual(bitsToHex([1, 0, 1, 0]), 'a');
});

test('hammingDistance: 相同为0，全反为位数', () => {
  assert.strictEqual(hammingDistance('ffffffffffffffff', 'ffffffffffffffff'), 0);
  assert.strictEqual(hammingDistance('ffffffffffffffff', '0000000000000000'), 64);
  assert.strictEqual(hammingDistance('f0', 'ff'), 4);
});

test('hammingDistance: 长度不一致抛错', () => {
  assert.throws(() => hammingDistance('ff', 'f'));
});

test('similarityFromHamming: 折算', () => {
  assert.strictEqual(similarityFromHamming(0, 64), 1);
  assert.strictEqual(similarityFromHamming(64, 64), 0);
  assert.strictEqual(similarityFromHamming(32, 64), 0.5);
});
