'use strict';

const LEVELS = { error: 0, warn: 1, info: 2, debug: 3 };

/**
 * 极简分级日志。简道云后端函数支持 console.*，执行日志里可见。
 * @param {string} level
 */
function createLogger(level = 'info') {
  const threshold = LEVELS[level] === undefined ? LEVELS.info : LEVELS[level];
  const emit = (lvl, method) => (...args) => {
    if (LEVELS[lvl] <= threshold) {
      // eslint-disable-next-line no-console
      (console[method] || console.log)(`[reimb-guard][${lvl}]`, ...args);
    }
  };
  return {
    error: emit('error', 'error'),
    warn: emit('warn', 'warn'),
    info: emit('info', 'log'),
    debug: emit('debug', 'log'),
  };
}

module.exports = { createLogger };
