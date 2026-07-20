'use strict';

/**
 * 纯函数：感知哈希（dHash）与汉明距离。用于 LLM 判重前的粗筛，
 * 把明显不相关的历史图片先排除，减少送入 LLM 的候选数量与成本。
 *
 * 说明：这里只负责「灰度网格 -> 哈希」这一纯计算部分；把图片解码成灰度网格
 * 由上层适配器（可选，依赖运行环境的图片解码能力）完成，因此本模块可离线单测。
 */

/**
 * dHash：比较每行相邻像素亮度，生成 rows*(cols-1) 位哈希。
 * 经典配置：网格 8 行 * 9 列 -> 64 位。
 * @param {number[][]} grid 灰度网格（每格 0~255）
 * @returns {string} 十六进制哈希串
 */
function dHashFromGrayGrid(grid) {
  if (!Array.isArray(grid) || !grid.length || !Array.isArray(grid[0])) {
    throw new Error('dHash: 需要二维灰度网格');
  }
  const bits = [];
  for (const row of grid) {
    for (let x = 0; x < row.length - 1; x++) {
      bits.push(row[x] < row[x + 1] ? 1 : 0);
    }
  }
  return bitsToHex(bits);
}

/** 位数组 -> 十六进制。不足 4 位的尾部按高位补零对齐。 */
function bitsToHex(bits) {
  let hex = '';
  for (let i = 0; i < bits.length; i += 4) {
    let nibble = 0;
    for (let j = 0; j < 4; j++) {
      nibble = (nibble << 1) | (bits[i + j] || 0);
    }
    hex += nibble.toString(16);
  }
  return hex;
}

const HEX_BITCOUNT = {
  0: 0, 1: 1, 2: 1, 3: 2, 4: 1, 5: 2, 6: 2, 7: 3,
  8: 1, 9: 2, a: 2, b: 3, c: 2, d: 3, e: 3, f: 4,
};

/**
 * 两个等长十六进制哈希的汉明距离。
 * @param {string} a
 * @param {string} b
 * @returns {number}
 */
function hammingDistance(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') {
    throw new Error('hamming: 需要字符串哈希');
  }
  if (a.length !== b.length) {
    throw new Error(`hamming: 哈希长度不一致 ${a.length} vs ${b.length}`);
  }
  let dist = 0;
  for (let i = 0; i < a.length; i++) {
    const x = parseInt(a[i], 16) ^ parseInt(b[i], 16);
    dist += HEX_BITCOUNT[x.toString(16)];
  }
  return dist;
}

/**
 * 由汉明距离折算的相似度 0~1。
 * @param {number} distance
 * @param {number} bits 哈希总位数（默认 64）
 */
function similarityFromHamming(distance, bits = 64) {
  if (bits <= 0) return 0;
  const s = 1 - distance / bits;
  return Math.max(0, Math.min(1, s));
}

module.exports = {
  dHashFromGrayGrid,
  bitsToHex,
  hammingDistance,
  similarityFromHamming,
};
