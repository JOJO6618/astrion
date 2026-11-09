import { onMounted, onBeforeUnmount, watch, type Ref } from 'vue';
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
import { browserBridge } from './types';
import { useBrowserLayer } from './useBrowserLayer';
import { browserOcclusions } from './browserOcclusions';

const floatingSelector = [
  '[data-native-browser-obscures]',
  '[role="tooltip"]',
  '[role="dialog"]',
  '[role="menu"]',
  '[aria-modal="true"]',
  '[class*="overlay"]',
  '[class*="backdrop"]',
  '.personal-menu',
  '.qd-menu',
  '.token-drawer',
  '.toast-stack',
  '.quota-toast',
  '.context-menu',
  '.citation-popover',
  '.cost-popover',
  '.quick-menu',
  '.settings-floating-menu',
  '.ws-menu-floating',
  '.conversation-actions-menu--fixed',
  '.workspace-actions-menu',
  '.file-at-menu',
  '.skill-slash-menu',
  '.agent-type-switcher__menu',
  '.permission-switcher__menu',
  '.floating-project-status__submenu',
  '.codex-account-menu',
  '.codex-weekly__menu'
].join(', ');

export function useBrowserSurface(
  surface: Ref<HTMLElement | null>,
  hidden: () => boolean,
  popups: () => boolean,
  floating: () => boolean = () => false
) {
  const store = useNativeBrowserStore();
  let frame = 0,
    previous = '',
    stopped = false,
    capturesInput = false,
    previousColor = '';
  let resize: ResizeObserver, mutations: MutationObserver, unsubscribe: (() => void) | undefined;
  const layer = useBrowserLayer(surface, () => !popups() && !capturesInput);
  function sync() {
    frame = 0;
    if (stopped) return;
    const surfaceColor = getComputedStyle(document.documentElement)
      .getPropertyValue('--surface-base')
      .trim();
    if (surfaceColor !== previousColor && /^#[a-f\d]{6}$/i.test(surfaceColor)) {
      previousColor = surfaceColor;
      browserBridge()?.theme(surfaceColor);
    }
    if (!store.snapshot) {
      layer.set(undefined, false);
      return;
    }
    const node = surface.value;
    const bounds = node?.getBoundingClientRect();
    const visible =
      !hidden() && !document.hidden && !!bounds && bounds.width >= 1 && bounds.height >= 1;
    let elevated = popups() || floating();
    capturesInput = popups();
    if (bounds && visible) {
      for (const candidate of document.querySelectorAll<HTMLElement>(floatingSelector)) {
        if (
          candidate.contains(node || null) ||
          node?.contains(candidate) ||
          candidate.closest('.native-browser-panel') ||
          !candidate.getClientRects().length
        )
          continue;
        const style = getComputedStyle(candidate);
        if (
          style.visibility === 'hidden' ||
          style.display === 'none' ||
          (style.pointerEvents === 'none' && !candidate.matches('[role="tooltip"]'))
        )
          continue;
        const rect = candidate.getBoundingClientRect();
        if (
          rect.width > 0 &&
          rect.height > 0 &&
          rect.left < bounds.right &&
          rect.right > bounds.left &&
          rect.top < bounds.bottom &&
          rect.bottom > bounds.top
        ) {
          elevated = true;
          if (!candidate.matches('.toast-stack, .quota-toast, .citation-popover, .cost-popover'))
            capturesInput = true;
        }
      }
      if (document.querySelector('.native-browser-panel [data-tooltip]:hover')) elevated = true;
    }
    elevated = visible && elevated;
    layer.set(bounds, visible, elevated);
    const rect = bounds
      ? { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height }
      : null;
    const panelBounds = node?.closest('.native-browser-panel')?.getBoundingClientRect();
    const metadata = {
      surfaceColor,
      ...(node && visible && browserBridge()?.surfaceMode === 'regions'
        ? {
            occlusions: browserOcclusions(
              node,
              // Cut only the modal box: a scrim-sized hole would remove the whole page behind it.
              `${floatingSelector}, .nb-popover, .nb-menu, .nb-floating-status, .nb-credential-floating, .nb-permission`
            )
          }
        : {}),
      focusFind: store.findOpen,
      viewport: {
        width: innerWidth,
        height: innerHeight,
        panelWidth: store.width,
        surfaceInset: Math.max(0, (panelBounds?.width || bounds?.width || 0) - (bounds?.width || 0))
      }
    };
    const signature = JSON.stringify([
      rect,
      visible,
      elevated,
      capturesInput,
      metadata,
      store.snapshot.lease
    ]);
    if (signature !== previous) {
      previous = signature;
      store.geometry(rect, visible, elevated, capturesInput, metadata);
    }
  }
  const schedule = () => {
    if (!frame && !stopped) frame = requestAnimationFrame(sync);
  };
  const force = () => {
    previous = '';
    schedule();
  };
  watch(
    () => [
      store.open,
      store.snapshot?.revision,
      store.section,
      store.menu,
      store.device,
      store.resizing,
      store.width,
      store.findOpen,
      hidden(),
      popups(),
      floating()
    ],
    schedule,
    { flush: 'post' }
  );
  watch(surface, (next, old) => {
    if (old) resize?.unobserve(old);
    if (next) resize?.observe(next);
    schedule();
  });
  onMounted(() => {
    resize = new ResizeObserver(schedule);
    if (surface.value) resize.observe(surface.value);
    mutations = new MutationObserver(schedule);
    mutations.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['class', 'style', 'hidden', 'open', 'aria-hidden', 'data-theme']
    });
    mutations.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme']
    });
    layer.start();
    window.addEventListener('resize', force);
    window.addEventListener('scroll', schedule, true);
    window.addEventListener('popstate', force);
    document.addEventListener('pointerover', schedule, true);
    document.addEventListener('pointerout', schedule, true);
    document.addEventListener('visibilitychange', force);
    unsubscribe = browserBridge()?.onGeometryRequest(force);
    schedule();
  });
  onBeforeUnmount(() => {
    store.geometry(null, false);
    stopped = true;
    cancelAnimationFrame(frame);
    resize?.disconnect();
    mutations?.disconnect();
    unsubscribe?.();
    layer.dispose();
    window.removeEventListener('resize', force);
    window.removeEventListener('scroll', schedule, true);
    window.removeEventListener('popstate', force);
    document.removeEventListener('pointerover', schedule, true);
    document.removeEventListener('pointerout', schedule, true);
    document.removeEventListener('visibilitychange', force);
  });
  return { schedule, force };
}
