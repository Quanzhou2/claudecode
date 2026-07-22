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
