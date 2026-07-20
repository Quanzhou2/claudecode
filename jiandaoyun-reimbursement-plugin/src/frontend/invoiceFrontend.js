'use strict';

/**
 * 前端扩展：发票字段变化时的处理。
 *
 * 说明：简道云不同版本前端扩展的上下文 API 名称可能不同，这里把与平台耦合的能力
 * 抽象成一个 `api` 适配对象（见下方 JSDoc）。部署时按你所在版本的前端事件上下文
 * 实现这几个方法即可（大多可直接映射到「前端事件」提供的入参/回调）。
 *
 * @typedef {Object} FrontApi
 * @property {(rowId:string, role:string)=>any}       getRowValue  取子表单当前行某 role 字段值
 * @property {(rowId:string, patch:object)=>void}     setRowValues 按 role 批量回填当前行字段
 * @property {(fnKey:string, params:object)=>Promise} invoke       调用后端函数（返回其 returnParams）
 * @property {(msg:string, type?:string)=>void}       toast        顶部消息提示
 * @property {()=>string}                             currentDataId 当前记录 dataId（编辑态）
 */

/**
 * 生成发票字段变化处理器。
 * @param {FrontApi} api
 * @param {object} cfg 已解析配置（getConfig 的结果或其精简子集）
 */
function createInvoiceHandler(api, cfg) {
  const roles = cfg.subform.fields;

  /**
   * 发票图片上传/变化时触发。
   * @param {object} evt { rowId, value } value 为图片字段值（数组或 url）
   */
  return async function onInvoiceImageChange(evt) {
    const rowId = evt.rowId;
    const imageUrl = firstUrl(evt.value) || api.getRowValue(rowId, 'invoiceImage');
    if (!imageUrl) return;

    api.toast('正在识别并查验发票…', 'loading');

    const priorVerifyCount = Number(api.getRowValue(rowId, 'verifyCount')) || 0;
    let ret;
    try {
      ret = await api.invoke('invoiceRecognizeVerifyDedup', {
        imageUrl,
        rowId,
        dataId: api.currentDataId ? api.currentDataId() : undefined,
        priorVerifyCount,
      });
    } catch (e) {
      api.toast(`发票处理失败：${e.message}`, 'error');
      api.setRowValues(rowId, { [roles.status.widget]: cfg.statusValues.ocrFailed, [roles.recognizeNote.widget]: String(e.message) });
      return;
    }

    // 回填识别结果（若已在「字段存储关系」里配置回填，可省略此步）
    api.setRowValues(rowId, {
      [roles.invoiceType.widget]: ret.invoiceType,
      [roles.invoiceCode.widget]: ret.invoiceCode,
      [roles.invoiceNumber.widget]: ret.invoiceNumber,
      [roles.invoiceDate.widget]: ret.invoiceDate,
      [roles.invoiceAmount.widget]: ret.invoiceAmount,
      [roles.taxAmount.widget]: ret.taxAmount,
      [roles.amountWithTax.widget]: ret.amountWithTax,
      [roles.checkCode.widget]: ret.checkCode,
      [roles.sellerTaxNo.widget]: ret.sellerTaxNo,
      [roles.verifyCount.widget]: ret.verifyCount,
      [roles.status.widget]: ret.status,
      [roles.recognizeNote.widget]: ret.note,
    });

    if (ret.ok) {
      api.toast('发票验真通过，未发现重复', 'success');
    } else {
      // 失败提示（阻止提交由「表单提交校验」保证：状态需为验证通过）
      api.toast(ret.note || '发票校验未通过，无法提交', 'error');
    }
  };
}

function firstUrl(value) {
  if (!value) return '';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    const item = value[0];
    if (!item) return '';
    return typeof item === 'string' ? item : item.url || '';
  }
  if (typeof value === 'object' && value.url) return value.url;
  return '';
}

module.exports = { createInvoiceHandler, firstUrl };
