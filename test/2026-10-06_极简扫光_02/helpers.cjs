const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const repo = path.resolve(__dirname, '../..');

function createLoader(overrides = {}, testWindow = {}) {
  const cache = new Map();
  function load(filename) {
    const absolute = path.resolve(filename);
    if (cache.has(absolute)) return cache.get(absolute).exports;
    const source = fs.readFileSync(absolute, 'utf8');
    const compiled = ts.transpileModule(source, {
      compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
      reportDiagnostics: true
    });
    const errors = (compiled.diagnostics || []).filter((item) => item.category === ts.DiagnosticCategory.Error);
    assert.equal(errors.length, 0, ts.formatDiagnosticsWithColorAndContext(errors, {
      getCanonicalFileName: (name) => name,
      getCurrentDirectory: () => repo,
      getNewLine: () => '\n'
    }));
    const module = { exports: {} };
    cache.set(absolute, module);
    const localRequire = (name) => {
      if (Object.hasOwn(overrides, name)) return overrides[name];
      if (name.startsWith('.')) return load(path.resolve(path.dirname(absolute), `${name}.ts`));
      return require(name);
    };
    new Function('module', 'exports', 'require', 'window', compiled.outputText)(
      module, module.exports, localRequire, testWindow
    );
    return module.exports;
  }
  return load;
}

module.exports = { createLoader, repo };
