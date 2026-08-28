import Prism from 'prismjs';
import katex from 'katex';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import remarkRehype from 'remark-rehype';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import rehypeStringify from 'rehype-stringify';
import { visit } from 'unist-util-visit';
import { t } from '@/locales';

let latexRenderTimer: number | null = null;
let streamingCodeHighlightTimer: number | null = null;
const markdownCache = new Map<string, string>();
let showHtmlDebugCount = 0;
const SHOW_HTML_DEBUG_MAX = 500;

function isShowHtmlDebugEnabled() {
  if (typeof window === 'undefined') return false;
  try {
    const flag = (window as any).__SHOW_HTML_DEBUG__;
    if (flag === true || flag === '1') return true;
    if (flag === false || flag === '0') return false;
    const localFlag = window.localStorage?.getItem('showHtmlDebug');
    if (localFlag === '1' || localFlag === 'true') return true;
    return false;
  } catch {
    return false;
  }
}

function showHtmlDebugLog(event: string, payload: Record<string, any> = {}) {
  if (!isShowHtmlDebugEnabled()) return;
  if (showHtmlDebugCount >= SHOW_HTML_DEBUG_MAX) return;
  showHtmlDebugCount += 1;
  if (showHtmlDebugCount === SHOW_HTML_DEBUG_MAX) {
    console.warn('[SHOW_HTML_DEBUG]', 'log-limit-reached', { max: SHOW_HTML_DEBUG_MAX });
    return;
  }
  console.log('[SHOW_HTML_DEBUG]', event, payload);
}

function encodeBase64Utf8(input: string) {
  try {
    return btoa(unescape(encodeURIComponent(input)));
  } catch {
    return '';
  }
}

function escapeHtmlAttribute(input: string): string {
  return input
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const SHOW_HTML_ALLOWED_RATIOS = ['1:1', '16:9', '9:16', '4:3', '3:4'] as const;
const SHOW_HTML_RATIO_VALUES: Record<string, number> = {
  '1:1': 1,
  '16:9': 16 / 9,
  '9:16': 9 / 16,
  '4:3': 4 / 3,
  '3:4': 3 / 4
};

function normalizeShowHtmlRatio(raw: string | null | undefined) {
  if (!raw) return '1:1';
  const cleaned = raw.trim().toLowerCase().replace(/[x/]/g, ':').replace(/\s+/g, '');
  if ((SHOW_HTML_ALLOWED_RATIOS as readonly string[]).includes(cleaned)) return cleaned;
  return '1:1';
}

function pickNearestAllowedRatio(width: number, height: number) {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return '1:1';
  }
  const target = width / height;
  let best = '1:1';
  let bestDiff = Number.POSITIVE_INFINITY;
  for (const ratio of SHOW_HTML_ALLOWED_RATIOS) {
    const diff = Math.abs(SHOW_HTML_RATIO_VALUES[ratio] - target);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = ratio;
    }
  }
  return best;
}

