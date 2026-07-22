/*
 * 简道云 · 自建插件 · 后端函数：付款凭证图片相似度查重
 * =====================================================================
 * 这是一段“方法体”（不是模块）。请勿加 module.exports / require 本地文件。
 *
 * 安装：简道云 → 插件管理 → 新建自建插件 → 新建函数（类型：后端函数）→
 *       把本文件从「======= 方法体开始 =======」到末尾整段粘进代码框。
 *
 * 入参声明：imageUrl(必填) 付款凭证图片 URL；dataId(选填)；rowId(选填)
 * 出参声明：ok, status, duplicate, similarity, threshold, note, matchedRecord
 *
 * LLM 使用通用 OpenAI 兼容接口（POST /chat/completions，视觉消息，base64 图片）。
 * 运行时假设：方法体是 async；入参可通过 params 拿到；可用 fetch 或 axios。
 * =====================================================================
 */

var CONFIG = {
  jdy: {
    apiKey: 'FILL_简道云APIKey',
    appId: '68ca0e2fb59e070714b68aa0',
    entryId: '6899902c9582f683ab885f8d',
    listUrl: 'https://api.jiandaoyun.com/api/v5/app/entry/data/list'
  },
  fields: {
    subform: 'FILL_票据录入_widget',
    attachment: 'FILL_附件_widget',
    recordNo: 'FILL_报销单编号_widget',
    flowStatus: 'FILL_流程状态_widget'
  },
  dedup: { statusIncludes: ['已完成', '审批通过', '已报销'], scanLimit: 5000, pageSize: 100, excludeSelf: true },
  llm: {
    endpoint: 'https://api.xiaomimimo.com/v1/chat/completions', // 小米 MiMo，OpenAI 兼容；须用多模态版本
    apiKey: 'FILL_MiMo_APIKey',
    model: 'mimo-v2.5-pro',
    threshold: 0.9,
    maxCandidates: 60,
    batchSize: 8,
    timeoutMs: 30000
  },
  status: { verified: '验证通过', duplicateVoucher: '凭证重复', failed: '识别失败' }
};

var PROMPT = '你是付款凭证查重助手。第一张图片是本次上传的付款凭证，其余是历史已报销记录中的凭证。' +
  '请判断本次上传是否与其中某一张为“同一张凭证”（翻拍、重扫、截图、轻微裁剪或调色都算同一张；' +
  '仅版式相同但金额/单号/时间/收付款方不同不算）。只输出一个 JSON，不要解释。格式：' +
  '{"scores":[每张历史图与上传图的相似度0~1],"bestIndex":最相似下标,"similarity":最高相似度,"sameDocument":true或false,"reason":"简要中文理由"}';

// ============================ 方法体开始 ============================
var P = readParams();
return await runVoucherSimilarity(P);

function readParams() {
  var src = (typeof params !== 'undefined' && params) ? params : {};   // eslint-disable-line
  return { imageUrl: src.imageUrl, dataId: src.dataId, rowId: src.rowId };
}

