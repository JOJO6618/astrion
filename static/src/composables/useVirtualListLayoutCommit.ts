import { onBeforeUnmount, watch, type Ref } from 'vue';

/**
 * virtua 的条目 RO 更新缓存，再由 Vue 提交列表 height 与偏移。
 * 该提交可能发生在本帧外层 RO 已交付之后；再等外层 RO 会迟一帧。
 * 只观察列表根的尺寸样式提交，在绘制前通知已有追底引擎，不直接写 scrollTop。
 */
export function useVirtualListLayoutCommit(
  contentRef: Ref<HTMLElement | null>,
  virtualizerRef: Ref<unknown>,
  onCommit: (element: HTMLElement) => void
) {
  let observer: MutationObserver | null = null;
  let generation = 0;

  function disconnect() {
    generation += 1;
    observer?.disconnect();
    observer = null;
  }

  const stopWatch = watch(
    [contentRef, virtualizerRef],
    () => {
      disconnect();
      if (typeof MutationObserver === 'undefined' || !virtualizerRef.value) return;
      const element = contentRef.value?.querySelector<HTMLElement>('.chat-message-list');
      if (!element) return;
      const currentGeneration = generation;
      let height = element.style.height;
      let width = element.style.width;
      observer = new MutationObserver(() => {
        if (currentGeneration !== generation || !element.isConnected) return;
        const nextHeight = element.style.height;
        const nextWidth = element.style.width;
        // 忽略 virtua 的 pointer-events 等非尺寸样式，不观察条目/内容子树。
        if (height === nextHeight && width === nextWidth) return;
        height = nextHeight;
        width = nextWidth;
        onCommit(element);
      });
      observer.observe(element, { attributes: true, attributeFilter: ['style'] });
      onCommit(element);
    },
    { flush: 'post', immediate: true }
  );

  onBeforeUnmount(() => {
    stopWatch();
    disconnect();
  });
}
