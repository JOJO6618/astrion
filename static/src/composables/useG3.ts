import { watch, type Ref } from 'vue';
import { g3Masks } from '@/utils/g3Contours';

/**
 * 单元素 G3 轮廓：只绘制传入的元素本身。
 * observeG3(root) 会连同子孙 [data-g3] 一起观察，弹窗和按钮各自挂载时会重复绘制；
 * 抽成公共组件后弹窗外框和按钮各管各的，改用这个。参数变化（按钮主次切换）时重绘。
 */
export function useG3(target: Ref<HTMLElement | null | undefined>, param: () => number) {
  watch(
    [target, param],
    ([element, value], _old, onCleanup) => {
      if (!element) return;
      let key = '';
      const paint = () => {
        const width = element.offsetWidth;
        const height = element.offsetHeight;
        const next = `${width}:${height}:${value}:${window.devicePixelRatio}`;
        if (!width || !height || next === key) return;
        const images = g3Masks(width, height, value * 2);
        if (!images) return;
        element.style.setProperty('--g3-mask', `url("${images.mask}")`);
        element.style.setProperty('--g3-outline', `url("${images.outline}")`);
        element.dataset.g3Ready = 'true';
        key = next;
      };
      paint();
      const observer = new ResizeObserver(paint);
      observer.observe(element);
      onCleanup(() => observer.disconnect());
    },
    { immediate: true, flush: 'post' }
  );
}
