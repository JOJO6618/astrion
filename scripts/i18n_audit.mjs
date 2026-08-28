#!/usr/bin/env node
/**
 * i18n 文案栏杆（对标 .stylelintrc.cjs 的颜色 token 栏杆，规范见 doc/frontend/i18n_spec.md）
 *
 * 作用：防止「绕过文案包直接写中文界面文字」的写法回退。
 *   - 已迁移文件（不在 baseline 中）：script/模板里的中文字符串 → 报错（必须用 t()/$t()）。
 *   - 存量文件（在 scripts/i18n_baseline.txt 中）：暂时豁免，迁移一个、从 baseline 移除一个。
 *   - 中文注释、console/debugLog 日志行不作为违规（见规范 §3.3）。
 *   - static/src/locales/** 永久豁免（文案定义源，对标 _tokens.scss）。
 *
 * 用法：
 *   node scripts/i18n_audit.mjs                # 检查
 *   node scripts/i18n_audit.mjs --write-baseline   # 重新生成 baseline（仅限立规初始化，禁止用于掩盖新增违规）
 */
import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOT = process.cwd();
const SRC_DIR = join(ROOT, 'static', 'src');
const LOCALES_DIR = join(SRC_DIR, 'locales');
const BASELINE_PATH = join(ROOT, 'scripts', 'i18n_baseline.txt');

const CJK_RE = /[一-龥]/;

/** 递归收集 .vue/.ts 文件（排除 .d.ts 与 locales/） */
function collectSourceFiles(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (full.startsWith(LOCALES_DIR)) continue;
    const stat = statSync(full);
    if (stat.isDirectory()) {
      if (entry === 'node_modules' || entry === 'dist') continue;
      collectSourceFiles(full, out);
    } else if (/\.(vue|ts)$/.test(entry) && !entry.endsWith('.d.ts')) {
      out.push(full);
    }
  }
  return out;
}

/**
 * 尽力而为的注释剥离（字符级状态机）：
 * 保留字符串内容（'...' "..." `...` 及正则字面量），剥离 // 行注释、/* *​/ 块注释、<!-- --> HTML 注释。
 *
 * 已知近似（作为防回退栏杆足够，不作为解析器）：
 * - 正则起点用「前一个有效字符是否为运算符/括号类」启发式判断；
 * - prevOutChar 跟踪的是「最近一次追加到输出的非空白字符」（注释视为空白），
 *   不能用 code 下标回查输出串（两者长度不等会错位——已踩过坑）。
 */
export function stripComments(code) {
  let out = '';
  let i = 0;
  const n = code.length;
  // 状态：normal | line | block | html | sq | dq | tpl | regex | regexClass
  let state = 'normal';
  const tplBraceDepth = []; // 模板字符串内 ${} 的嵌套深度栈
  let prevOutChar = ''; // 最近追加到 out 的非空白字符（正则起点启发式用）

  const append = (text) => {
    out += text;
    for (let k = text.length - 1; k >= 0; k--) {
      const c = text[k];
      if (c !== ' ' && c !== '\t' && c !== '\n' && c !== '\r') {
        prevOutChar = c;
        break;
      }
    }
  };

  while (i < n) {
    const ch = code[i];
    const next = i + 1 < n ? code[i + 1] : '';

    if (state === 'line') {
      if (ch === '\n') {
        state = 'normal';
        out += ch;
      }
      i++;
      continue;
    }
    if (state === 'block') {
      if (ch === '*' && next === '/') {
        state = 'normal';
        i += 2;
      } else {
        if (ch === '\n') out += ch; // 保留换行，行号不漂移
        i++;
      }
      continue;
    }
    if (state === 'html') {
      if (ch === '-' && next === '-' && code[i + 2] === '>') {
        state = 'normal';
        i += 3;
      } else {
        if (ch === '\n') out += ch;
        i++;
      }
      continue;
    }
    if (state === 'regex') {
      if (ch === '\\') {
        append(ch + next);
        i += 2;
        continue;
      }
      if (ch === '[') {
        state = 'regexClass';
        append(ch);
        i++;
        continue;
      }
      if (ch === '/') {
        state = 'normal';
        append(ch);
        i++;
        continue;
      }
      if (ch === '\n') {
        // 正则跨行 = 之前误判（实为除法），回退为普通代码
        state = 'normal';
        out += ch;
        i++;
        continue;
      }
      append(ch);
      i++;
      continue;
    }
    if (state === 'regexClass') {
      if (ch === '\\') {
        append(ch + next);
        i += 2;
        continue;
      }
      if (ch === ']') state = 'regex';
      append(ch);
      i++;
      continue;
    }
    if (state === 'sq' || state === 'dq' || state === 'tpl') {
      if (ch === '\\') {
        append(ch + next);
        i += 2;
        continue;
      }
      if (state === 'sq' && ch === "'") {
        state = 'normal';
        append(ch);
        i++;
        continue;
      }
      if (state === 'dq' && ch === '"') {
        state = 'normal';
        append(ch);
        i++;
        continue;
      }
      if (state === 'tpl') {
        if (ch === '`') {
          state = 'normal';
          append(ch);
          i++;
          continue;
        }
        if (ch === '$' && next === '{') {
          tplBraceDepth.push(1);
          append('${');
          i += 2;
          state = 'normal';
          continue;
        }
      }
      append(ch);
      i++;
      continue;
    }

    // normal 状态
    if (ch === '/' && next === '/') {
      state = 'line';
      i += 2;
      continue;
    }
    if (ch === '/' && next === '*') {
      state = 'block';
      i += 2;
      continue;
    }
    // 正则字面量（启发式）：/ 的前一个有效字符是运算符/括号类时视为正则起点，
    // 否则视为除法。不修这个会把 /"/g 这类正则里的引号误判为字符串起点，导致后续状态全错。
    if (ch === '/' && next !== '/' && next !== '*' && next !== '=') {
      const regexStarters = '(,=:[!&|?{};+-*%^~<>';
      if (prevOutChar === '' || regexStarters.includes(prevOutChar)) {
        state = 'regex';
        append(ch);
        i++;
        continue;
      }
    }
    if (ch === '<' && next === '!' && code[i + 2] === '-' && code[i + 3] === '-') {
      state = 'html';
      i += 4;
      continue;
    }
    if (ch === "'") {
      state = 'sq';
      append(ch);
      i++;
      continue;
    }
    if (ch === '"') {
      state = 'dq';
      append(ch);
      i++;
      continue;
    }
    if (ch === '`') {
      state = 'tpl';
      append(ch);
      i++;
      continue;
    }
    if (ch === '{' && tplBraceDepth.length > 0) {
      tplBraceDepth[tplBraceDepth.length - 1]++;
      append(ch);
      i++;
      continue;
    }
    if (ch === '}' && tplBraceDepth.length > 0) {
      tplBraceDepth[tplBraceDepth.length - 1]--;
      if (tplBraceDepth[tplBraceDepth.length - 1] === 0) {
        tplBraceDepth.pop();
        append(ch);
        i++;
        state = 'tpl';
        continue;
      }
      append(ch);
      i++;
      continue;
    }
    append(ch);
    i++;
  }
  return out;
}

