'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { createLlmSimilarityClient } = require('../src/similarity/llmSimilarityClient');

function fakeHttp(captured, response) {
  return {
    async postJson(url, body, opts) {
      captured.url = url;
      captured.body = body;
      captured.opts = opts;
      return response;
    },
  };
}

const openaiResponse = {
  choices: [
    { message: { content: '{"scores":[0.93],"bestIndex":0,"similarity":0.93,"sameDocument":true,"reason":"金额与流水号一致"}' } },
  ],
};

test('openai-compatible: 默认走 /chat/completions，Bearer 鉴权，图片为 data URL', async () => {
  const captured = {};
  const cfg = { provider: 'openai-compatible', model: 'gpt-4o', apiKey: 'sk-test', timeoutMs: 5000 };
  const client = createLlmSimilarityClient(cfg, fakeHttp(captured, openaiResponse), { debug() {} });

  const res = await client.compareBatch(
    { base64: 'AAA', mediaType: 'image/png' },
    [{ base64: 'BBB', mediaType: 'image/jpeg' }]
  );

  assert.strictEqual(captured.url, 'https://api.openai.com/v1/chat/completions');
  assert.strictEqual(captured.opts.headers.Authorization, 'Bearer sk-test');
  assert.strictEqual(captured.body.model, 'gpt-4o');
  const content = captured.body.messages[0].content;
  const imageParts = content.filter((c) => c.type === 'image_url');
  assert.strictEqual(imageParts.length, 2, '上传图 + 1 张历史图');
  assert.ok(imageParts[0].image_url.url.startsWith('data:image/png;base64,'));
  // 解析
  assert.strictEqual(res.similarity, 0.93);
  assert.strictEqual(res.sameDocument, true);
});

test('openai-compatible: 自定义 endpoint 生效', async () => {
  const captured = {};
  const cfg = {
    provider: 'openai-compatible',
    endpoint: 'https://my-llm.example.com/v1/chat/completions',
    model: 'qwen-vl-max',
    apiKey: 'k',
    timeoutMs: 5000,
  };
  const client = createLlmSimilarityClient(cfg, fakeHttp(captured, openaiResponse));
  await client.compareBatch({ base64: 'AAA' }, [{ base64: 'BBB' }]);
  assert.strictEqual(captured.url, 'https://my-llm.example.com/v1/chat/completions');
  assert.strictEqual(captured.body.model, 'qwen-vl-max');
});

test('无历史候选时不发请求', async () => {
  const captured = {};
  const cfg = { provider: 'openai-compatible', model: 'gpt-4o', apiKey: 'k' };
  const client = createLlmSimilarityClient(cfg, fakeHttp(captured, openaiResponse));
  const res = await client.compareBatch({ base64: 'AAA' }, []);
  assert.strictEqual(res.similarity, 0);
  assert.strictEqual(captured.url, undefined, '空候选不应调用 LLM');
});
