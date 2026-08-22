/**
 * Prism 语法高亮共享模块。
 *
 * 集中完成两件事：
 * 1. 注册常用语言包（prismjs 核心只自带 markup/css/clike/javascript，
 *    其余语言必须显式 import；在此统一注册后，CodeBlock / ShowFileCard /
 *    快捷窗口文件预览等所有使用 Prism 的地方自动获益）。
 * 2. 提供「路径 → Prism 语言 id」映射与「文本 → 高亮 HTML」的纯函数。
 *
 * 颜色样式复用 main.ts 引入的 prismjs/themes/prism.css（与代码块渲染同款），
 * 本模块不产出任何颜色相关代码。
 */

import Prism from 'prismjs';

// ---- 语言包注册（注意 import 顺序即依赖顺序：被依赖的语言必须先加载） ----
import 'prismjs/components/prism-markup-templating'; // php 依赖
import 'prismjs/components/prism-c';
import 'prismjs/components/prism-cpp'; // 依赖 c
import 'prismjs/components/prism-java';
import 'prismjs/components/prism-csharp';
import 'prismjs/components/prism-kotlin';
import 'prismjs/components/prism-swift';
import 'prismjs/components/prism-go';
import 'prismjs/components/prism-rust';
import 'prismjs/components/prism-php'; // 依赖 markup-templating
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-ruby';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-powershell';
import 'prismjs/components/prism-batch';
import 'prismjs/components/prism-yaml';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-sql';
import 'prismjs/components/prism-scss';
import 'prismjs/components/prism-sass';
import 'prismjs/components/prism-less';
import 'prismjs/components/prism-typescript';
import 'prismjs/components/prism-jsx';
import 'prismjs/components/prism-tsx'; // 依赖 jsx + typescript
import 'prismjs/components/prism-toml';
import 'prismjs/components/prism-ini';
import 'prismjs/components/prism-markdown';

/** 扩展名（小写、带点）→ Prism 语言 id */
const EXT_TO_PRISM_LANG: Record<string, string> = {
  '.js': 'javascript',
  '.mjs': 'javascript',
  '.cjs': 'javascript',
  '.jsx': 'jsx',
  '.ts': 'typescript',
  '.mts': 'typescript',
  '.cts': 'typescript',
  '.tsx': 'tsx',
  '.vue': 'markup',
  '.py': 'python',
  '.rb': 'ruby',
  '.go': 'go',
  '.rs': 'rust',
  '.java': 'java',
  '.c': 'c',
  '.h': 'c',
  '.cpp': 'cpp',
  '.hpp': 'cpp',
  '.cc': 'cpp',
  '.hh': 'cpp',
  '.cxx': 'cpp',
  '.cs': 'csharp',
  '.php': 'php',
  '.swift': 'swift',
  '.kt': 'kotlin',
  '.kts': 'kotlin',
  '.sh': 'bash',
  '.bash': 'bash',
  '.zsh': 'bash',
  '.ps1': 'powershell',
  '.bat': 'batch',
  '.cmd': 'batch',
  '.html': 'markup',
  '.htm': 'markup',
  '.xhtml': 'markup',
  '.xml': 'markup',
  '.svg': 'markup',
  '.css': 'css',
  '.scss': 'scss',
  '.sass': 'sass',
  '.less': 'less',
  '.json': 'json',
  '.json5': 'json',
  '.yaml': 'yaml',
  '.yml': 'yaml',
  '.toml': 'toml',
  '.ini': 'ini',
  '.cfg': 'ini',
  '.conf': 'ini',
  '.sql': 'sql',
  '.md': 'markdown',
  '.markdown': 'markdown'
};

/**
 * 根据文件路径推断 Prism 语言 id。
 * @returns 语言 id；无法推断（如 .txt/.log/.csv 或无扩展名）返回 null。
 */
export function prismLangForPath(path: string): string | null {
  const name = path.split('/').pop() || '';
  const dotIdx = name.lastIndexOf('.');
  if (dotIdx < 0) return null;
  const ext = name.slice(dotIdx).toLowerCase();
  return EXT_TO_PRISM_LANG[ext] || null;
}

/** HTML 转义（Prism 不可用时的兜底渲染） */
export function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/**
 * 对整段代码做语法高亮，返回可安全用于 v-html 的 HTML 字符串。
 * 语言未注册或高亮过程抛错时，回退为转义后的纯文本。
 */
export function highlightCode(content: string, lang: string | null): string {
  if (lang) {
    const grammar = Prism.languages[lang];
    if (grammar) {
      try {
        return Prism.highlight(content, grammar, lang);
      } catch {
        // fallthrough 到纯文本兜底
      }
    }
  }
  return escapeHtml(content);
}

export { Prism };