function extractShowHtmlRatio(tagSource: string) {
  const ratioMatch = tagSource.match(/ratio\s*=\s*["']?\s*([0-9]{1,2}\s*[:xX/]\s*[0-9]{1,2})/i);
  if (ratioMatch?.[1]) {
    return normalizeShowHtmlRatio(ratioMatch[1]);
  }
  const widthMatch = tagSource.match(/width\s*=\s*["']?(\d{1,4})/i);
  const heightMatch = tagSource.match(/height\s*=\s*["']?(\d{1,4})/i);
  if (widthMatch?.[1] && heightMatch?.[1]) {
    const width = Number.parseInt(widthMatch[1], 10);
    const height = Number.parseInt(heightMatch[1], 10);
    return pickNearestAllowedRatio(width, height);
  }
  return '1:1';
}

function normalizeShowHtmlJsMode(raw: string | null | undefined) {
  if (!raw) return 'off';
  const cleaned = raw.trim().toLowerCase();
  if (['on', '1', 'true', 'yes', 'enabled', 'enable'].includes(cleaned)) {
    return 'on';
  }
  return 'off';
}

function extractShowHtmlJsMode(tagSource: string) {
  const jsMatch = tagSource.match(/js\s*=\s*["']?\s*([a-zA-Z0-9_-]+)/i);
  if (jsMatch?.[1]) {
    return normalizeShowHtmlJsMode(jsMatch[1]);
  }
  return 'off';
}

function isShowHtmlTagInsideMarkdownCode(raw: string, tagIndex: number) {
  if (!raw || tagIndex <= 0) return false;
  let i = 0;
  let inFence = false;
  let inInlineCode = false;

  while (i < tagIndex) {
    // fenced code block: ```
    if (!inInlineCode && raw.startsWith('```', i)) {
      inFence = !inFence;
      i += 3;
      continue;
    }
    if (!inFence && raw[i] === '`') {
      // 处理转义反引号 \`
      const escaped = i > 0 && raw[i - 1] === '\\';
      if (!escaped) {
        inInlineCode = !inInlineCode;
      }
      i += 1;
      continue;
    }
    i += 1;
  }

  return inFence || inInlineCode;
}

function transformShowHtmlBlocks(raw: string, isStreaming: boolean) {
  if (!raw || raw.indexOf('<show_html') === -1) return raw;
  showHtmlDebugLog('md:transform:start', {
    isStreaming,
    rawLength: raw.length
  });
  let output = '';
  let cursor = 0;
  let blockCount = 0;
  const closeTag = '</show_html>';
  const closeTagForMarkdown = '</show-html>';

  while (cursor < raw.length) {
    const start = raw.indexOf('<show_html', cursor);
    if (start === -1) {
      output += raw.slice(cursor);
      break;
    }
    if (isShowHtmlTagInsideMarkdownCode(raw, start)) {
      // 在 markdown 代码片段里的 <show_html> 仅作为文本展示，不参与渲染替换
      output += raw.slice(cursor, start + 1);
      cursor = start + 1;
      continue;
    }
    blockCount += 1;
    output += raw.slice(cursor, start);

    const openEnd = raw.indexOf('>', start);
    if (openEnd === -1) {
      if (!isStreaming) {
        output += raw.slice(start);
        break;
      }
      const partialOpen = raw.slice(start);
      const ratio = extractShowHtmlRatio(partialOpen);
      const jsMode = extractShowHtmlJsMode(partialOpen);
      const ratioAttr = ratio ? ` ratio="${ratio}"` : '';
      const jsAttr = ` js="${jsMode}"`;
      showHtmlDebugLog('md:transform:open-tag-partial', {
        blockCount,
        isStreaming,
        ratio,
        jsMode,
        partialLength: partialOpen.length
      });
      output += `<show-html${ratioAttr}${jsAttr} data-partial="1">${closeTagForMarkdown}`;
      break;
    }

    const openTag = raw.slice(start, openEnd + 1);
    const ratio = extractShowHtmlRatio(openTag);
    const jsMode = extractShowHtmlJsMode(openTag);
    const canonicalOpenTag = `<show-html ratio="${ratio}" js="${jsMode}">`;
    const closeStart = raw.indexOf(closeTag, openEnd + 1);
    if (closeStart === -1) {
      if (!isStreaming) {
        output += raw.slice(start);
        break;
      }
      const innerPartial = raw.slice(openEnd + 1);
      const encoded = encodeBase64Utf8(innerPartial);
      showHtmlDebugLog('md:transform:close-tag-partial', {
        blockCount,
        isStreaming,
        ratio,
        jsMode,
        innerPartialLength: innerPartial.length,
        encodedLength: encoded.length
      });
      const safeOpen = canonicalOpenTag.replace(
        />$/,
        encoded ? ` data-encoded="${encoded}" data-partial="1">` : ' data-partial="1">'
      );
      output += `${safeOpen}${closeTagForMarkdown}`;
      break;
    }

    const inner = raw.slice(openEnd + 1, closeStart);
    const encoded = encodeBase64Utf8(inner);
    showHtmlDebugLog('md:transform:block-complete', {
      blockCount,
      isStreaming,
      ratio,
      jsMode,
      innerLength: inner.length,
      encodedLength: encoded.length
    });
    const safeOpen = canonicalOpenTag.replace(/>$/, encoded ? ` data-encoded="${encoded}">` : '>');
    output += `${safeOpen}${closeTagForMarkdown}`;
    cursor = closeStart + closeTag.length;
  }
  showHtmlDebugLog('md:transform:end', {
    isStreaming,
    blocks: blockCount,
    outLength: output.length
  });

  return output;
}

function transformShowImageBlocks(raw: string) {
  if (!raw || raw.indexOf('show_image') === -1) return raw;
  let output = '';
  let cursor = 0;
  const openToken = '<show_image';
  const closeToken = '</show_image>';

  while (cursor < raw.length) {
    const openIdx = raw.indexOf(openToken, cursor);
    const closeIdx = raw.indexOf(closeToken, cursor);
    if (openIdx === -1 && closeIdx === -1) {
      output += raw.slice(cursor);
      break;
    }
    const hasOpen = openIdx !== -1;
    const hasClose = closeIdx !== -1;
    const useOpen = hasOpen && (!hasClose || openIdx < closeIdx);
    const hitIdx = useOpen ? openIdx : closeIdx;
    if (hitIdx < 0) {
      output += raw.slice(cursor);
      break;
    }
    if (isShowHtmlTagInsideMarkdownCode(raw, hitIdx)) {
      output += raw.slice(cursor, hitIdx + 1);
      cursor = hitIdx + 1;
      continue;
    }
    output += raw.slice(cursor, hitIdx);
    if (useOpen) {
      output += '<show-image';
      cursor = hitIdx + openToken.length;
      continue;
    }
    output += '</show-image>';
    cursor = hitIdx + closeToken.length;
  }

  return output;
}

/**
 * 预处理 <show_file> 标签（自闭合或开闭形式）。
 * markdown parser 在 inline 位置会把自定义标签当文本转义，
 * 转成显式闭合形式后可以被 rehypeRaw 识别为 raw HTML 元素。
 * show_file 不含富文本内容，不需要 base64 编码。
 */
function transformShowFileBlocks(raw: string) {
  if (!raw || (raw.indexOf('show_file') === -1 && raw.indexOf('show-file') === -1)) return raw;
  // 1. 自闭合：<show_file .../> 或 <show-file .../>
  let output = raw.replace(
    /<show[_-]file\b([^>]*?)\s*\/>/gi,
    (match, attrs: string) => `<show-file${attrs}></show-file>`
  );
  // 2. 开闭形式：<show_file ...> ... </show_file>，忽略内部内容，只保留属性
  output = output.replace(
    /<show[_-]file\b([^>]*)>([\s\S]*?)<\/show[_-]file>/gi,
    (match, attrs: string) => `<show-file${attrs}></show-file>`
  );
  return output;
}

/**
 * 预处理 LaTeX 数学公式，避免被 markdown parser 拆段。
 * $$...$$ 转换为块级占位符，$...$ 转换为行内占位符，
 * 后续由 renderMathBlocks / renderMathInElement 统一渲染。
 */
function transformMathBlocks(raw: string): string {
  if (!raw || raw.indexOf('$') === -1) return raw;

  // 先保护代码块和行内代码，避免其中的 $ 被误替换
  const CODE_BLOCK_PLACEHOLDER = '__MATH_PROTECT_CODE_BLOCK_';
  const INLINE_CODE_PLACEHOLDER = '__MATH_PROTECT_INLINE_CODE_';

  const codeBlocks: string[] = [];
  let protectedText = raw.replace(/```[\s\S]*?```/g, (match) => {
    codeBlocks.push(match);
    return `${CODE_BLOCK_PLACEHOLDER}${codeBlocks.length - 1}__`;
  });

  const inlineCodes: string[] = [];
  protectedText = protectedText.replace(/`[^`\n]+`/g, (match) => {
    inlineCodes.push(match);
    return `${INLINE_CODE_PLACEHOLDER}${inlineCodes.length - 1}__`;
  });

  // 块级 $$...$$
  protectedText = protectedText.replace(/\$\$([\s\S]*?)\$\$/g, (match, latex: string) => {
    const trimmed = latex.replace(/\s+/g, ' ').trim();
    if (!trimmed) return match;
    return `<span class="math-block" data-latex="${escapeHtmlAttribute(trimmed)}" data-display="block"></span>`;
  });

  // 行内 $...$（排除 $ 本身和反斜杠转义）
  protectedText = protectedText.replace(
    /(?<![\\$])\$([^$\n]+?)\$(?![\\$])/g,
    (match, latex: string) => {
      const trimmed = latex.replace(/\s+/g, ' ').trim();
      if (!trimmed) return match;
      return `<span class="math-inline" data-latex="${escapeHtmlAttribute(trimmed)}" data-display="inline"></span>`;
    }
  );

  // 还原保护内容
  protectedText = protectedText.replace(
    new RegExp(`${CODE_BLOCK_PLACEHOLDER}(\\d+)__`, 'g'),
    (_, i: string) => codeBlocks[Number(i)]
  );
  protectedText = protectedText.replace(
    new RegExp(`${INLINE_CODE_PLACEHOLDER}(\\d+)__`, 'g'),
    (_, i: string) => inlineCodes[Number(i)]
  );

  return protectedText;
}

type RehypeProps = Record<string, unknown>;

function readAttr(props: RehypeProps | undefined, name: string): string {
  if (!props) return '';
  const direct = props[name];
  if (typeof direct === 'string') return direct;
  const camel = name.replace(/-([a-z])/g, (_, ch: string) => ch.toUpperCase());
  const camelVal = props[camel];
  if (typeof camelVal === 'string') return camelVal;
  return '';
}

function writeAttr(props: RehypeProps, name: string, value: string | null) {
  const camel = name.replace(/-([a-z])/g, (_, ch: string) => ch.toUpperCase());
  if (value == null || value === '') {
    delete props[name];
    delete props[camel];
    return;
  }
  props[name] = value;
}

// show_file 预览类型：text/code/json/csv/markdown/image/pdf/binary
const SHOW_FILE_PREVIEW_TYPES = new Set([
  'text', 'code', 'json', 'csv', 'markdown', 'image', 'pdf', 'binary'
]);

const SHOW_FILE_EXT_TO_TYPE: Record<string, string> = {
  '.txt': 'text',
  '.md': 'markdown', '.markdown': 'markdown',
  '.json': 'json', '.json5': 'json',
  '.csv': 'csv', '.tsv': 'csv',
  '.yaml': 'text', '.yml': 'text', '.toml': 'text',
  '.ini': 'text', '.cfg': 'text', '.conf': 'text', '.log': 'text',
  '.js': 'code', '.ts': 'code', '.jsx': 'code', '.tsx': 'code',
  '.vue': 'code', '.py': 'code', '.rb': 'code', '.go': 'code',
  '.rs': 'code', '.java': 'code', '.c': 'code', '.h': 'code',
  '.cpp': 'code', '.hpp': 'code', '.cs': 'code', '.php': 'code',
  '.swift': 'code', '.kt': 'code', '.sh': 'code', '.bash': 'code',
  '.html': 'code', '.htm': 'code', '.css': 'code', '.scss': 'code',
  '.less': 'code', '.xml': 'code', '.svg': 'image', '.sql': 'code',
  '.png': 'image', '.jpg': 'image', '.jpeg': 'image',
  '.gif': 'image', '.webp': 'image', '.bmp': 'image', '.ico': 'image',
  '.avif': 'image',
  '.pdf': 'pdf',
};

function inferShowFileType(path: string, explicit?: string): string {
  if (explicit && SHOW_FILE_PREVIEW_TYPES.has(explicit)) return explicit;
  const lowerPath = path.toLowerCase();
  const dotIdx = lowerPath.lastIndexOf('.');
  if (dotIdx >= 0) {
    const ext = lowerPath.slice(dotIdx);
    if (SHOW_FILE_EXT_TO_TYPE[ext]) return SHOW_FILE_EXT_TO_TYPE[ext];
  }
  return 'binary';
}

function normalizeShowTagsPlugin() {
  return (tree: any) => {
    visit(tree, 'element', (node: any) => {
      if (!node || typeof node.tagName !== 'string') return;
      if (node.tagName === 'show_html' || node.tagName === 'show-html') {
        node.tagName = 'show_html';
        const props = (node.properties || {}) as RehypeProps;
        const ratio = normalizeShowHtmlRatio(readAttr(props, 'ratio'));
        const jsMode = normalizeShowHtmlJsMode(readAttr(props, 'js'));
        const encoded = readAttr(props, 'data-encoded');
        const partial = readAttr(props, 'data-partial');
        writeAttr(props, 'ratio', ratio);
        writeAttr(props, 'js', jsMode);
        writeAttr(props, 'data-encoded', encoded);
        writeAttr(props, 'data-partial', partial === '1' ? '1' : null);
        node.properties = props;
      }
      if (node.tagName === 'show_image' || node.tagName === 'show-image') {
        node.tagName = 'show_image';
        const props = (node.properties || {}) as RehypeProps;
        const src = readAttr(props, 'src').trim();
        const alt = readAttr(props, 'alt').trim();
        const title = readAttr(props, 'title').trim();
        writeAttr(props, 'src', src);
        writeAttr(props, 'alt', alt || title || '');
        if (!title) {
          writeAttr(props, 'title', null);
        }
        node.properties = props;
      }
      if (node.tagName === 'show_file' || node.tagName === 'show-file') {
        node.tagName = 'show_file';
        const props = (node.properties || {}) as RehypeProps;
        const rawPath = readAttr(props, 'path').trim();
        const path = rawPath.replace(/^\/+/, '');
        writeAttr(props, 'path', path);
        node.properties = props;
      }
    });
  };
}

/**
 * 拦截 markdown 链接里的 download:// 协议，标记为下载链接。
 * 输入：[文件名.pdf](download:///output/file.pdf) 或 (download://output/file.pdf)
 * 输出：<a class="md-download-link" data-path="output/file.pdf" href="download://output/file.pdf">文件名.pdf</a>
 * 点击事件由 bootstrap.ts 统一拦截。
 */
function transformDownloadLinksPlugin() {
  return (tree: any) => {
    visit(tree, 'element', (node: any) => {
      if (!node || node.tagName !== 'a' || !node.properties) return;
      const href = node.properties.href;
      if (typeof href !== 'string') return;
      // 兼容 download:///path（三个斜杠，旧写法）和 download://path（两个斜杠，推荐写法）
      const match = href.match(/^download:\/\/\/?(.*)$/i);
      if (!match) return;
      const rawPath = match[1];
      const path = rawPath.replace(/^\/+/, '');
      if (!path) return;
      node.properties.href = `download://${path}`;
      node.properties['data-path'] = path;
      node.properties['data-download'] = '1';
      const existingClass = node.properties.className;
      if (Array.isArray(existingClass)) {
        if (!existingClass.includes('md-download-link')) {
          existingClass.push('md-download-link');
        }
      } else {
        node.properties.className = ['md-download-link'];
      }
    });
  };
}

function wrapMarkdownTablesPlugin() {
  return (tree: any) => {
    visit(tree, 'element', (node: any, index: number | undefined, parent: any) => {
      if (!node || node.tagName !== 'table' || !parent || typeof index !== 'number') return;
      if (parent?.tagName === 'div' && Array.isArray(parent?.properties?.className)) {
        const classes = parent.properties.className as string[];
        if (classes.includes('md-table-scroll')) {
          return;
        }
      }
      parent.children[index] = {
        type: 'element',
        tagName: 'div',
        properties: { className: ['md-table-scroll'], 'data-md-table-scroll': '1' },
        children: [node]
      };
    });
  };
}

const sanitizedSchema: Record<string, any> = {
  ...defaultSchema,
  tagNames: [
    ...(defaultSchema.tagNames || []),
    'show_html', 'show_image', 'show_file',
    'show-html', 'show-image', 'show-file'
  ],
  attributes: {
    ...(defaultSchema.attributes || {}),
    div: [...((defaultSchema.attributes || {}).div || []), 'className', 'data-md-table-scroll'],
    span: [...((defaultSchema.attributes || {}).span || []), 'className', 'dataLatex', 'dataDisplay', 'dataMathRendered', 'data-latex', 'data-display', 'data-math-rendered'],
    a: [
      'ariaDescribedBy', 'ariaLabel', 'ariaLabelledBy',
      'dataFootnoteBackref', 'dataFootnoteRef',
      'data-path', 'data-download',
      'href',
      // className: 合并默认的 data-footnote-backref 和我们的 md-download-link
      ['className', 'data-footnote-backref', 'md-download-link']
    ],
    show_html: ['ratio', 'js', 'data-encoded', 'data-partial'],
    show_image: ['src', 'alt', 'title', 'width', 'height'],
    show_file: ['path'],
    'show-html': ['ratio', 'js', 'data-encoded', 'data-partial'],
    'show-image': ['src', 'alt', 'title', 'width', 'height'],
    'show-file': ['path']
  },
  // 允许 <a href="download://..."> 链接
  protocols: {
    ...(defaultSchema.protocols || {}),
    href: [
      ...((defaultSchema.protocols || {}).href || []),
      'download'
    ]
  }
};

const markdownProcessor = unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkBreaks)
  .use(remarkRehype, { allowDangerousHtml: true })
  .use(rehypeRaw)
  .use(normalizeShowTagsPlugin)
  .use(transformDownloadLinksPlugin)
  .use(wrapMarkdownTablesPlugin)
  .use(rehypeSanitize, sanitizedSchema)
  .use(rehypeStringify, { allowDangerousHtml: false });

function stableHash(input: string): string {
  let hash = 5381;
  for (let i = 0; i < input.length; i += 1) {
    hash = (hash * 33) ^ input.charCodeAt(i);
  }
  return (hash >>> 0).toString(36);
}

function buildCacheKey(text: string): string {
  return `md_${text.length}_${stableHash(text)}`;
}

function wrapCodeBlocks(html: string, isStreaming = false) {
  return html.replace(
    /<pre><code([^>]*)>([\s\S]*?)<\/code><\/pre>/g,
    (match, attributes, content) => {
      const langMatch = attributes.match(/class="[^"]*language-([\w-]+)/);
      const language = langMatch ? langMatch[1] : 'text';
      const blockId = `code-${stableHash(`${attributes}|${content}`)}`;
      const streamingAttr = isStreaming ? ' data-streaming="1"' : '';
      const copyCodeLabel = t('utils.copyCode');
      const escapedContent = content
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');

      return `
            <div class="code-block-wrapper"${streamingAttr} data-md-code-block="1">
                <div class="code-block-header">
                    <span class="code-language">${language}</span>
                    <button class="copy-code-btn" data-code="${blockId}" title="${copyCodeLabel}" aria-label="${copyCodeLabel}"></button>
                </div>
                <pre><code${attributes} data-code-id="${blockId}" data-original-code="${escapedContent}">${content}</code></pre>
            </div>`;
    }
  );
}

function scheduleStreamingCodeHighlight() {
  if (typeof window === 'undefined') return;
  if (streamingCodeHighlightTimer !== null) return;

  streamingCodeHighlightTimer = window.setTimeout(() => {
    streamingCodeHighlightTimer = null;

    if (typeof Prism === 'undefined') return;

    const codeBlocks = document.querySelectorAll('.code-block-wrapper[data-streaming="1"] pre code');
    codeBlocks.forEach((block) => {
      try {
        Prism.highlightElement(block as HTMLElement);
      } catch (error) {
        console.warn('流式代码高亮失败:', error);
      }
    });
  }, 120);
}

function renderMarkdownToHtml(text: string): string {
  const file = markdownProcessor.processSync(text);
  return String(file);
}

export interface MarkdownSegment {
  type: 'text' | 'code';
  content: string;
  language?: string;
  closed: boolean;
  key: string;
}

function hashCodeBlock(language: string, content: string): string {
  return stableHash(`${language || ''}|${content}`);
}

export function parseMarkdownSegments(text: string, isStreaming = false): MarkdownSegment[] {
  if (!text) {
    return [{ type: 'text', content: '', closed: true, key: 'text-0' }];
  }

  const segments: MarkdownSegment[] = [];
  let cursor = 0;
  const regex = /^```([^\n]*)\n([\s\S]*?)^```/gm;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > cursor) {
      segments.push({
        type: 'text',
        content: text.slice(cursor, match.index),
        closed: true,
        key: `text-${cursor}`
      });
    }

    const language = match[1].trim();
    const content = match[2];
    const key = `code-${hashCodeBlock(language, content)}`;

    segments.push({
      type: 'code',
      content,
      language,
      closed: true,
      key
    });

    cursor = regex.lastIndex;
  }

  if (cursor < text.length) {
    const remaining = text.slice(cursor);

    if (isStreaming) {
      const openMatch = remaining.match(/^```([^\n]*)\n([\s\S]*)$/m);
      if (openMatch) {
        if (openMatch.index && openMatch.index > 0) {
          segments.push({
            type: 'text',
            content: remaining.slice(0, openMatch.index),
            closed: true,
            key: `text-${cursor}`
          });
        }
        const language = openMatch[1].trim();
        const content = openMatch[2];
        segments.push({
          type: 'code',
          content,
          language,
          closed: false,
          key: 'code-streaming-active'
        });
        return segments;
      }
    }

    segments.push({
      type: 'text',
      content: remaining,
      closed: true,
      key: `text-${cursor}`
    });
  }

  return segments;
}

export function renderMarkdownText(text: string, isStreaming = false): string {
  if (!text) return '';

  // isStreaming 必须透传：流式期间未闭合的 show_html 需要编码成 data-partial 占位
  // （js=off 实时渲染 / js=on 显示"渲染中"），否则原始标签文本会直接散落到消息里
  const safeText = transformMathBlocks(
    transformShowFileBlocks(
      transformShowImageBlocks(transformShowHtmlBlocks(text, isStreaming))
    )
  );

  let html = '';
  try {
    html = renderMarkdownToHtml(safeText);
  } catch (error) {
    console.warn('Markdown 渲染失败，降级为纯文本渲染:', error);
    html = escapeHtml(safeText).replace(/\n/g, '<br>');
  }

  return html;
}

export function renderMathBlocks(container: HTMLElement) {
  if (!container || typeof window === 'undefined') return;

  const elements = container.querySelectorAll(
    '.math-block:not([data-math-rendered]), .math-inline:not([data-math-rendered])'
  );

  elements.forEach((el) => {
    const latex = el.getAttribute('data-latex');
    if (!latex) return;

    const display = el.getAttribute('data-display') === 'block';
    try {
      el.innerHTML = katex.renderToString(latex, {
        throwOnError: false,
        displayMode: display,
        trust: true
      });
      el.setAttribute('data-math-rendered', 'true');
    } catch (error) {
      console.warn('LaTeX渲染失败:', error);
      el.setAttribute('data-math-rendered', 'error');
    }
  });
}

function renderMathBlocksForSelector(selector: string) {
  if (typeof document === 'undefined') return;
  const elements = document.querySelectorAll(selector);
  elements.forEach((el) => renderMathBlocks(el as HTMLElement));
}

function escapeHtml(input: string) {
  return input
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function renderMarkdown(text: string, isStreaming = false) {
  if (!text) {
    return '';
  }

  if (!isStreaming) {
    const cacheKey = buildCacheKey(text);
    if (markdownCache.has(cacheKey)) {
      return markdownCache.get(cacheKey) as string;
    }
  }

  const safeText = transformMathBlocks(
    transformShowFileBlocks(
      transformShowImageBlocks(transformShowHtmlBlocks(text, isStreaming))
    )
  );
  let html = '';
  try {
    html = renderMarkdownToHtml(safeText);
  } catch (error) {
    console.warn('Markdown 渲染失败，降级为纯文本渲染:', error);
    html = escapeHtml(safeText).replace(/\n/g, '<br>');
  }
  html = wrapCodeBlocks(html, isStreaming);

  if (isStreaming) {
    scheduleStreamingCodeHighlight();
  }

  if (!isStreaming && text.length < 10000) {
    const cacheKey = buildCacheKey(text);
    markdownCache.set(cacheKey, html);
    if (markdownCache.size > 20) {
      const firstKey = markdownCache.keys().next().value as string | undefined;
      if (firstKey) {
        markdownCache.delete(firstKey);
      }
    }
  }

  if (!isStreaming) {
    setTimeout(() => {
      if (typeof Prism !== 'undefined') {
        const codeBlocks = document.querySelectorAll(
          '.code-block-wrapper pre code:not([data-highlighted])'
        );
        codeBlocks.forEach((block) => {
          try {
            Prism.highlightElement(block as HTMLElement);
            block.setAttribute('data-highlighted', 'true');
          } catch (error) {
            console.warn('代码高亮失败:', error);
          }
        });
      }

      renderMathBlocksForSelector('.text-output .text-content:not(.streaming-text)');
    }, 100);
  }

  return html;
}

export function renderLatexInRealtime() {
  if (typeof window === 'undefined') {
    return;
  }

  if (latexRenderTimer) {
    cancelAnimationFrame(latexRenderTimer);
  }

  latexRenderTimer = requestAnimationFrame(() => {
    const elements = document.querySelectorAll('.text-output .streaming-text');
    elements.forEach((element) => {
      renderMathBlocks(element as HTMLElement);
    });
  });
}
