'use strict';

/**
 * 前端扩展：付款凭证附件字段变化时的处理。
 * api / cfg 约定同 invoiceFrontend.js。
 */

const { firstUrl } = require('./invoiceFrontend');

/**
 * @param {import('./invoiceFrontend').FrontApi} api
 * @param {object} cfg
 */
function createVoucherHandler(api, cfg) {
  const roles = cfg.subform.fields;

  /**
   * 附件（付款凭证）上传/变化时触发。
   * @param {object} evt { rowId, value }
   */
  return async function onVoucherChange(evt) {
    const rowId = evt.rowId;
    const imageUrl = firstUrl(evt.value) || api.getRowValue(rowId, 'voucherAttachment');
    if (!imageUrl) return;

    api.toast('正在比对付款凭证是否重复…', 'loading');

    let ret;
    try {
      ret = await api.invoke('voucherSimilarityCheck', {
        imageUrl,
        rowId,
        dataId: api.currentDataId ? api.currentDataId() : undefined,
      });
    } catch (e) {
      api.toast(`凭证查重失败：${e.message}`, 'error');
      api.setRowValues(rowId, {
        [roles.status.widget]: cfg.statusValues.ocrFailed,
        [roles.recognizeNote.widget]: String(e.message),
      });
      return;
    }

    // 把查重结论写入状态/说明（供提交校验拦截）
    api.setRowValues(rowId, {
      [roles.status.widget]: ret.duplicate ? cfg.statusValues.duplicateVoucher : ret.status,
      [roles.recognizeNote.widget]: ret.note,
    });

    if (ret.ok) {
      api.toast('付款凭证未发现重复', 'success');
    } else {
      api.toast(ret.note || '付款凭证疑似重复，无法提交', 'error');
    }
  };
}

module.exports = { createVoucherHandler };