async function runVoucherSimilarity(p) {
  var S = CONFIG.status, th = CONFIG.llm.threshold;
  var base = { ok: false, status: S.failed, duplicate: false, similarity: 0, threshold: th, note: '', matchedRecord: null };
  if (!p.imageUrl) return Object.assign(base, { note: '未取到上传的凭证图片地址' });

  // 1) 上传图 -> base64
  var newImage;
  try { newImage = await fetchImage(p.imageUrl); }
  catch (e) { return Object.assign(base, { note: '读取上传凭证失败：' + e.message }); }

  // 2) 历史凭证图片
  var candidates;
  try { candidates = await queryHistoryVouchers(p.dataId); }
  catch (e) { return Object.assign(base, { status: S.duplicateVoucher, note: '查重查询失败，暂不能提交：' + e.message }); }
  if (!candidates.length) return Object.assign(base, { ok: true, status: S.verified, note: '无历史凭证可比对，通过' });
  candidates = candidates.slice(0, CONFIG.llm.maxCandidates);

  // 3) LLM 分批比对，命中即停
  var best = { similarity: 0, candidate: null, reason: '' };
  try {
    for (var i = 0; i < candidates.length; i += CONFIG.llm.batchSize) {
      var batch = candidates.slice(i, i + CONFIG.llm.batchSize);
      var imgs = [];
      for (var j = 0; j < batch.length; j++) imgs.push(await fetchImage(batch[j].url));
      var res = await llmCompare(newImage, imgs);
      var scores = res.scores || [];
      for (var k = 0; k < batch.length; k++) {
        var s = Number(scores[k]) || 0;
        if (s > best.similarity) best = { similarity: s, candidate: batch[k], reason: res.reason || '' };
      }
      if (best.similarity >= th) break;
    }
  } catch (e) {
    return Object.assign(base, { status: S.duplicateVoucher, note: '相似度分析失败，暂不能提交：' + e.message });
  }

  // 4) 判定
  if (best.similarity >= th) {
    var m = best.candidate || {};
    return Object.assign(base, {
      status: S.duplicateVoucher, duplicate: true, similarity: best.similarity,
      matchedRecord: { dataId: m.dataId, recordNo: m.recordNo, rowId: m.rowId, imageUrl: m.url },
      note: '付款凭证疑似重复（相似度 ' + Math.round(best.similarity * 100) + '% ≥ 阈值 ' + Math.round(th * 100) + '%）' +
            (m.recordNo ? '，命中历史报销单「' + m.recordNo + '」' : '') + '：' + best.reason
    });
  }
  return Object.assign(base, { ok: true, status: S.verified, similarity: best.similarity, note: '未发现重复凭证（最高相似度 ' + Math.round(best.similarity * 100) + '% < 阈值 ' + Math.round(th * 100) + '%）' });
}

// ---------------- 历史数据 ----------------
async function queryHistoryVouchers(selfDataId) {
  var f = CONFIG.fields, d = CONFIG.dedup;
  var wanted = [f.subform, f.recordNo, f.flowStatus].filter(function (w) { return w && w.indexOf('FILL_') !== 0; });
  var filter;
  if (f.flowStatus && f.flowStatus.indexOf('FILL_') !== 0 && d.statusIncludes && d.statusIncludes.length) {
    filter = { rel: 'and', cond: [{ field: f.flowStatus, type: 'text', method: 'in', value: d.statusIncludes }] };
  }
  var records = [], cursor = '', limit = Math.min(d.pageSize || 100, 100);
  var maxPages = Math.ceil((d.scanLimit || 5000) / limit) + 1;
  for (var page = 0; page < maxPages; page++) {
    var reqBody = { app_id: CONFIG.jdy.appId, entry_id: CONFIG.jdy.entryId, limit: limit };
    if (wanted.length) reqBody.fields = wanted;
    if (filter) reqBody.filter = filter;
    if (cursor) reqBody.data_id = cursor;
    var resp = await httpPostJson(CONFIG.jdy.listUrl, reqBody, { 'Content-Type': 'application/json', Authorization: 'Bearer ' + CONFIG.jdy.apiKey }, 15000);
    var rows = (resp && resp.data) || [];
    for (var i = 0; i < rows.length; i++) { records.push(rows[i]); if (records.length >= (d.scanLimit || 5000)) break; }
    if (rows.length < limit) break;
    cursor = rows[rows.length - 1]._id; if (!cursor) break;
  }
  var out = [];
  for (var r = 0; r < records.length; r++) {
    var rec = records[r];
    if (d.excludeSelf && selfDataId && rec._id === selfDataId) continue;
    var sub = Array.isArray(rec[f.subform]) ? rec[f.subform] : [];
    for (var s = 0; s < sub.length; s++) {
      var urls = fileUrls(sub[s][f.attachment]);
      for (var u = 0; u < urls.length; u++) out.push({ dataId: rec._id, recordNo: f.recordNo ? rec[f.recordNo] : undefined, rowId: sub[s]._id, url: urls[u] });
    }
  }
  return out;
}
function fileUrls(v) {
  if (!v) return [];
  var arr = Array.isArray(v) ? v : [v], out = [];
  for (var i = 0; i < arr.length; i++) { var it = arr[i]; if (!it) continue; if (typeof it === 'string') out.push(it); else if (it.url) out.push(it.url); }
  return out;
}

