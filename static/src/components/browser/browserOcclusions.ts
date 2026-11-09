import type { BrowserRect } from './types';

/** CSS viewport coordinates; native code applies the main view offset, DPI and zoom. */
export function browserOcclusions(surface: HTMLElement, selector: string): BrowserRect[] {
  const bounds = surface.getBoundingClientRect();
  const cuts: BrowserRect[] = [];
  for (const candidate of document.querySelectorAll<HTMLElement>(selector)) {
    if (candidate === surface || candidate.contains(surface) || !candidate.getClientRects().length)
      continue;
    const style = getComputedStyle(candidate);
    if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) === 0)
      continue;
    const rect = candidate.getBoundingClientRect();
    const x = Math.max(rect.left, bounds.left);
    const y = Math.max(rect.top, bounds.top);
    const right = Math.min(rect.right, bounds.right);
    const bottom = Math.min(rect.bottom, bounds.bottom);
    if (right <= x || bottom <= y) continue;
    cuts.push({ x, y, width: right - x, height: bottom - y });
    // More than 128 independent overlays cannot be represented by the native contract.
    // Cover the entire surface so no DOM control is hidden behind a native page.
    if (cuts.length === 128)
      return [{ x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height }];
  }
  return cuts;
}
