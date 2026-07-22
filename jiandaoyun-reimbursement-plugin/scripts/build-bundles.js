'use strict';

/**
 * 极简 CommonJS 打包器：把某个后端函数入口及其相对依赖内联成单文件，
 * 方便直接粘贴进简道云「自建插件 >> 后端函数」的代码框。
 *
 * - 相对 require（以 . 开头）会被内联；
 * - 其它 require（fs/path/crypto/axios 等）保留为运行时原生 require；
 * - plugin.config.json 会被内联，并在文件头调用 setEmbeddedConfig 注入。
 *
 * 用法：node scripts/build-bundles.js
 */

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const SRC = path.join(ROOT, 'src');
const DIST = path.join(ROOT, 'dist');

function resolveModule(spec, fromDir) {
  let p = path.resolve(fromDir, spec);
  if (fs.existsSync(p) && fs.statSync(p).isFile()) return p;
  if (fs.existsSync(p + '.js')) return p + '.js';
  if (fs.existsSync(path.join(p, 'index.js'))) return path.join(p, 'index.js');
  throw new Error(`无法解析模块：${spec}（from ${fromDir}）`);
}

function collect(entry) {
  const modules = new Map(); // absPath -> source
  const stack = [path.resolve(entry)];
  while (stack.length) {
    const abs = stack.pop();
    if (modules.has(abs)) continue;
    const src = fs.readFileSync(abs, 'utf8');
    modules.set(abs, src);
    const dir = path.dirname(abs);
    const re = /require\(\s*(['"])(\.[^'"]+)\1\s*\)/g;
    let m;
    while ((m = re.exec(src)) !== null) {
      const dep = resolveModule(m[2], dir);
      if (!modules.has(dep)) stack.push(dep);
    }
  }
  return modules;
}

function idFor(abs) {
  return path.relative(ROOT, abs).replace(/\\/g, '/');
}

function buildBundle(entryRel, outName, exportKind) {
  const entryAbs = path.join(ROOT, entryRel);
  const modules = collect(entryAbs);
  const cfgRaw = fs.readFileSync(path.join(ROOT, 'plugin.config.json'), 'utf8');

  let out = '';
  out += `'use strict';\n`;
  out += `/* 自动生成，请勿手改。源码见 ${entryRel}。构建：npm run build */\n`;
  out += `var __modules = {};\nvar __cache = {};\n`;
  out += `function __load(id){\n`;
  out += `  if (__cache[id]) return __cache[id].exports;\n`;
  out += `  var module = { exports: {} };\n  __cache[id] = module;\n`;
  out += `  __modules[id].call(module.exports, module, module.exports, __mkReq(id));\n`;
  out += `  return module.exports;\n}\n`;
  out += `function __mkReq(fromId){\n`;
  out += `  var base = fromId.split('/').slice(0, -1);\n`;
  out += `  return function(spec){\n`;
  out += `    if (spec.charAt(0) !== '.') return require(spec);\n`;
  out += `    var parts = base.slice();\n`;
  out += `    spec.split('/').forEach(function(seg){\n`;
  out += `      if (seg === '.' || seg === '') return;\n`;
  out += `      if (seg === '..') parts.pop(); else parts.push(seg);\n`;
  out += `    });\n`;
  out += `    var id = parts.join('/');\n`;
  out += `    if (!__modules[id] && __modules[id + '.js']) id = id + '.js';\n`;
  out += `    if (!__modules[id] && __modules[id + '/index.js']) id = id + '/index.js';\n`;
  out += `    return __load(id);\n`;
  out += `  };\n}\n`;

  for (const [abs, src] of modules) {
    const id = idFor(abs);
    out += `__modules[${JSON.stringify(id)}] = function(module, exports, require){\n`;
    out += src;
    out += `\n};\n`;
  }

  // 注入内联配置
  out += `__load(${JSON.stringify('src/shared/config.js')}).setEmbeddedConfig(${cfgRaw});\n`;

  const entryId = idFor(entryAbs);
  // 简道云后端函数期望一个可调用入口。既导出 module.exports=main，也提供全局 main。
  out += `var __entry = __load(${JSON.stringify(entryId)});\n`;
  out += `module.exports = __entry;\n`;
  if (exportKind === 'main') {
    out += `if (typeof module.exports === 'function') { module.exports.main = module.exports.main || module.exports; }\n`;
  }

  fs.mkdirSync(DIST, { recursive: true });
  const outPath = path.join(DIST, outName);
  fs.writeFileSync(outPath, out, 'utf8');
  return outPath;
}

function main() {
  const a = buildBundle('src/invoice/invoiceBackend.js', 'invoiceBackend.bundle.js', 'main');
  const b = buildBundle('src/similarity/similarityBackend.js', 'similarityBackend.bundle.js', 'main');
  // 前端扩展也打个包，便于粘贴
  const c = buildBundle('src/frontend/invoiceFrontend.js', 'invoiceFrontend.bundle.js', 'plain');
  const d = buildBundle('src/frontend/attachmentFrontend.js', 'attachmentFrontend.bundle.js', 'plain');
  console.log('已生成：');
  [a, b, c, d].forEach((p) => console.log('  -', path.relative(ROOT, p)));
}

main();
