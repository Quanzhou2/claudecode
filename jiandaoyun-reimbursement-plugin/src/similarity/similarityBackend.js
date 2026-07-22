'use strict';

/**
 * 后端函数：付款凭证图片相似度查重。
 * 简道云自建插件「后端函数」入口：module.exports = async function(params, context)
 *
 * 流程：
 *   1) 拉取本次上传的凭证图片；
 *   2) 查询历史「已报销/审批通过」记录中同一附件字段的图片；
 *   3)（可选）感知哈希粗筛，缩小候选；
 *   4) 多模态 LLM 相似度分析，取最相似分值；
 *   5) 超过阈值判为重复，ok=false，供表单提交校验拦截。
 */

const { getConfig } = require('../shared/config');
const { createHttpClient } = require('../shared/httpClient');
const { createLogger } = require('../shared/logger');
const { createLlmSimilarityClient } = require('./llmSimilarityClient');
const { prefilterByHash, decideDuplicate } = require('./similarity');
const { dHashFromGrayGrid } = require('./imageHash');
const {
  createJdyDataClient,
  buildStatusFilter,
} = require('../shared/jdyDataClient');
const { collectVoucherImages } = require('../shared/records');

function chunk(arr, size) {
  const out = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

function guessMediaType(url) {
  const u = String(url).toLowerCase();
  if (u.includes('.png')) return 'image/png';
  if (u.includes('.webp')) return 'image/webp';
  if (u.includes('.gif')) return 'image/gif';
  return 'image/jpeg';
}

function arrayBufferToBase64(buf) {
  const b = Buffer.isBuffer(buf) ? buf : Buffer.from(buf);
  return b.toString('base64');
}

/**
 * 核心逻辑（依赖注入，便于测试）。
 * @param {object} params { imageUrl, dataId, rowId }
 * @param {object} deps { cfg, http, llm, dataClient, logger, decodeToGrayGrid? }
 */
async function runVoucherSimilarity(params, deps) {
  const { cfg, http, llm, dataClient, logger, decodeToGrayGrid } = deps;
  const S = cfg.statusValues;
  const sim = cfg.voucher.similarity;
  const threshold = sim.threshold;

  const base = {
    ok: false,
    status: S.pending,
    duplicate: false,
    similarity: 0,
    threshold,
    note: '',
    matchedRecord: null,
  };

  if (!params.imageUrl) {
    return { ...base, status: S.ocrFailed, note: '未取到上传的凭证图片地址' };
  }

  // 1) 拉取上传图
  let newImage;
  try {
    const buf = await http.getArrayBuffer(params.imageUrl, { timeoutMs: sim.timeoutMs });
    newImage = { base64: arrayBufferToBase64(buf), mediaType: guessMediaType(params.imageUrl), url: params.imageUrl };
  } catch (e) {
    logger.error('拉取上传凭证失败', e.message);
    return { ...base, status: S.ocrFailed, note: `读取上传凭证失败：${e.message}` };
  }

  // 2) 查询历史凭证图片
  let candidates = [];
  try {
    const dedupCfg = cfg.invoice.dedup; // 复用同一套「已报销」过滤
    const subformWidget = cfg.subform.widget;
    const attachmentWidget = cfg.subform.fields.voucherAttachment.widget;
    const recordNoWidget = cfg.main.fields.recordNo && cfg.main.fields.recordNo.widget;

    const records = await dataClient.queryRecords({
      fields: [subformWidget, recordNoWidget, dedupCfg.statusField].filter(
        (w) => w && !String(w).startsWith('FILL_')
      ),
      filter: buildStatusFilter(dedupCfg),
      limit: dedupCfg.pageSize,
      scanLimit: dedupCfg.scanLimit,
    });
    const groups = collectVoucherImages(records, {
      subformWidget,
      attachmentWidget,
      recordNoWidget,
    });
    // 拍平成单图候选，排除当前记录自身
    for (const g of groups) {
      if (cfg.runtime.excludeSelf && params.dataId && g.dataId === params.dataId) continue;
      for (const url of g.imageUrls) {
        candidates.push({ dataId: g.dataId, recordNo: g.recordNo, rowId: g.rowId, url });
      }
    }
  } catch (e) {
    logger.error('查询历史凭证失败', e.message);
    return { ...base, status: S.duplicateVoucher, note: `查重查询失败，暂不能提交：${e.message}` };
  }

  if (candidates.length === 0) {
    return { ...base, ok: true, status: S.verified, note: '无历史凭证可比对，通过' };
  }

  // 截断到 maxCandidates
  candidates = candidates.slice(0, sim.maxCandidates);

  // 3) 可选：感知哈希粗筛（需运行环境可解码图片）
  if (sim.prefilter && sim.prefilter.enabled && typeof decodeToGrayGrid === 'function') {
    try {
      const newHash = dHashFromGrayGrid(await decodeToGrayGrid(newImage));
      for (const c of candidates) {
        try {
          const buf = await http.getArrayBuffer(c.url, { timeoutMs: sim.timeoutMs });
          c._buf = buf;
          c.hash = dHashFromGrayGrid(await decodeToGrayGrid({ base64: arrayBufferToBase64(buf), url: c.url }));
        } catch (_e) {
          // 单张失败不阻塞，留待 LLM 复核
        }
      }
      candidates = prefilterByHash(newHash, candidates, {
        hammingMaxDistance: sim.prefilter.hammingMaxDistance,
        topK: sim.prefilter.topK,
      });
    } catch (e) {
      logger.warn('感知哈希粗筛失败，跳过粗筛', e.message);
    }
  }

  // 4) LLM 相似度分析（分批，每批 topK 张）
  const batchSize = (sim.prefilter && sim.prefilter.topK) || 8;
  const batches = chunk(candidates, batchSize);
  const scored = [];
  try {
    for (const batch of batches) {
      const imgs = [];
      for (const c of batch) {
        const buf = c._buf || (await http.getArrayBuffer(c.url, { timeoutMs: sim.timeoutMs }));
        imgs.push({ base64: arrayBufferToBase64(buf), mediaType: guessMediaType(c.url), url: c.url });
      }
      const res = await llm.compareBatch(newImage, imgs);
      (res.scores || []).forEach((s, i) => {
        if (batch[i]) scored.push({ similarity: s, candidate: batch[i], reason: res.reason });
      });
      // 提前命中阈值即可停止，省调用
      const hit = scored.find((x) => x.similarity >= threshold);
      if (hit) break;
    }
  } catch (e) {
    logger.error('LLM 相似度分析失败', e.message);
    return { ...base, status: S.duplicateVoucher, note: `相似度分析失败，暂不能提交：${e.message}` };
  }

  // 5) 判定
  const decision = decideDuplicate(scored, threshold);
  if (decision.duplicate) {
    const m = decision.matched || {};
    return {
      ...base,
      status: S.duplicateVoucher,
      duplicate: true,
      similarity: decision.maxSimilarity,
      matchedRecord: { dataId: m.dataId, recordNo: m.recordNo, rowId: m.rowId, imageUrl: m.url },
      note: `付款凭证疑似重复（相似度 ${(decision.maxSimilarity * 100).toFixed(0)}% ≥ 阈值 ${(threshold * 100).toFixed(0)}%）${
        m.recordNo ? `，命中历史报销单「${m.recordNo}」` : ''
      }：${decision.reason || ''}`,
    };
  }

  return {
    ...base,
    ok: true,
    status: S.verified,
    similarity: decision.maxSimilarity,
    note: `未发现重复凭证（最高相似度 ${(decision.maxSimilarity * 100).toFixed(0)}% < 阈值 ${(threshold * 100).toFixed(0)}%）`,
  };
}

/** 简道云后端函数入口。 */
async function main(params, _context) {
  const cfg = getConfig();
  const logger = createLogger(cfg.runtime.logLevel);
  const http = createHttpClient({ timeoutMs: cfg.voucher.similarity.timeoutMs });
  const llm = createLlmSimilarityClient(cfg.voucher.similarity, http, logger);
  const dataClient = createJdyDataClient(cfg, http);
  // decodeToGrayGrid 默认不注入（纯 JS 环境无法解码图片）；如运行环境装了 sharp/jimp
  // 可在此传入一个 async (img)=>number[][] 的解码器以启用感知哈希粗筛。
  return runVoucherSimilarity(params, { cfg, http, llm, dataClient, logger });
}

module.exports = main;
module.exports.main = main;
module.exports.runVoucherSimilarity = runVoucherSimilarity;
