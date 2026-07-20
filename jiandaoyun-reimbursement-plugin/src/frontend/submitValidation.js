'use strict';

/**
 * 纯函数：表单提交校验判定。
 *
 * 简道云在「表单设计 >> 表单属性 >> 表单提交校验」处可写自定义校验；也可在前端扩展里
 * 于提交前调用本判定。逻辑：子表单每一行的「状态」字段都必须等于「验证通过」，
 * 否则阻止提交，并给出第一条不通过原因。
 */

/**
 * @param {Array<object>} subformRows 子表单行数组，每行含 状态/识别说明 字段
 * @param {object} opts { statusWidget, noteWidget, verifiedValue }
 * @returns {{pass:boolean, message:string, badRowIndex:number}}
 */
function validateSubmission(subformRows, opts) {
  const { statusWidget, noteWidget, verifiedValue } = opts;
  if (!Array.isArray(subformRows) || subformRows.length === 0) {
    return { pass: true, message: '', badRowIndex: -1 };
  }
  for (let i = 0; i < subformRows.length; i++) {
    const row = subformRows[i] || {};
    const status = row[statusWidget];
    if (status !== verifiedValue) {
      const note = noteWidget ? row[noteWidget] : '';
      const reason = note || status || '发票/凭证未通过校验';
      return {
        pass: false,
        message: `第 ${i + 1} 行票据未通过校验：${reason}`,
        badRowIndex: i,
      };
    }
  }
  return { pass: true, message: '', badRowIndex: -1 };
}

module.exports = { validateSubmission };
