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
