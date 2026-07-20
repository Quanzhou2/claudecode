'use strict';

/**
 * 发票查验（验真）适配器：校验发票号码真伪与状态。
 *
 * 参考猫猫发票识别插件的验真方式：以发票代码/号码/开票日期/校验码或金额为查验要素，
 * 调用税务查验通道（如猫猫/诺诺/百望等）返回真伪与发票状态（正常/作废/红冲）。
 * 做成可插拔 provider，默认 maomao。
 */

/**
 * @param {object} verifyCfg cfg.invoice.verify
 * @param {object} http
 * @param {object} [logger]
 */
function createVerifyClient(verifyCfg, http, logger = console) {
  /**
   * @param {object} inv 归一化后的发票字段（ocrClient.recognize 的结果）
   * @returns {Promise<{authentic:boolean, invoiceStatus:string, message:string, raw:any}>}
   */
  async function verify(inv) {
    if (!verifyCfg.requireVerify) {
      return { authentic: true, invoiceStatus: '未查验', message: '未开启验真', raw: null };
    }
    if (!verifyCfg.endpoint) {
      throw new Error(
        '验真: 未配置查验服务地址（INVOICE_VERIFY_ENDPOINT）。'
      );
    }
    // 数电票通常无发票代码；查验要素以号码为主。
    if (!inv || !inv.invoiceNumber) {
      return {
        authentic: false,
        invoiceStatus: '要素缺失',
        message: '缺少发票号码，无法查验',
        raw: null,
      };
    }

    const headers = { 'Content-Type': 'application/json' };
    if (verifyCfg.appKey) headers['X-App-Key'] = verifyCfg.appKey;
    if (verifyCfg.appSecret) headers['X-App-Secret'] = verifyCfg.appSecret;

    const body = {
      invoiceCode: inv.invoiceCode || '',
      invoiceNumber: inv.invoiceNumber,
      invoiceDate: inv.invoiceDate || '',
      checkCode: inv.checkCode || '',
      amount: inv.invoiceAmount != null ? String(inv.invoiceAmount) : '',
    };

    const raw = await http.postJson(verifyCfg.endpoint, body, {
      headers,
      timeoutMs: verifyCfg.timeoutMs,
    });
    const result = interpretVerify(raw);
    if (logger && logger.debug) logger.debug('Verify result:', result);
    return { ...result, raw };
  }

  return { verify };
}

/**
 * 解释查验返回。兼容多种返回结构；判定 authentic 与发票状态。
 * @returns {{authentic:boolean, invoiceStatus:string, message:string}}
 */
function interpretVerify(raw) {
  const r = (raw && (raw.data || raw.result || raw.Response)) || raw || {};

  // 明确的成功/真伪标志
  const codeOk =
    r.code === 0 || r.code === '0000' || r.success === true || r.errcode === 0;

  // 常见状态字段：正常/作废/红冲/查无此票
  const statusRaw =
    r.invoiceStatus || r.status || r.state || r.checkResult || r.result || '';
  const statusStr = String(statusRaw);

  const abnormal = /作废|红冲|异常|查无|不一致|失败|无效/.test(statusStr);
  const normal = /正常|一致|已开|成功|验证通过|true/i.test(statusStr);

  let authentic;
  if (abnormal) authentic = false;
  else if (normal || codeOk) authentic = true;
  else authentic = Boolean(codeOk);

  const message =
    r.message || r.msg || r.desc || statusStr || (authentic ? '查验通过' : '查验未通过');

  return {
    authentic,
    invoiceStatus: statusStr || (authentic ? '正常' : '未知'),
    message: String(message),
  };
}

module.exports = { createVerifyClient, interpretVerify };
