'use strict';

/**
 * 插件聚合导出。简道云部署时用到的是 dist/ 下的单文件打包版本；
 * 本文件方便本地按模块引用与测试。
 */

module.exports = {
  // 后端函数
  invoiceBackend: require('./invoice/invoiceBackend'),
  similarityBackend: require('./similarity/similarityBackend'),
  // 前端扩展
  invoiceFrontend: require('./frontend/invoiceFrontend'),
  attachmentFrontend: require('./frontend/attachmentFrontend'),
  submitValidation: require('./frontend/submitValidation'),
  // 纯逻辑
  dedup: require('./invoice/dedup'),
  similarity: require('./similarity/similarity'),
  imageHash: require('./similarity/imageHash'),
  normalize: require('./shared/normalize'),
  // 适配器 / 客户端
  ocrClient: require('./invoice/ocrClient'),
  verifyClient: require('./invoice/verifyClient'),
  llmSimilarityClient: require('./similarity/llmSimilarityClient'),
  jdyDataClient: require('./shared/jdyDataClient'),
  httpClient: require('./shared/httpClient'),
  records: require('./shared/records'),
  config: require('./shared/config'),
};
