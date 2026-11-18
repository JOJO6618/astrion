/**
 * 行内引用（Inline Citations）：chip 增强与 popover 状态控制。
 *
 * 数据流：消息文本中的【cite:...】marker 经 useMarkdownRenderer 转换为
 * <span class="md-citation-chip" data-citation-ids="...">，本模块在
 * MarkdownRenderer 渲染后扫描容器，用 message.metadata.citations 填充
 * chip 内容并接管交互（悬停延迟开 / 移出延迟关 / 点击固定 / 滚动收起）。
 */
import { reactive } from 'vue';
import { faviconFallbackHtml, upgradeCitationFavicons } from './citationFavicon';

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

/** 一次打开的悬停卡。每次打开都换新 id：切换引用时旧卡片沿原路收回、新卡片在新引用处展开，
 *  两者是不同的 DOM 实例，旧卡片不会被复用成新卡片（DESIGN.md §6.10「单例」）。 */
export interface CitationCardState {
  id: number;
  anchor: HTMLElement;
  annotations: CitationAnnotation[];
  pinned: boolean;
}

/** 悬停卡全局状态：同一时间只有一张活动卡片（退出动画中的旧卡片由 Transition 保留） */
export const citationPopover = reactive({
  current: null as CitationCardState | null
});

const HOVER_OPEN_DELAY = 250; // 冷启动：悬停多久后打开
const HOVER_OPEN_DELAY_WARM = 80; // 已有卡片（或刚收起）时移到另一个引用的打开延迟
const WARM_WINDOW = 400; // 卡片收起后多久内仍算热状态
const HOVER_CLOSE_DELAY = 300; // 移出多久后关闭（留出移到卡片上的余量）

/** chip 元素上挂的当前 annotations（final 到达后可被富化替换，触发器事件时读取） */
type ChipWithAnnotations = HTMLElement & { _citationAnnotations?: CitationAnnotation[] };

let openTimer: ReturnType<typeof setTimeout> | undefined;
let closeTimer: ReturnType<typeof setTimeout> | undefined;
let cardSeq = 0;
let lastClosedAt = -Infinity;

function clearPopoverTimers() {
  clearTimeout(openTimer);
  clearTimeout(closeTimer);
  openTimer = undefined;
  closeTimer = undefined;
}

function isWarm() {
  return !!citationPopover.current || performance.now() - lastClosedAt < WARM_WINDOW;
}

export function openCitationPopover(
  anchor: HTMLElement,
  annotations: CitationAnnotation[],
  pinned: boolean
) {
  clearPopoverTimers();
  citationPopover.current?.anchor.classList.remove('is-open');
  citationPopover.current = { id: ++cardSeq, anchor, annotations, pinned };
  anchor.classList.add('is-open');
}

export function closeCitationPopover() {
  clearPopoverTimers();
  const card = citationPopover.current;
  if (!card) return;
  card.anchor.classList.remove('is-open');
  citationPopover.current = null;
  lastClosedAt = performance.now();
}

function schedulePopoverClose() {
  clearTimeout(closeTimer);
  closeTimer = setTimeout(() => {
    if (!citationPopover.current?.pinned) closeCitationPopover();
  }, HOVER_CLOSE_DELAY);
}

/** 指针进入卡片：取消关闭，并取消途中扫过其他引用排下的打开（CitationCard 调用） */
export function keepCitationPopover() {
  clearTimeout(closeTimer);
  clearTimeout(openTimer);
}

/** 指针离开卡片：未固定时按延迟关闭 */
export function leaveCitationPopover() {
  if (citationPopover.current && !citationPopover.current.pinned) schedulePopoverClose();
}

/** 指针进入引用。注意不取消旧卡片的关闭计时：旧卡片按自己的节奏收起，
 *  新卡片打开时再替换它；从引用移向卡片途中扫过的引用在进入卡片时被取消。 */
export function hoverCitationChip(chip: ChipWithAnnotations) {
  const card = citationPopover.current;
  if (card?.pinned) return; // 固定期间悬停其他引用不替换
  if (card?.anchor === chip) {
    clearTimeout(closeTimer);
    return;
  }
  clearTimeout(openTimer);
  openTimer = setTimeout(
    () => openCitationPopover(chip, chip._citationAnnotations || [], false),
    isWarm() ? HOVER_OPEN_DELAY_WARM : HOVER_OPEN_DELAY
  );
}

export function leaveCitationChip(chip: ChipWithAnnotations) {
  clearTimeout(openTimer);
  const card = citationPopover.current;
  if (card?.anchor === chip && !card.pinned) schedulePopoverClose();
}

/** 点击引用：悬停打开的原地固定；再点已固定的收起；点其他引用切换并固定 */
export function clickCitationChip(chip: ChipWithAnnotations) {
  const card = citationPopover.current;
  if (card?.anchor === chip) {
    if (card.pinned) {
      closeCitationPopover();
    } else {
      clearPopoverTimers();
      card.pinned = true;
    }
    return;
  }
  openCitationPopover(chip, chip._citationAnnotations || [], true);
}

function attachChipTriggers(chip: ChipWithAnnotations) {
  // 事件时从元素上读最新 annotations（final 富化替换后无需重绑监听器）
  chip.addEventListener('mouseenter', () => hoverCitationChip(chip));
  chip.addEventListener('mouseleave', () => leaveCitationChip(chip));
  chip.addEventListener('click', (e) => {
    e.stopPropagation();
    clickCitationChip(chip);
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
    file_name: path.split('/').pop() || path
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
  return ann.type === 'file_citation'
    ? ann.file_name || ann.file_path || ''
    : shortDomain(ann.domain);
}

const FILE_ICON_SVG =
  '<svg class="chip-file-icon" viewBox="0 0 16 16" fill="none">' +
  '<path d="M4 1.5h5.5L13 5v9.5H4z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>' +
  '<path d="M9.5 1.5V5H13" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>';

function chipIconHtml(ann: CitationAnnotation): string {
  // 先渲染首字母兜底，真实 favicon 由 upgradeCitationFavicons 竞速成功后原地替换
  return ann.type === 'file_citation' ? FILE_ICON_SVG : faviconFallbackHtml(ann.domain || '');
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
  opts: { final?: boolean } = {}
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
        const enriched = ids.map((id) => map.get(id)).filter((a): a is CitationAnnotation => !!a);
        if (enriched.length) chip._citationAnnotations = enriched;
      }
      return;
    }

    let annotations = ids.map((id) => map.get(id)).filter((a): a is CitationAnnotation => !!a);

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

  // 字母占位统一走多源竞速升级（域名级缓存，重复调用无副作用）
  upgradeCitationFavicons(container);
}

function escapeText(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