/** console / debugLog 日志行不作为违规（日志可保留中文，见规范 §3.3） */
const LOG_LINE_RE =
  /\bconsole\s*\.\s*(log|warn|error|info|debug|trace|table|group|groupEnd|groupCollapsed)\s*\(|\bdebugLog\s*\(|\.\s*debugLog\s*\(/;

/** 返回文件中剥离注释后仍含中文的行号列表 */
function findCjkLines(filePath) {
  const stripped = stripComments(readFileSync(filePath, 'utf8'));
  const lines = stripped.split('\n');
  const hits = [];
  for (let idx = 0; idx < lines.length; idx++) {
    if (CJK_RE.test(lines[idx]) && !LOG_LINE_RE.test(lines[idx])) hits.push(idx + 1);
  }
  return hits;
}

function readBaseline() {
  try {
    return new Set(
      readFileSync(BASELINE_PATH, 'utf8')
        .split('\n')
        .map((line) => line.trim())
        .filter((line) => line && !line.startsWith('#'))
    );
  } catch {
    return new Set();
  }
}

function main() {
  // 入口守卫：被 import 时不执行（供其他脚本复用 stripComments）
  const isMain = process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop());
  if (!isMain) return;

  const writeBaseline = process.argv.includes('--write-baseline');
  const files = collectSourceFiles(SRC_DIR);
  const violations = new Map(); // relPath -> lineNumbers

  for (const file of files) {
    const hits = findCjkLines(file);
    if (hits.length > 0) {
      violations.set(relative(ROOT, file), hits);
    }
  }

  if (writeBaseline) {
    const content =
      '# i18n baseline —— 立规前已存在中文文案的存量文件（迁移一个、移除一个）\n' +
      '# 重新生成：node scripts/i18n_audit.mjs --write-baseline\n' +
      [...violations.keys()].sort().join('\n') +
      '\n';
    writeFileSync(BASELINE_PATH, content);
    console.log(`[i18n-audit] baseline 已写入 ${violations.size} 个文件 → scripts/i18n_baseline.txt`);
    return;
  }

  const baseline = readBaseline();
  const newViolations = [];
  const cleanNow = [];

  for (const [rel, hits] of violations) {
    if (baseline.has(rel)) {
      // 存量豁免中
    } else {
      newViolations.push([rel, hits]);
    }
  }
  for (const rel of baseline) {
    if (!violations.has(rel)) cleanNow.push(rel);
  }

  if (newViolations.length > 0) {
    console.error('[i18n-audit] 以下文件绕过了文案包，直接写入了中文（规范见 doc/frontend/i18n_spec.md）：');
    for (const [rel, hits] of newViolations) {
      console.error(`  ${rel}  (行: ${hits.slice(0, 10).join(', ')}${hits.length > 10 ? '…' : ''})`);
    }
    console.error('[i18n-audit] 请改用 t()/$t() 取词；若为存量文件迁移中，请先完成迁移再加入 baseline 移除流程。');
    process.exit(1);
  }

  if (cleanNow.length > 0) {
    console.log('[i18n-audit] 以下 baseline 文件已不含中文文案，可从 scripts/i18n_baseline.txt 移除：');
    for (const rel of cleanNow) console.log(`  ${rel}`);
  }
  console.log(`[i18n-audit] 通过（豁免中 ${baseline.size - cleanNow.length} 个存量文件，无新增违规）。`);
}

main();
