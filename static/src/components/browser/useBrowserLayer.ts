import type { Ref } from 'vue';
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
import { browserBridge, type BrowserInput } from './types';

export function useBrowserLayer(surface: Ref<HTMLElement | null>, inputAllowed: () => boolean) {
  const store = useNativeBrowserStore();
  let backdrop: HTMLDivElement | null = null;
  let raised = false;
  let dragging = false;
  function set(bounds: DOMRect | undefined, visible: boolean, elevated = false) {
    raised = visible && elevated && !!bounds;
    document.documentElement.classList.toggle('nb-native-overlay', visible && !!bounds);
    if (!visible || !bounds) {
      backdrop?.remove();
      backdrop = null;
      dragging = false;
      return;
    }
    if (!backdrop) {
      backdrop = document.createElement('div');
      backdrop.className = 'nb-native-underlay';
      backdrop.setAttribute('aria-hidden', 'true');
      for (let i = 0; i < 4; i++) backdrop.appendChild(document.createElement('span'));
      document.body.appendChild(backdrop);
    }
    const left = Math.max(0, Math.min(innerWidth, bounds.left));
    const top = Math.max(0, Math.min(innerHeight, bounds.top));
    const right = Math.max(left, Math.min(innerWidth, bounds.right));
    const bottom = Math.max(top, Math.min(innerHeight, bounds.bottom));
    const styles = [
      `left:0;right:0;top:0;height:${top}px`,
      `left:0;top:${top}px;width:${left}px;height:${bottom - top}px`,
      `left:${right}px;right:0;top:${top}px;height:${bottom - top}px`,
      `left:0;right:0;top:${bottom}px;bottom:0`
    ];
    Array.from(backdrop.children).forEach((node, index) => {
      if (node.getAttribute('style') !== styles[index]) node.setAttribute('style', styles[index]);
    });
  }
  function send(event: PointerEvent | WheelEvent, type: BrowserInput['type']) {
    if (!raised || !inputAllowed() || !store.snapshot || !store.active) return;
    if (
      event.target instanceof Element &&
      event.target.closest('.nb-floating-status, .nb-credential-floating')
    )
      return;
    if (!dragging && !(event.target instanceof Node && surface.value?.contains(event.target)))
      return;
    const modifiers = [
      event.shiftKey && 'shift',
      event.ctrlKey && 'control',
      event.altKey && 'alt',
      event.metaKey && 'meta',
      event.buttons & 1 && 'leftButtonDown',
      event.buttons & 2 && 'rightButtonDown',
      event.buttons & 4 && 'middleButtonDown'
    ].filter(Boolean) as string[];
    const wheelScale =
      event instanceof WheelEvent
        ? event.deltaMode === 1
          ? 16
          : event.deltaMode === 2
            ? surface.value?.clientHeight || 1
            : 1
        : 1;
    browserBridge()?.input({
      lease: store.snapshot.lease,
      tabId: store.active.id,
      type,
      x: event.clientX,
      y: event.clientY,
      button: event.button,
      clickCount: event.detail || 1,
      modifiers,
      ...(event instanceof WheelEvent
        ? { deltaX: event.deltaX * wheelScale, deltaY: event.deltaY * wheelScale }
        : {})
    });
    if (type === 'mouseDown') dragging = true;
    if (type === 'mouseUp') dragging = false;
    event.preventDefault();
    event.stopImmediatePropagation();
  }
  const down = (event: PointerEvent) => send(event, 'mouseDown');
  const move = (event: PointerEvent) => send(event, 'mouseMove');
  const up = (event: PointerEvent) => send(event, 'mouseUp');
  const wheel = (event: WheelEvent) => send(event, 'mouseWheel');
  function start() {
    document.addEventListener('pointerdown', down, true);
    document.addEventListener('pointermove', move, true);
    document.addEventListener('pointerup', up, true);
    document.addEventListener('wheel', wheel, { capture: true, passive: false });
  }
  function dispose() {
    set(undefined, false);
    document.removeEventListener('pointerdown', down, true);
    document.removeEventListener('pointermove', move, true);
    document.removeEventListener('pointerup', up, true);
    document.removeEventListener('wheel', wheel, true);
  }
  return { set, start, dispose };
}
