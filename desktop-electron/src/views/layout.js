// 主窗口/独立窗口的视图几何（纯函数，不依赖 Electron）。

/** 顶部 chrome 标签条高度（CSS px）。前端 _tab-strip.scss 的 46px 与内容视图预留空间都对齐这个值；
 *  改动需三处同步。 */
export const CHROME_STRIP_HEIGHT = 46;

/** chrome 条：钉在顶部，全宽 × 46px。 */
export function chromeBounds({ width }) {
  return { x: 0, y: 0, width, height: CHROME_STRIP_HEIGHT };
}

/** 主窗口内容区：chrome 条之下的剩余区域（y = 46）。 */
export function mainContentBounds({ width, height }) {
  return { x: 0, y: CHROME_STRIP_HEIGHT, width, height: Math.max(0, height - CHROME_STRIP_HEIGHT) };
}

/** TODO(P2) 独立对话窗口：铺满窗口（y = 0），顶部栏由页面自己绘制。 */
export function detachedContentBounds({ width, height }) {
  return { x: 0, y: 0, width, height };
}

/** 独立对话窗口页面自绘顶部栏高度（前端 _detached-view.scss 的 --detached-bar-height；红绿灯/WCO 按它居中）。 */
export const DETACHED_BAR_HEIGHT = 38;

/** 传给 Electron 原生接口（setPosition/setBounds 等，gin 整数转换）的坐标：-0、NaN、小数都会让它抛错。
 *  有限数 → 四舍五入后的整数（-0 → 0）；否则 null（调用方应跳过该次调用）。 */
export const toInt = (value) => (Number.isFinite(value) ? Math.round(value) | 0 : null);
