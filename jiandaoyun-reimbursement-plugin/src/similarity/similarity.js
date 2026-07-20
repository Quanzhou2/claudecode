'use strict';

const { hammingDistance } = require('./imageHash');

/**
 * 纯函数：付款凭证相似度判重的决策逻辑。自研部分。
 */

/**
 * 感知哈希粗筛：从历史候选中挑出与新图汉明距离较近的若干张，减少 LLM 调用。
 * @param {string} newHash 新图 dHash（可为空则不粗筛，全部返回，受 topK 限制）
 * @param {Array<{hash?:string}>} candidates
 * @param {object} opts { hammingMaxDistance=12, topK=8, hashBits=64 }
 * @returns {Array} 排序后的候选子集（附带 _hamming/_hashSim）
 */
function prefilterByHash(newHash, candidates, opts = {}) {
  const { hammingMaxDistance = 12, topK = 8, hashBits = 64 } = opts;
  if (!Array.isArray(candidates)) return [];

  if (!newHash) {
    // 无法计算新图哈希：不粗筛，仅按顺序截断，交给 LLM 判断
    return candidates.slice(0, topK);
  }

  const withDist = [];
  const noHash = [];
  for (const c of candidates) {
    if (c && typeof c.hash === 'string' && c.hash.length === newHash.length) {
      const d = hammingDistance(newHash, c.hash);
      withDist.push({ ...c, _hamming: d, _hashSim: 1 - d / hashBits });
    } else {
      noHash.push(c); // 缺哈希的候选无法粗筛，保留待 LLM 复核
    }
  }
  withDist.sort((a, b) => a._hamming - b._hamming);
  const near = withDist.filter((c) => c._hamming <= hammingMaxDistance);

  // 命中粗筛阈值的优先；不足 topK 时补充最接近的其余候选与无哈希候选
  const ordered = [
    ...near,
    ...withDist.filter((c) => c._hamming > hammingMaxDistance),
    ...noHash,
  ];
  return ordered.slice(0, topK);
}

/**
 * 依据打分结果判重。
 * @param {Array<{similarity:number, candidate:object, reason?:string}>} scored
 * @param {number} threshold
 * @returns {{duplicate:boolean, maxSimilarity:number, matched:object|null, reason:string}}
 */
function decideDuplicate(scored, threshold) {
  if (!Array.isArray(scored) || scored.length === 0) {
    return { duplicate: false, maxSimilarity: 0, matched: null, reason: '无历史可比对' };
  }
  let best = scored[0];
  for (const s of scored) {
    if ((s.similarity || 0) > (best.similarity || 0)) best = s;
  }
  const maxSimilarity = best.similarity || 0;
  return {
    duplicate: maxSimilarity >= threshold,
    maxSimilarity,
    matched: maxSimilarity >= threshold ? best.candidate : null,
    reason: best.reason || '',
  };
}

module.exports = { prefilterByHash, decideDuplicate };
