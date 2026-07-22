'use strict';
/* 自动生成，请勿手改。源码见 src/similarity/similarityBackend.js。构建：npm run build */
var __modules = {};
var __cache = {};
function __load(id){
  if (__cache[id]) return __cache[id].exports;
  var module = { exports: {} };
  __cache[id] = module;
  __modules[id].call(module.exports, module, module.exports, __mkReq(id));
  return module.exports;
}
function __mkReq(fromId){
  var base = fromId.split('/').slice(0, -1);
  return function(spec){
    if (spec.charAt(0) !== '.') return require(spec);
    var parts = base.slice();
    spec.split('/').forEach(function(seg){
      if (seg === '.' || seg === '') return;
      if (seg === '..') parts.pop(); else parts.push(seg);
    });
    var id = parts.join('/');
    if (!__modules[id] && __modules[id + '.js']) id = id + '.js';
    if (!__modules[id] && __modules[id + '/index.js']) id = id + '/index.js';
    return __load(id);
  };
}
__modules["src/similarity/similarityBackend.js"] = function(module, exports, require){
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

};
__modules["src/shared/records.js"] = function(module, exports, require){
'use strict';

/**
 * 纯函数：从简道云记录里抽取子表单行、字段值、文件 URL。
 * JDY 记录结构：record[subformWidget] = [ { _id, <widget>: value, ... }, ... ]
 * 文件/图片字段值：[ { name, url }, ... ]
 */

/** 取记录主表某字段的原始值。 */
function getFieldValue(record, widget) {
  if (!record || !widget) return undefined;
  return record[widget];
}

/** 取子表单行数组。 */
function getSubformRows(record, subformWidget) {
  const v = record && record[subformWidget];
  return Array.isArray(v) ? v : [];
}

/** 从文件/图片字段值里抽取所有 url。value 可能是数组或单对象。 */
function extractFileUrls(value) {
  if (!value) return [];
  const arr = Array.isArray(value) ? value : [value];
  const urls = [];
  for (const item of arr) {
    if (!item) continue;
    if (typeof item === 'string') {
      urls.push(item);
    } else if (typeof item === 'object' && item.url) {
      urls.push(item.url);
    }
  }
  return urls;
}

/**
 * 收集历史记录里所有子表单行的发票号码信息。
 * @param {object[]} records
 * @param {object} map { subformWidget, numberWidget, codeWidget, recordNoWidget }
 * @returns {Array<{dataId, recordNo, rowId, invoiceNumber, invoiceCode}>}
 */
function collectInvoiceEntries(records, map) {
  const out = [];
  for (const rec of records) {
    const dataId = rec._id;
    const recordNo = map.recordNoWidget ? rec[map.recordNoWidget] : undefined;
    const rows = getSubformRows(rec, map.subformWidget);
    for (const row of rows) {
      out.push({
        dataId,
        recordNo,
        rowId: row._id,
        invoiceNumber: map.numberWidget ? row[map.numberWidget] : undefined,
        invoiceCode: map.codeWidget ? row[map.codeWidget] : undefined,
      });
    }
  }
  return out;
}

/**
 * 收集历史记录里所有子表单行的凭证附件图片。
 * @param {object[]} records
 * @param {object} map { subformWidget, attachmentWidget, recordNoWidget }
 * @returns {Array<{dataId, recordNo, rowId, imageUrls:string[]}>}
 */
function collectVoucherImages(records, map) {
  const out = [];
  for (const rec of records) {
    const dataId = rec._id;
    const recordNo = map.recordNoWidget ? rec[map.recordNoWidget] : undefined;
    const rows = getSubformRows(rec, map.subformWidget);
    for (const row of rows) {
      const imageUrls = extractFileUrls(row[map.attachmentWidget]);
      if (imageUrls.length) {
        out.push({ dataId, recordNo, rowId: row._id, imageUrls });
      }
    }
  }
  return out;
}

module.exports = {
  getFieldValue,
  getSubformRows,
  extractFileUrls,
  collectInvoiceEntries,
  collectVoucherImages,
};

};
__modules["src/shared/jdyDataClient.js"] = function(module, exports, require){
'use strict';

/**
 * 简道云数据接口客户端（v5）。用于查询「费用报销单」历史记录做去重比对。
 * 文档：查询多条数据 POST /api/v5/app/entry/data/list（游标分页，limit<=100）。
 *
 * 依赖注入 httpClient，便于测试。
 */

/**
 * @param {object} cfg getConfig() 的返回
 * @param {object} http createHttpClient() 的返回
 */
function createJdyDataClient(cfg, http) {
  const { appId, entryId, apiBase, apiVersion, apiKey } = cfg.dataset;
  const listUrl = `${apiBase}/${apiVersion}/app/entry/data/list`;

  function authHeaders() {
    return {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    };
  }

  /**
   * 分页拉取满足 filter 的记录。
   * @param {object} params
   * @param {string[]} [params.fields] 只取这些 widget 字段，减小体积
   * @param {object}   [params.filter] JDY filter 对象 { rel, cond:[...] }
   * @param {number}   [params.limit=100] 每页
   * @param {number}   [params.scanLimit=5000] 最多扫描总数
   * @returns {Promise<object[]>} 记录数组
   */
  async function queryRecords(params = {}) {
    const limit = Math.min(params.limit || 100, 100);
    const scanLimit = params.scanLimit || 5000;
    const out = [];
    let cursor = '';
    // 防御性：最多翻 (scanLimit/limit)+1 页
    const maxPages = Math.ceil(scanLimit / limit) + 1;
    for (let page = 0; page < maxPages; page++) {
      const body = {
        app_id: appId,
        entry_id: entryId,
        limit,
      };
      if (params.fields && params.fields.length) body.fields = params.fields;
      if (params.filter) body.filter = params.filter;
      if (cursor) body.data_id = cursor;

      const resp = await http.postJson(listUrl, body, {
        headers: authHeaders(),
      });
      const rows = (resp && resp.data) || [];
      for (const r of rows) {
        out.push(r);
        if (out.length >= scanLimit) return out;
      }
      if (rows.length < limit) break; // 最后一页
      cursor = rows[rows.length - 1]._id;
      if (!cursor) break;
    }
    return out;
  }

  return { queryRecords, listUrl };
}

/**
 * 构造「只查已报销/审批通过」的 filter。
 * @param {object} dedupCfg cfg.invoice.dedup
 * @returns {object|undefined}
 */
function buildStatusFilter(dedupCfg) {
  if (!dedupCfg || !dedupCfg.statusField || dedupCfg.statusField.startsWith('FILL_')) {
    return undefined; // 未配置流程状态字段则不加过滤
  }
  const includes = dedupCfg.statusIncludes || [];
  if (!includes.length) return undefined;
  return {
    rel: 'and',
    cond: [
      { field: dedupCfg.statusField, type: 'text', method: 'in', value: includes },
    ],
  };
}

module.exports = { createJdyDataClient, buildStatusFilter };

};
__modules["src/similarity/imageHash.js"] = function(module, exports, require){
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

};
__modules["src/similarity/similarity.js"] = function(module, exports, require){
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

};
__modules["src/similarity/llmSimilarityClient.js"] = function(module, exports, require){
'use strict';

/**
 * LLM 图片相似度分析客户端（多模态）。
 *
 * 把「新上传的付款凭证图片」与「若干历史凭证图片」一并发给多模态大模型，
 * 让模型判断新图是否与其中某张为同一张凭证（含翻拍/重扫/裁剪/轻微编辑），
 * 返回每张的相似度分值与最相似项。相较逐对调用，一次多图可显著降低调用次数与成本。
 *
 * provider：openai-compatible（默认，POST /chat/completions，视觉模型）| claude（Anthropic Messages API，可选）| custom
 * 依赖注入 http。
 */

const PROMPT = [
  '你是发票/付款凭证查重助手。第一张图片是用户本次上传的付款凭证，',
  '其余图片是历史已报销记录中的付款凭证。请判断本次上传是否与其中某一张为“同一张凭证”，',
  '包括翻拍、重新扫描、截图、轻微裁剪或调色等情况都应视为同一张。',
  '仅凭证类型或版式相同但内容（金额、单号、时间、收付款方等）不同，不算同一张。',
  '只输出一个 JSON，不要解释。格式：',
  '{"scores":[每张历史图片与上传图的相似度0~1], "bestIndex":最相似历史图的下标(从0开始), ',
  '"similarity":最高相似度, "sameDocument":是否判定为同一张(true/false), "reason":"简要中文理由"}',
].join('');

/**
 * @param {object} simCfg cfg.voucher.similarity
 * @param {object} http
 * @param {object} [logger]
 */
function createLlmSimilarityClient(simCfg, http, logger = console) {
  const provider = simCfg.provider || 'openai-compatible';

  /**
   * @param {{base64?:string, mediaType?:string, url?:string}} newImage
   * @param {Array<{base64?:string, mediaType?:string, url?:string}>} candidates
   * @returns {Promise<{scores:number[], bestIndex:number, similarity:number, sameDocument:boolean, reason:string}>}
   */
  async function compareBatch(newImage, candidates) {
    if (!candidates || candidates.length === 0) {
      return { scores: [], bestIndex: -1, similarity: 0, sameDocument: false, reason: '无历史图片' };
    }
    const { url, headers, body } = buildRequest(provider, simCfg, newImage, candidates);
    const raw = await http.postJson(url, body, { headers, timeoutMs: simCfg.timeoutMs });
    const text = extractText(provider, raw);
    const parsed = parseModelJson(text, candidates.length);
    if (logger && logger.debug) logger.debug('LLM similarity:', parsed);
    return parsed;
  }

  return { compareBatch };
}

function buildRequest(provider, simCfg, newImage, candidates) {
  const images = [newImage, ...candidates];

  // 可选：claude（Anthropic Messages API）
  if (provider === 'claude') {
    const content = [{ type: 'text', text: PROMPT }];
    images.forEach((img, i) => {
      content.push({ type: 'text', text: i === 0 ? '【上传图】' : `【历史图#${i - 1}】` });
      content.push({ type: 'image', source: toClaudeSource(img) });
    });
    return {
      url: simCfg.endpoint || 'https://api.anthropic.com/v1/messages',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': simCfg.apiKey,
        'anthropic-version': '2023-06-01',
      },
      body: {
        model: simCfg.model,
        max_tokens: 500,
        temperature: 0,
        messages: [{ role: 'user', content }],
      },
    };
  }

  // 默认：openai-compatible（POST /chat/completions，OpenAI 视觉消息格式）
  const content = [{ type: 'text', text: PROMPT }];
  images.forEach((img, i) => {
    content.push({ type: 'text', text: i === 0 ? '【上传图】' : `【历史图#${i - 1}】` });
    content.push({ type: 'image_url', image_url: { url: toDataUrl(img) } });
  });
  return {
    url: simCfg.endpoint || 'https://api.openai.com/v1/chat/completions',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${simCfg.apiKey}`,
    },
    body: {
      model: simCfg.model,
      max_tokens: 500,
      temperature: 0,
      messages: [{ role: 'user', content }],
    },
  };
}

function toClaudeSource(img) {
  if (img.base64) {
    return { type: 'base64', media_type: img.mediaType || 'image/jpeg', data: img.base64 };
  }
  return { type: 'url', url: img.url };
}

function toDataUrl(img) {
  if (img.base64) return `data:${img.mediaType || 'image/jpeg'};base64,${img.base64}`;
  return img.url;
}

function extractText(provider, raw) {
  if (!raw) return '';
  // claude（可选）
  if (provider === 'claude') {
    if (Array.isArray(raw.content)) {
      const t = raw.content.find((c) => c.type === 'text');
      return t ? t.text : '';
    }
    return raw.completion || '';
  }
  // 默认：openai-compatible（choices[0].message.content）
  return (
    raw.choices &&
    raw.choices[0] &&
    raw.choices[0].message &&
    raw.choices[0].message.content
  ) || '';
}

/** 从模型输出里稳健地解析 JSON。 */
function parseModelJson(text, candidateCount) {
  const fallback = {
    scores: new Array(candidateCount).fill(0),
    bestIndex: -1,
    similarity: 0,
    sameDocument: false,
    reason: '模型未返回可解析结果',
  };
  if (!text || typeof text !== 'string') return fallback;
  const m = text.match(/\{[\s\S]*\}/);
  if (!m) return fallback;
  let obj;
  try {
    obj = JSON.parse(m[0]);
  } catch (_e) {
    return fallback;
  }
  const scores = Array.isArray(obj.scores)
    ? obj.scores.map((n) => clamp01(Number(n)))
    : [];
  let similarity = clamp01(Number(obj.similarity));
  if (!similarity && scores.length) similarity = Math.max(...scores);
  let bestIndex =
    Number.isInteger(obj.bestIndex) ? obj.bestIndex : scores.indexOf(similarity);
  return {
    scores,
    bestIndex,
    similarity,
    sameDocument: Boolean(obj.sameDocument),
    reason: typeof obj.reason === 'string' ? obj.reason : '',
  };
}

function clamp01(n) {
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(1, n));
}

module.exports = { createLlmSimilarityClient, parseModelJson, PROMPT };

};
__modules["src/shared/logger.js"] = function(module, exports, require){
'use strict';

const LEVELS = { error: 0, warn: 1, info: 2, debug: 3 };

/**
 * 极简分级日志。简道云后端函数支持 console.*，执行日志里可见。
 * @param {string} level
 */
function createLogger(level = 'info') {
  const threshold = LEVELS[level] === undefined ? LEVELS.info : LEVELS[level];
  const emit = (lvl, method) => (...args) => {
    if (LEVELS[lvl] <= threshold) {
      // eslint-disable-next-line no-console
      (console[method] || console.log)(`[reimb-guard][${lvl}]`, ...args);
    }
  };
  return {
    error: emit('error', 'error'),
    warn: emit('warn', 'warn'),
    info: emit('info', 'log'),
    debug: emit('debug', 'log'),
  };
}

module.exports = { createLogger };

};
__modules["src/shared/httpClient.js"] = function(module, exports, require){
'use strict';

/**
 * 统一 HTTP 客户端：优先用运行时自带的 fetch（Node18+ / 简道云后端函数），
 * 无 fetch 时回退到 axios。内置超时 + 指数退避重试。
 *
 * 设计成可注入（tests 里传入 fakeFetch），避免真实网络。
 */

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function resolveFetch(injected) {
  if (injected) return injected;
  if (typeof fetch === 'function') return fetch;
  try {
    // 简道云后端函数环境通常内置 axios
    // eslint-disable-next-line global-require
    const axios = require('axios');
    return axiosAsFetch(axios);
  } catch (_e) {
    throw new Error(
      'No fetch available and axios not installed. Provide a fetch implementation.'
    );
  }
}

/** 把 axios 适配成 fetch-like，只覆盖本插件用到的能力。 */
function axiosAsFetch(axios) {
  return async function fetchLike(url, opts = {}) {
    const res = await axios({
      url,
      method: opts.method || 'GET',
      headers: opts.headers,
      data: opts.body,
      timeout: opts.timeoutMs,
      responseType: opts.responseType === 'arraybuffer' ? 'arraybuffer' : 'text',
      // axios 抛错 by default on non-2xx；这里统一交给上层判断
      validateStatus: () => true,
    });
    return {
      ok: res.status >= 200 && res.status < 300,
      status: res.status,
      async json() {
        return typeof res.data === 'string' ? JSON.parse(res.data) : res.data;
      },
      async text() {
        return typeof res.data === 'string' ? res.data : JSON.stringify(res.data);
      },
      async arrayBuffer() {
        return res.data;
      },
    };
  };
}

/**
 * @param {object} options
 * @param {number} [options.retries=2]
 * @param {number} [options.timeoutMs=15000]
 * @param {Function} [options.fetchImpl] 注入的 fetch（测试用）
 * @param {Function} [options.onRetry]
 */
function createHttpClient(options = {}) {
  const {
    retries = 2,
    timeoutMs = 15000,
    fetchImpl,
    onRetry = () => {},
  } = options;
  const doFetch = resolveFetch(fetchImpl);

  async function request(url, opts = {}) {
    const perCallTimeout = opts.timeoutMs || timeoutMs;
    let lastErr;
    for (let attempt = 0; attempt <= retries; attempt++) {
      const controller =
        typeof AbortController === 'function' ? new AbortController() : null;
      const timer = controller
        ? setTimeout(() => controller.abort(), perCallTimeout)
        : null;
      try {
        const res = await doFetch(url, {
          ...opts,
          timeoutMs: perCallTimeout,
          signal: controller ? controller.signal : undefined,
        });
        if (timer) clearTimeout(timer);
        // 5xx / 429 视为可重试
        if ((res.status >= 500 || res.status === 429) && attempt < retries) {
          lastErr = new Error(`HTTP ${res.status}`);
          throw lastErr;
        }
        return res;
      } catch (err) {
        if (timer) clearTimeout(timer);
        lastErr = err;
        if (attempt < retries) {
          const backoff = 2 ** attempt * 500;
          onRetry(attempt + 1, err);
          await sleep(backoff);
          continue;
        }
        throw lastErr;
      }
    }
    throw lastErr;
  }

  async function getJson(url, opts = {}) {
    const res = await request(url, { ...opts, method: opts.method || 'GET' });
    if (!res.ok) {
      const body = await safeText(res);
      throw new Error(`GET ${url} -> ${res.status} ${body.slice(0, 300)}`);
    }
    return res.json();
  }

  async function postJson(url, payload, opts = {}) {
    const res = await request(url, {
      ...opts,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = await safeText(res);
      throw new Error(`POST ${url} -> ${res.status} ${body.slice(0, 300)}`);
    }
    return res.json();
  }

  async function getArrayBuffer(url, opts = {}) {
    const res = await request(url, {
      ...opts,
      method: 'GET',
      responseType: 'arraybuffer',
    });
    if (!res.ok) throw new Error(`GET(bin) ${url} -> ${res.status}`);
    return res.arrayBuffer();
  }

  return { request, getJson, postJson, getArrayBuffer };
}

async function safeText(res) {
  try {
    return await res.text();
  } catch (_e) {
    return '';
  }
}

module.exports = { createHttpClient, sleep };

};
__modules["src/shared/config.js"] = function(module, exports, require){
'use strict';

const fs = require('fs');
const path = require('path');

/**
 * 加载 plugin.config.json 并解析密钥（通过 *Env 指向的环境变量读取）。
 * 简道云自建插件里，密钥应放在插件的「密钥/环境变量」配置项中，这里统一从 env 读取。
 */

let cached = null;
let embedded = null;

/**
 * 供打包版本使用：把 plugin.config.json 的内容直接内联进来，
 * 避免运行时读文件（简道云代码框里没有文件系统）。
 */
function setEmbeddedConfig(raw) {
  embedded = raw;
  cached = null;
}

function loadRawConfig(configPath) {
  if (embedded && !configPath) return embedded;
  const p =
    configPath || path.join(__dirname, '..', '..', 'plugin.config.json');
  const text = fs.readFileSync(p, 'utf8');
  return JSON.parse(text);
}

function env(name, fallback = '') {
  return process.env[name] !== undefined ? process.env[name] : fallback;
}

/**
 * 返回带密钥解析的配置对象。可传入 overrides（测试注入）。
 * @param {object} [opts]
 * @param {string} [opts.configPath]
 * @param {object} [opts.raw] 直接注入配置对象（跳过读文件）
 * @param {object} [opts.env] 注入 env 映射
 */
function getConfig(opts = {}) {
  if (cached && !opts.raw && !opts.configPath && !opts.env) return cached;
  const raw = opts.raw || loadRawConfig(opts.configPath);
  const readEnv = (name, fallback) =>
    opts.env ? (opts.env[name] !== undefined ? opts.env[name] : fallback) : env(name, fallback);

  const cfg = {
    raw,
    dataset: {
      appId: raw.dataset.appId,
      entryId: raw.dataset.entryId,
      apiBase: raw.dataset.apiBase,
      apiVersion: raw.dataset.apiVersion,
      apiKey: readEnv(raw.dataset.apiKeyEnv, ''),
    },
    main: raw.main || { fields: {} },
    subform: raw.subform,
    statusValues: raw.statusValues,
    invoice: {
      ocr: {
        ...raw.invoice.ocr,
        endpoint: readEnv(raw.invoice.ocr.endpointEnv, ''),
        apiKey: readEnv(raw.invoice.ocr.apiKeyEnv, ''),
        appKey: readEnv(raw.invoice.ocr.appKeyEnv, ''),
        appSecret: readEnv(raw.invoice.ocr.appSecretEnv, ''),
      },
      verify: {
        ...raw.invoice.verify,
        endpoint: readEnv(raw.invoice.verify.endpointEnv, ''),
        appKey: readEnv(raw.invoice.verify.appKeyEnv, ''),
        appSecret: readEnv(raw.invoice.verify.appSecretEnv, ''),
      },
      dedup: raw.invoice.dedup,
    },
    voucher: {
      similarity: {
        ...raw.voucher.similarity,
        endpoint: readEnv(raw.voucher.similarity.endpointEnv, ''),
        apiKey: readEnv(raw.voucher.similarity.apiKeyEnv, ''),
      },
    },
    runtime: raw.runtime,
  };

  if (!opts.raw && !opts.configPath && !opts.env) cached = cfg;
  return cfg;
}

/** 便捷取子表单字段的 widget id。 */
function fieldWidget(cfg, role) {
  const f = cfg.subform.fields[role];
  return f ? f.widget : undefined;
}

module.exports = { getConfig, fieldWidget, loadRawConfig, setEmbeddedConfig };

};
__load("src/shared/config.js").setEmbeddedConfig({
  "$comment": "简道云自建插件运行配置。部署时把 <FILL_*> 占位符替换为真实值。字段 role -> _widget_ 的映射既可以在此处填写（供后端函数按 role 读写），也可以直接在简道云「前端事件 >> 字段存储关系」里配置回填。",

  "dataset": {
    "$comment": "费用报销单本身所在的应用/表单，用于查询历史已报销记录做去重。app_id / entry_id 已从上传的表单地址中解析。",
    "appId": "68ca0e2fb59e070714b68aa0",
    "entryId": "6899902c9582f683ab885f8d",
    "apiBase": "https://api.jiandaoyun.com/api",
    "apiVersion": "v5",
    "apiKeyEnv": "JDY_API_KEY"
  },

  "main": {
    "$comment": "主表（费用报销单本身）字段映射，供去重结果里回显命中的历史报销单。",
    "fields": {
      "recordNo":    { "label": "报销单编号", "widget": "FILL_报销单编号_widget", "type": "text" },
      "flowStatus":  { "label": "流程状态",   "widget": "FILL_流程状态_widget",   "type": "text" }
    }
  },

  "subform": {
    "$comment": "发票信息子表单（表单里名为「票据录入」）的字段 role -> widget 映射。",
    "widget": "FILL_票据录入_widget",
    "fields": {
      "invoiceImage":   { "label": "发票",     "widget": "FILL_发票_widget",     "type": "image" },
      "invoiceType":    { "label": "发票类型", "widget": "FILL_发票类型_widget", "type": "text" },
      "invoiceCode":    { "label": "发票代码", "widget": "FILL_发票代码_widget", "type": "text" },
      "invoiceNumber":  { "label": "发票号码", "widget": "FILL_发票号码_widget", "type": "text" },
      "invoiceDate":    { "label": "票据日期", "widget": "FILL_票据日期_widget", "type": "text" },
      "invoiceAmount":  { "label": "发票金额", "widget": "FILL_发票金额_widget", "type": "number" },
      "taxAmount":      { "label": "税额",     "widget": "FILL_税额_widget",     "type": "number" },
      "amountWithTax":  { "label": "价税合计", "widget": "FILL_价税合计_widget", "type": "number" },
      "checkCode":      { "label": "校验码",   "widget": "FILL_校验码_widget",   "type": "text" },
      "sellerTaxNo":    { "label": "销方税号", "widget": "FILL_销方税号_widget", "type": "text" },
      "status":         { "label": "状态",     "widget": "FILL_状态_widget",     "type": "text" },
      "verifyCount":    { "label": "查验次数", "widget": "FILL_查验次数_widget", "type": "number" },
      "recognizeNote":  { "label": "识别说明", "widget": "FILL_识别说明_widget", "type": "text" },
      "voucherAttachment": { "label": "附件",  "widget": "FILL_附件_widget",     "type": "upload" }
    }
  },

  "statusValues": {
    "$comment": "写回「状态」字段的枚举值。表单提交校验只放行 verified。",
    "pending": "待验证",
    "verified": "验证通过",
    "duplicateInvoice": "发票重复",
    "verifyFailed": "验真失败",
    "duplicateVoucher": "凭证重复",
    "ocrFailed": "识别失败"
  },

  "invoice": {
    "ocr": {
      "$comment": "发票识别：默认用多模态 LLM（OpenAI 兼容视觉接口）直接从图片抽取发票要素。provider 取值：llm(默认，openai-compatible) | claude | maomao|baidu|tencent|huawei|custom(旧的第三方OCR接口)。llm 模式用 endpoint/apiKey/model；旧模式用 endpoint/appKey/appSecret。",
      "provider": "llm",
      "endpointEnv": "LLM_OCR_ENDPOINT",
      "apiKeyEnv": "LLM_OCR_API_KEY",
      "model": "gpt-4o",
      "appKeyEnv": "INVOICE_OCR_APP_KEY",
      "appSecretEnv": "INVOICE_OCR_APP_SECRET",
      "timeoutMs": 20000
    },
    "verify": {
      "$comment": "发票查验（验真）提供方，校验发票号码真伪与状态。provider：maomao | nuonuo | baiwang | custom。",
      "provider": "maomao",
      "endpointEnv": "INVOICE_VERIFY_ENDPOINT",
      "appKeyEnv": "INVOICE_VERIFY_APP_KEY",
      "appSecretEnv": "INVOICE_VERIFY_APP_SECRET",
      "timeoutMs": 15000,
      "requireVerify": true
    },
    "dedup": {
      "$comment": "去重范围：只与「已报销/审批通过」的历史记录比对。statusFilter 为流程状态字段与放行值；scanLimit 为最多扫描的历史记录数。",
      "statusField": "FILL_流程状态_widget",
      "statusIncludes": ["已完成", "审批通过", "已报销"],
      "scanLimit": 5000,
      "pageSize": 100,
      "matchOn": ["invoiceNumber"],
      "alsoMatchCode": true
    }
  },

  "voucher": {
    "similarity": {
      "$comment": "付款凭证图片相似度分析（LLM 多模态，OpenAI 兼容接口）。provider：openai-compatible | custom。endpoint 指向兼容 /chat/completions 的服务，model 用其提供的视觉模型名。threshold 超过则判重。",
      "provider": "openai-compatible",
      "endpointEnv": "LLM_SIMILARITY_ENDPOINT",
      "apiKeyEnv": "LLM_SIMILARITY_API_KEY",
      "model": "gpt-4o",
      "threshold": 0.9,
      "timeoutMs": 30000,
      "maxCandidates": 60,
      "prefilter": {
        "$comment": "感知哈希（dHash）预筛，先用汉明距离粗筛，减少送入 LLM 的候选数量。启用需运行环境能解码图片为灰度网格。",
        "enabled": true,
        "hammingMaxDistance": 12,
        "topK": 8
      }
    }
  },

  "runtime": {
    "$comment": "现场记录编辑时，需排除当前记录自身，避免与自己比对。dataIdParam 为前端事件传入当前 dataId 的请求参数名。",
    "excludeSelf": true,
    "logLevel": "info"
  }
}
);
var __entry = __load("src/similarity/similarityBackend.js");
module.exports = __entry;
if (typeof module.exports === 'function') { module.exports.main = module.exports.main || module.exports; }
