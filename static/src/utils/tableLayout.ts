/**
 * Markdown 表格布局模式判定（换行 / 横向滚动）。
 *
 * 测量表格内容的自然宽度（所有单元格单行不折行时的宽度），与滚动容器可用宽度比较：
 * - 自然宽度 ≤ 可用宽度 × 阈值 → 换行模式（默认，单元格折行、表格占满内容区）
 * - 自然宽度 > 可用宽度 × 阈值 → 容器打 data-md-table-mode="scroll"，退化为整表横向滚动
 *
 * 样式侧见 styles/components/chat/_chat-area.scss 的表格段。
 */

/** 自然宽度超出可用宽度的该倍数以内时，仍用换行模式吸收（避免略宽一点的表被迫滚动） */
const SCROLL_RATIO_THRESHOLD = 1.35;

const MODE_ATTR = 'data-md-table-mode';

const TABLE_SCROLLER_SELECTOR = '.md-table-scroll, [data-md-table-scroll="1"]';

function measureNaturalWidth(table: HTMLTableElement): number {
  // 同一帧内临时切到「自然宽度 + 全部不折行」布局测量，读完立即恢复——
  // 浏览器不会在两次样式变更之间绘制，无闪烁
  const prevWidth = table.style.width;
  table.style.width = 'max-content';
  const cells = table.querySelectorAll('th, td');
  const savedWhiteSpace: string[] = [];
  cells.forEach((cell, i) => {
    const el = cell as HTMLElement;
    savedWhiteSpace[i] = el.style.whiteSpace;
    el.style.whiteSpace = 'nowrap';
  });
  const width = table.offsetWidth;
  table.style.width = prevWidth;
  cells.forEach((cell, i) => {
    (cell as HTMLElement).style.whiteSpace = savedWhiteSpace[i];
  });
  return width;
}

function availableWidth(scroller: HTMLElement): number {
  const style = getComputedStyle(scroller);
  return (
    scroller.clientWidth -
    parseFloat(style.paddingLeft || '0') -
    parseFloat(style.paddingRight || '0')
  );
}

function classifyOne(scroller: HTMLElement): void {
  const table = scroller.querySelector('table');
  if (!table) return;
  const avail = availableWidth(scroller);
  // 容器不可见（折叠块/未激活标签页内）时宽度为 0，跳过，等可见后由 ResizeObserver 补判
  if (avail <= 0) return;
  const natural = measureNaturalWidth(table);
  const mode = natural <= avail * SCROLL_RATIO_THRESHOLD ? 'wrap' : 'scroll';
  if (scroller.getAttribute(MODE_ATTR) !== mode) {
    scroller.setAttribute(MODE_ATTR, mode);
  }
}

/** 扫描 root 下所有 markdown 表格滚动容器，按当前可用宽度重新判定布局模式（幂等） */
export function classifyMarkdownTables(root: ParentNode): void {
  root.querySelectorAll<HTMLElement>(TABLE_SCROLLER_SELECTOR).forEach(classifyOne);
}

/**
 * 监听 root 下所有表格滚动容器的宽度变化（窗口缩放 / 侧栏开合），宽度变化时自动重判。
 * 返回停止函数。仅宽度变化触发，折行引起的高度变化不会重复测量。
 */
export function observeMarkdownTables(root: ParentNode): () => void {
  if (typeof ResizeObserver === 'undefined') return () => {};
  const scrollers = Array.from(root.querySelectorAll<HTMLElement>(TABLE_SCROLLER_SELECTOR));
  if (!scrollers.length) return () => {};
  const lastWidths = new WeakMap<HTMLElement, number>();
  const observer = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const el = entry.target as HTMLElement;
      const width = entry.contentRect.width;
      const last = lastWidths.get(el);
      if (last === undefined || Math.abs(width - last) > 1) {
        lastWidths.set(el, width);
        classifyOne(el);
      }
    }
  });
  scrollers.forEach((el) => observer.observe(el));
  return () => observer.disconnect();
}
