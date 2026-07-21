'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { createLlmOcrClient } = require('../src/invoice/llmOcrClient');

function fakeHttp(captured, responseText) {
  return {
    async getArrayBuffer(url) { captured.imageUrl = url; return Buffer.from('img-bytes'); },
    async postJson(url, body, opts) {
      captured.url = url; captured.body = body; captured.opts = opts;
      return { choices: [{ message: { content: responseText } }] };
    },
  };
}

test('llm-ocr: 默认 openai 兼容视觉接口，图片以 data URL 传入，解析发票字段', async () => {
  const captured = {};
  const cfg = { provider: 'llm', model: 'gpt-4o', apiKey: 'sk-x', endpoint: '', timeoutMs: 5000 };
  const respText = '{"invoiceType":"增值税专用发票","invoiceCode":"011002000111","invoiceNumber":"1234-5678","invoiceDate":"2024年01月31日","invoiceAmount":1000,"taxAmount":130,"amountWithTax":1130,"checkCode":"1234","sellerTaxNo":"91500"}';
  const client = createLlmOcrClient(cfg, fakeHttp(captured, respText), { debug() {} });

  const inv = await client.recognize('http://x/inv.png');

  assert.strictEqual(captured.url, 'https://api.openai.com/v1/chat/completions');
  assert.strictEqual(captured.opts.headers.Authorization, 'Bearer sk-x');
  const content = captured.body.messages[0].content;
  assert.ok(content.some((c) => c.type === 'image_url' && c.image_url.url.startsWith('data:image/png;base64,')));
  // 归一化后的结果
  assert.strictEqual(inv.recognized, true);
  assert.strictEqual(inv.invoiceNumber, '12345678');
  assert.strictEqual(inv.invoiceCode, '011002000111');
  assert.strictEqual(inv.invoiceDate, '2024-01-31');
  assert.strictEqual(inv.amountWithTax, 1130);
});

test('llm-ocr: 模型输出含说明文字也能抽出 JSON', async () => {
  const captured = {};
  const cfg = { provider: 'llm', model: 'gpt-4o', apiKey: 'sk-x', timeoutMs: 5000 };
  const respText = '识别结果如下：\n```json\n{"invoiceNumber":"99887766"}\n```\n完毕';
  const client = createLlmOcrClient(cfg, fakeHttp(captured, respText));
  const inv = await client.recognize('http://x/inv.jpg');
  assert.strictEqual(inv.invoiceNumber, '99887766');
  assert.strictEqual(inv.recognized, true);
});

test('llm-ocr: 无有效 JSON -> recognized=false', async () => {
  const captured = {};
  const cfg = { provider: 'llm', model: 'gpt-4o', apiKey: 'sk-x', timeoutMs: 5000 };
  const client = createLlmOcrClient(cfg, fakeHttp(captured, '这不是一张发票'));
  const inv = await client.recognize('http://x/inv.jpg');
  assert.strictEqual(inv.recognized, false);
});

test('llm-ocr: 缺少密钥抛错', async () => {
  const cfg = { provider: 'llm', model: 'gpt-4o', apiKey: '', timeoutMs: 5000 };
  const client = createLlmOcrClient(cfg, fakeHttp({}, '{}'));
  await assert.rejects(() => client.recognize('http://x/inv.jpg'), /未配置密钥/);
});

test('llm-ocr: claude 分支用 x-api-key 与 image source，并从 content 文本解析', async () => {
  const captured = {};
  const cfg = { provider: 'claude', model: 'claude-opus-4-8', apiKey: 'ck', endpoint: '', timeoutMs: 5000 };
  // claude 响应结构：content:[{type:'text',text:...}]
  const http = {
    async getArrayBuffer(url) { captured.imageUrl = url; return Buffer.from('img'); },
    async postJson(url, body, opts) {
      captured.url = url; captured.body = body; captured.opts = opts;
      return { content: [{ type: 'text', text: '{"invoiceNumber":"555"}' }] };
    },
  };
  const client = createLlmOcrClient(cfg, http);
  const inv = await client.recognize('http://x/inv.jpg');
  assert.strictEqual(captured.url, 'https://api.anthropic.com/v1/messages');
  assert.strictEqual(captured.opts.headers['x-api-key'], 'ck');
  assert.strictEqual(captured.body.messages[0].content[1].type, 'image');
  assert.strictEqual(inv.invoiceNumber, '555');
});
