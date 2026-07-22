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
