'use strict';

const { mapOcrResult } = require('./ocrClient');

/**
 * 基于多模态 LLM 的发票识别（OCR）。
 *
 * 把发票图片交给视觉大模型，直接抽取发票要素并以 JSON 返回，替代传统 OCR 接口。
 * 走通用 OpenAI 兼容接口（POST /chat/completions，视觉消息，base64 图片）；claude 作为可选分支。
 * 解析出的对象复用 ocrClient.mapOcrResult 做字段归一化（兼容中英文键）。
 *
 * 依赖注入 http（需 getArrayBuffer + postJson）。
 */

const OCR_PROMPT = [
  '你是发票识别助手。请从这张发票图片中提取关键信息，只输出一个 JSON，不要解释、不要代码块围栏。',
  '字段（识别不到就留空字符串或 null）：',
  '{"invoiceType":"发票类型","invoiceCode":"发票代码","invoiceNumber":"发票号码",',
  '"invoiceDate":"开票日期(YYYY-MM-DD)","invoiceAmount":不含税金额数字,"taxAmount":税额数字,',
  '"amountWithTax":价税合计数字,"checkCode":"校验码","sellerTaxNo":"销方税号"}',
].join('');

function createLlmOcrClient(ocrCfg, http, logger = console) {
  const provider = ocrCfg.provider === 'claude' ? 'claude' : 'openai-compatible';

  async function recognize(imageUrl) {
    if (!imageUrl) throw new Error('OCR: imageUrl 为空');
    if (!ocrCfg.apiKey) {
      throw new Error('LLM OCR: 未配置密钥（LLM_OCR_API_KEY）。');
    }
    const buf = await http.getArrayBuffer(imageUrl, { timeoutMs: ocrCfg.timeoutMs });
    const image = {
      base64: Buffer.from(buf).toString('base64'),
      mediaType: guessMedia(imageUrl),
      url: imageUrl,
    };
    const req = buildRequest(provider, ocrCfg, image);
    const raw = await http.postJson(req.url, req.body, {
      headers: req.headers,
      timeoutMs: ocrCfg.timeoutMs,
    });
    const text = extractText(provider, raw);
    const obj = parseJsonObject(text);
    if (logger && logger.debug) logger.debug('LLM OCR parsed:', obj);
    return mapOcrResult(obj, 'llm');
  }

  return { recognize };
}

function buildRequest(provider, cfg, image) {
  if (provider === 'claude') {
    const source = { type: 'base64', media_type: image.mediaType, data: image.base64 };
    return {
      url: cfg.endpoint || 'https://api.anthropic.com/v1/messages',
      headers: { 'Content-Type': 'application/json', 'x-api-key': cfg.apiKey, 'anthropic-version': '2023-06-01' },
      body: {
        model: cfg.model, max_tokens: 800, temperature: 0,
        messages: [{ role: 'user', content: [{ type: 'text', text: OCR_PROMPT }, { type: 'image', source }] }],
      },
    };
  }
  const dataUrl = `data:${image.mediaType};base64,${image.base64}`;
  return {
    url: cfg.endpoint || 'https://api.openai.com/v1/chat/completions',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${cfg.apiKey}` },
    body: {
      model: cfg.model, max_tokens: 800, temperature: 0,
      messages: [{ role: 'user', content: [{ type: 'text', text: OCR_PROMPT }, { type: 'image_url', image_url: { url: dataUrl } }] }],
    },
  };
}

function extractText(provider, raw) {
  if (!raw) return '';
  if (provider === 'claude') {
    if (Array.isArray(raw.content)) {
      const t = raw.content.find((c) => c.type === 'text');
      return t ? t.text : '';
    }
    return raw.completion || '';
  }
  return (raw.choices && raw.choices[0] && raw.choices[0].message && raw.choices[0].message.content) || '';
}

function parseJsonObject(text) {
  if (!text || typeof text !== 'string') return {};
  const m = text.match(/\{[\s\S]*\}/);
  if (!m) return {};
  try {
    return JSON.parse(m[0]);
  } catch (_e) {
    return {};
  }
}

function guessMedia(url) {
  const u = String(url).toLowerCase();
  if (u.includes('.png')) return 'image/png';
  if (u.includes('.webp')) return 'image/webp';
  return 'image/jpeg';
}

module.exports = { createLlmOcrClient, OCR_PROMPT, parseJsonObject };
