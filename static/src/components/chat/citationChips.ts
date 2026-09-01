/**
 * 行内引用（Inline Citations）：chip 增强与 popover 状态控制。
 *
 * 数据流：消息文本中的【cite:...】marker 经 useMarkdownRenderer 转换为
 * <span class="md-citation-chip" data-citation-ids="...">，本模块在
 * MarkdownRenderer 渲染后扫描容器，用 message.metadata.citations 填充
 * chip 内容并接管交互（悬停延迟开 / 移出延迟关 / 点击固定 / 滚动收起）。
 */
import { reactive } from 'vue';

export interface CitationAnnotation {
  id: string;
  type: 'url_citation' | 'file_citation';
  // url 来源
  title?: string;
  url?: string;
  domain?: string;
  published_date?: string;
  // 文件来源
  file_path?: string;
  file_name?: string;
  size?: number;
  // 可选定位
  page?: number;
  line_start?: number;
  line_end?: number;
  snippet?: string;
}

/** popover 全局状态（单例；同一时间只存在一个引用弹层） */
export const citationPopover = reactive({
  visible: false,
  pinned: false,
  anchor: null as HTMLElement | null,
  annotations: [] as CitationAnnotation[],
});

const HOVER_OPEN_DELAY = 250; // 悬停多久后打开
const HOVER_CLOSE_DELAY = 300; // 移出多久后关闭（留出移到弹层上的余量）

/** chip 元素上挂的当前 annotations（final 到达后可被富化替换，触发器事件时读取） */
type ChipWithAnnotations = HTMLElement & { _citationAnnotations?: CitationAnnotation[] };

let openTimer: ReturnType<typeof setTimeout> | undefined;
let closeTimer: ReturnType<typeof setTimeout> | undefined;

function clearPopoverTimers() {
  clearTimeout(openTimer);
  clearTimeout(closeTimer);
  openTimer = undefined;
  closeTimer = undefined;
}

export function openCitationPopover(anchor: HTMLElement, annotations: CitationAnnotation[], pinned: boolean) {
  closeCitationPopover();
  citationPopover.anchor = anchor;
  citationPopover.annotations = annotations;
  citationPopover.pinned = pinned;
  citationPopover.visible = true;
  anchor.classList.add('is-open');
}

export function closeCitationPopover() {
  clearPopoverTimers();
  if (citationPopover.anchor) {
    citationPopover.anchor.classList.remove('is-open');
  }
  citationPopover.visible = false;
  citationPopover.pinned = false;
  citationPopover.anchor = null;
  citationPopover.annotations = [];
}

function schedulePopoverClose() {
  clearTimeout(closeTimer);
  closeTimer = setTimeout(() => {
    if (!citationPopover.pinned) closeCitationPopover();
  }, HOVER_CLOSE_DELAY);
}

/** popover 自身 hover 进入时取消关闭（CitationPopover 组件调用） */
export function keepCitationPopover() {
  clearTimeout(closeTimer);
}

/** popover 自身 hover 离开时按延迟关闭（未固定时） */
export function leaveCitationPopover() {
  if (!citationPopover.pinned) schedulePopoverClose();
}

function attachChipTriggers(chip: ChipWithAnnotations) {
  // 事件时从元素上读最新 annotations（final 富化替换后无需重绑监听器）
  const current = () => chip._citationAnnotations || [];
  chip.addEventListener('mouseenter', () => {
    clearTimeout(closeTimer);
    if (citationPopover.anchor === chip && citationPopover.visible) return;
    clearTimeout(openTimer);
    openTimer = setTimeout(() => openCitationPopover(chip, current(), false), HOVER_OPEN_DELAY);
  });
  chip.addEventListener('mouseleave', () => {
    clearTimeout(openTimer);
    if (citationPopover.visible && citationPopover.anchor === chip && !citationPopover.pinned) {
      schedulePopoverClose();
    }
  });
  chip.addEventListener('click', (e) => {
    e.stopPropagation();
    clearPopoverTimers();
    if (citationPopover.anchor === chip && citationPopover.visible && citationPopover.pinned) {
      closeCitationPopover(); // 再点已固定的弹层 → 收起
    } else {
      openCitationPopover(chip, current(), true); // 点击 = 固定
    }
  });
}

/** 从文件引用 token（如 docs/x.md#L1-10）直接解析出临时 annotation：
 *  marker 自带路径与定位，chip 在输出瞬间即可渲染，无需等后端富化。 */
