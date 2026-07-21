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
