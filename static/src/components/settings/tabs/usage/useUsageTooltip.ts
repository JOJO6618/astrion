import { onBeforeUnmount, onMounted, reactive } from 'vue';
import { emptyTooltip } from './types';

/**
 * 用量统计页图表共用的悬浮提示状态。
 * 任何滚动（设置页内容区、热力图横向滚动、窗口）或窗口尺寸变化都立即收起：
 * 提示框是 fixed 定位，滚动后会与触发元素错位。滚动事件不冒泡，因此在 window 上用捕获阶段监听。
 */
export function useUsageTooltip() {
  const tip = reactive(emptyTooltip());

  function hide() {
    tip.visible = false;
  }

  function show(target: Element, title: string, main: string, rows: [string, string][] = []) {
    const rect = target.getBoundingClientRect();
    showAtRect(rect, title, main, rows);
  }

  function showAtRect(
    rect: { left: number; top: number; width: number; bottom: number },
    title: string,
    main: string,
    rows: [string, string][] = []
  ) {
    Object.assign(tip, {
      visible: true,
      title,
      main,
      rows,
      anchor: { left: rect.left, top: rect.top, width: rect.width, bottom: rect.bottom }
    });
  }

  const listenerOptions: AddEventListenerOptions = { capture: true, passive: true };
  onMounted(() => {
    window.addEventListener('scroll', hide, listenerOptions);
    window.addEventListener('resize', hide);
  });
  onBeforeUnmount(() => {
    window.removeEventListener('scroll', hide, listenerOptions);
    window.removeEventListener('resize', hide);
  });

  return { tip, hide, show, showAtRect };
}