// ---------------- LLM（OpenAI 兼容）----------------
async function llmCompare(newImage, candImages) {
  if (!candImages.length) return { scores: [], similarity: 0, sameDocument: false, reason: '' };
  var content = [{ type: 'text', text: PROMPT }];
  var all = [newImage].concat(candImages);
  for (var i = 0; i < all.length; i++) {
    content.push({ type: 'text', text: i === 0 ? '【上传图】' : '【历史图#' + (i - 1) + '】' });
    content.push({ type: 'image_url', image_url: { url: 'data:' + (all[i].mediaType || 'image/jpeg') + ';base64,' + all[i].base64 } });
  }
  var body = { model: CONFIG.llm.model, max_tokens: 500, temperature: 0, messages: [{ role: 'user', content: content }] };
  var raw = await httpPostJson(CONFIG.llm.endpoint, body, { 'Content-Type': 'application/json', Authorization: 'Bearer ' + CONFIG.llm.apiKey }, CONFIG.llm.timeoutMs);
  var text = (raw && raw.choices && raw.choices[0] && raw.choices[0].message && raw.choices[0].message.content) || '';
  return parseJson(text, candImages.length);
}
function parseJson(text, n) {
  var fb = { scores: new Array(n).fill(0), similarity: 0, sameDocument: false, reason: '模型未返回可解析结果' };
  if (!text) return fb;
  var m = String(text).match(/\{[\s\S]*\}/); if (!m) return fb;
  var obj; try { obj = JSON.parse(m[0]); } catch (e) { return fb; }
  var scores = Array.isArray(obj.scores) ? obj.scores.map(clamp01) : [];
  var sim = clamp01(obj.similarity); if (!sim && scores.length) sim = Math.max.apply(null, scores);
  return { scores: scores, similarity: sim, sameDocument: !!obj.sameDocument, reason: typeof obj.reason === 'string' ? obj.reason : '' };
}
function clamp01(n) { n = Number(n); if (!isFinite(n)) return 0; return Math.max(0, Math.min(1, n)); }

// ---------------- HTTP ----------------
async function fetchImage(url) {
  var buf, mt = guessMedia(url);
  if (typeof fetch === 'function') {
    var res = await fetch(url); if (!res.ok) throw new Error('HTTP ' + res.status);
    buf = Buffer.from(await res.arrayBuffer());
  } else {
    var axios = require('axios');
    var r = await axios({ url: url, method: 'GET', responseType: 'arraybuffer', timeout: CONFIG.llm.timeoutMs });
    buf = Buffer.from(r.data);
  }
  return { base64: buf.toString('base64'), mediaType: mt };
}
function guessMedia(url) {
  var u = String(url).toLowerCase();
  if (u.indexOf('.png') >= 0) return 'image/png';
  if (u.indexOf('.webp') >= 0) return 'image/webp';
  return 'image/jpeg';
}
async function httpPostJson(url, body, headers, timeoutMs) {
  if (typeof fetch === 'function') {
    var res = await fetch(url, { method: 'POST', headers: headers, body: JSON.stringify(body) });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  }
  var axios = require('axios');
  var r = await axios({ url: url, method: 'POST', headers: headers, data: body, timeout: timeoutMs, validateStatus: function () { return true; } });
  if (r.status < 200 || r.status >= 300) throw new Error('HTTP ' + r.status);
  return typeof r.data === 'string' ? JSON.parse(r.data) : r.data;
}
// ============================ 方法体结束 ============================