export function fileAnnotationFromToken(token: string): CitationAnnotation | null {
  const raw = (token || '').trim();
  if (!raw || /^src_/i.test(raw)) return null;
  // 兼容 #L1-10 与 GitHub 风格 #L1-L10 / #p7
  const m = raw.match(/^(.*?)#(?:L(\d+)(?:-L?(\d+))?|p(\d+))$/i);
  let path = (m ? m[1] : raw).trim();
  if (path.startsWith('./')) path = path.slice(2);
  if (!path) return null;
  const ann: CitationAnnotation = {
    id: token,
    type: 'file_citation',
    file_path: path,
    file_name: path.split('/').pop() || path,
  };
  if (m) {
    if (m[4]) ann.page = parseInt(m[4], 10);
    if (m[2]) {
      ann.line_start = parseInt(m[2], 10);
      if (m[3]) ann.line_end = parseInt(m[3], 10);
    }
  }
  return ann;
}

/** 扫描对话消息中的工具结果，收集 web_search / extract_webpage 注册的来源 annotation。
 *  工具完成先于模型输出 marker，因此流式期间即可解析 cite:src_xxx。 */
export function collectConversationCitations(messages: any[]): CitationAnnotation[] {
  const map = new Map<string, CitationAnnotation>();
  for (const msg of messages || []) {
    for (const action of msg?.actions || []) {
      const list = action?.tool?.result?.citations;
      if (!Array.isArray(list)) continue;
      for (const ann of list) {
        if (ann && typeof ann.id === 'string' && !map.has(ann.id)) {
          map.set(ann.id, ann);
        }
      }
    }
  }
  return [...map.values()];
}

function shortDomain(domain?: string): string {
  return (domain || '').replace(/^www\./, '');
}

function chipLabel(ann: CitationAnnotation): string {
  return ann.type === 'file_citation' ? (ann.file_name || ann.file_path || '') : shortDomain(ann.domain);
}

function faviconHtml(domain: string): string {
  const d = shortDomain(domain);
  // 站点图标加载失败时退化为域名首字符
  const letter = (d[0] || '?').toUpperCase();
  return (
    `<img class="chip-favicon" loading="lazy" alt="" ` +
    `src="https://www.google.com/s2/favicons?domain=${encodeURIComponent(d)}&sz=64" ` +
    `onerror="this.outerHTML='<span class=&quot;chip-letter&quot;>${letter}</span>'">`
  );
}

const FILE_ICON_SVG =
  '<svg class="chip-file-icon" viewBox="0 0 16 16" fill="none">' +
  '<path d="M4 1.5h5.5L13 5v9.5H4z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>' +
  '<path d="M9.5 1.5V5H13" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>';

function chipIconHtml(ann: CitationAnnotation): string {
  return ann.type === 'file_citation' ? FILE_ICON_SVG : faviconHtml(ann.domain || '');
}

/**
 * 扫描容器内的 chip 占位 span，填充内容并接管交互。
 *
 * 两阶段解析：
 * - 即时（输出瞬间）：cite 来源从对话工具结果收集的 citations 查表；
 *   file 来源 token 自解析（marker 自带路径/locator，无需等待后端）。
 * - 权威（final=true，message.metadata.citations 到达）：仅以权威表为准，
 *   查不到的 chip 移除（后端已剥离无效 marker）；已渲染 chip 用富化数据
 *   替换引用（popover 可读 snippet/size），DOM 不重建。
 */
export function enhanceCitationChips(
  container: HTMLElement,
  citations: CitationAnnotation[] | undefined,
  opts: { final?: boolean } = {},
) {
  const chips = container.querySelectorAll<ChipWithAnnotations>('.md-citation-chip');
  if (!chips.length) return;
  const final = !!opts.final;
  const map = new Map((citations || []).map((c) => [c.id, c]));

  chips.forEach((chip) => {
    const ids = (chip.dataset.citationIds || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);

    // 已渲染的 chip：final 到达时换权威富化数据（供 popover 读取），DOM 不动
    if (chip.dataset.citeEnhanced === '1' && chip.dataset.citeLoaded === '1') {
      if (final) {
        const enriched = ids
          .map((id) => map.get(id))
          .filter((a): a is CitationAnnotation => !!a);
        if (enriched.length) chip._citationAnnotations = enriched;
      }
      return;
    }

    let annotations = ids
      .map((id) => map.get(id))
      .filter((a): a is CitationAnnotation => !!a);

    // 非 final：file token 自解析作为临时数据（查表未命中时），让 chip 即时渲染
    if (!annotations.length && !final) {
      const derived = ids
        .map((token) => fileAnnotationFromToken(token))
        .filter((a): a is CitationAnnotation => !!a);
      if (derived.length) annotations = derived;
    }

    if (!annotations.length) {
      if (final) {
        // 权威裁决：后端已剥离该 marker（幻觉 id / 文件不存在），移除 chip
        chip.remove();
        return;
      }
      // 流式占位：中性胶囊，暂不可交互
      if (chip.dataset.citeEnhanced !== '1') {
        chip.dataset.citeEnhanced = '1';
        chip.classList.add('is-pending');
        chip.innerHTML = '<span class="chip-pending-dot"></span>';
      }
      return;
    }

    const icons = annotations.slice(0, 2).map(chipIconHtml).join('');
    const more =
      annotations.length > 1 ? `<span class="chip-count">+${annotations.length - 1}</span>` : '';
    chip.innerHTML =
      `<span class="chip-icons">${icons}</span>` +
      `<span class="chip-label">${escapeText(chipLabel(annotations[0]))}</span>` +
      more;
    chip.classList.remove('is-pending');
    chip.classList.add(annotations[0].type === 'file_citation' ? 'is-file' : 'is-url');
    chip.dataset.citeEnhanced = '1';
    chip.dataset.citeLoaded = '1';
    chip._citationAnnotations = annotations;
    attachChipTriggers(chip);
  });
}

function escapeText(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
