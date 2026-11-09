import { computed, ref, watch, nextTick, onMounted, onBeforeUnmount, type InjectionKey } from 'vue';
import { t } from '@/locales';
import { useNativeBrowserStore, type BrowserSection } from '@/stores/nativeBrowser';
import { useBrowserSurface } from './useBrowserSurface';
import { displayBrowserAddress } from './address';
import { useAddressSuggestions } from './useAddressSuggestions';
import { browserTabLabel } from './internalPages';
import type { BrowserEvent } from './types';

export function useBrowserControls() {
  const store = useNativeBrowserStore();
  const panel = ref<HTMLElement | null>(null),
    surface = ref<HTMLElement | null>(null),
    address = ref<HTMLInputElement | null>(null),
    findInput = ref<HTMLInputElement | null>(null);
  const addressText = ref(''),
    newtabText = ref(''),
    dragged = ref(''),
    picker = ref(false),
    tabQuery = ref(''),
    siteInfo = ref(false),
    downloadsOpen = ref(false);
  const context = ref<{
    x: number;
    y: number;
    type: 'tab' | 'page';
    id: string;
    copy?: boolean;
    editable?: boolean;
  } | null>(null);
  const addressSuggestions = useAddressSuggestions(addressText);
  const permission = computed(() => store.snapshot?.permissions[0]);
  const permissionName = computed(() =>
    t(
      `browser.${({ 'clipboard-read': 'clipboard' } as Record<string, string>)[permission.value?.permission || ''] || permission.value?.permission || 'permissionTitle'}`
    )
  );
  const filteredTabs = computed(() =>
    store.tabs.filter((tab) =>
      `${browserTabLabel(tab)} ${tab.url}`.toLowerCase().includes(tabQuery.value.toLowerCase())
    )
  );
  const contextActions = computed(() =>
    context.value?.type === 'tab'
      ? [
          { action: 'duplicate', label: 'duplicate' },
          { action: 'close', label: 'closeTab' },
          { action: 'closeOthers', label: 'closeOthers' }
        ]
      : [
          { action: 'back', label: 'back' },
          { action: 'reload', label: 'reload' },
          ...(context.value?.copy ? [{ action: 'copy', label: 'copy' }] : []),
          ...(context.value?.editable
            ? [
                { action: 'paste', label: 'paste' },
                { action: 'selectAll', label: 'selectAll' }
              ]
            : []),
          { action: 'find', label: 'find' }
        ]
  );
  const { force } = useBrowserSurface(
    surface,
    () =>
      !store.open ||
      !!store.section ||
      store.navigationPending ||
      !!store.active?.pending ||
      !store.active ||
      store.active.url === 'about:blank' ||
      !!store.active.error,
    () =>
      store.menu ||
      picker.value ||
      siteInfo.value ||
      downloadsOpen.value ||
      addressSuggestions.open.value ||
      !!context.value ||
      !!permission.value ||
      store.resizing,
    () =>
      store.findOpen ||
      !!store.error ||
      !!store.snapshot?.credentials?.prompt ||
      !!store.snapshot?.credentials?.fill
  );
  watch(
    [() => store.active?.id, () => store.active?.url],
    () => {
      addressText.value = displayBrowserAddress(store.active?.url);
      store.findText = '';
      store.findOpen = false;
      closePopovers();
    },
    { immediate: true }
  );
  watch(permission, (value) => {
    if (value)
      void nextTick(() =>
        panel.value?.querySelector<HTMLButtonElement>('.nb-permission button')?.focus()
      );
  });
  function closePopovers() {
    addressSuggestions.close();
    store.menu = false;
    picker.value = false;
    siteInfo.value = false;
    downloadsOpen.value = false;
    context.value = null;
  }
  function toggleMenu() {
    const show = !store.menu;
    closePopovers();
    store.menu = show;
  }
  function togglePicker() {
    const show = !picker.value;
    closePopovers();
    picker.value = show;
  }
  function toggleSite() {
    const show = !siteInfo.value;
    closePopovers();
    siteInfo.value = show;
  }
  function toggleDownloads() {
    const show = !downloadsOpen.value;
    closePopovers();
    downloadsOpen.value = show;
  }
  let quietAddressFocus = false;
  function focusAddress(event: FocusEvent) {
    closePopovers();
    addressSuggestions.focus(quietAddressFocus);
    quietAddressFocus = false;
    (event.target as HTMLInputElement).select();
  }
  function chooseAddress(url: string) {
    closePopovers();
    void store.navigate(url);
    address.value?.blur();
  }
  function navigate() {
    const selected = addressSuggestions.open.value
      ? addressSuggestions.items.value[addressSuggestions.selected.value]
      : undefined;
    if (selected) {
      chooseAddress(selected.url);
      return;
    }
    closePopovers();
    const currentURL = store.active?.url;
    const destination =
      currentURL && addressText.value === displayBrowserAddress(currentURL)
        ? currentURL
        : addressText.value;
    void store.navigate(destination);
    address.value?.blur();
  }
  async function newTab() {
    closePopovers();
    await store.command('new');
    await nextTick();
    const input = address.value;
    if (!input) return;
    // New tabs land in the address bar like other browsers, without popping recent visits.
    if (document.activeElement === input) {
      addressSuggestions.focus(true);
      input.select();
    } else {
      // Kept until the focus event consumes it: if the window regains OS focus later,
      // the deferred focus event must still stay quiet.
      quietAddressFocus = true;
      input.focus();
    }
  }
  function selectTab(id: string) {
    closePopovers();
    void store.command('select', undefined, id);
  }
  function find(next = false, forward = true) {
    if (store.active && !store.section)
      void store.command('find', { text: store.findText, next, forward });
  }
  function closeFind() {
    store.findOpen = false;
    if (store.active && !store.section) void store.command('find', { text: '' });
  }
  async function menuAction(action: string) {
    closePopovers();
    if (['history', 'downloads', 'settings', 'passwords', 'import', 'clear'].includes(action))
      store.showSection(action as BrowserSection);
    else if (action === 'find') {
      if (!store.active || store.section) return;
      store.findOpen = true;
      await nextTick();
      findInput.value?.focus();
    } else if (action.startsWith('device-')) device(action === 'device-mobile');
    else {
      await store.command(action);
      force();
    }
  }
  function device(mobile: boolean) {
    store.device = mobile;
    closePopovers();
    void store.command(
      'device',
      mobile ? { width: Number(store.deviceWidth), height: Number(store.deviceHeight) } : null
    );
  }
  function rotate() {
    const width = store.deviceWidth;
    store.deviceWidth = store.deviceHeight;
    store.deviceHeight = width;
    device(true);
  }
  function shortcut(event: BrowserEvent) {
    if (event.key === 'l') {
      address.value?.focus();
      address.value?.select();
    } else if (event.key === 'f') void menuAction('find');
    else if (event.key === 't') void newTab();
    else if (event.key === 'w' && store.active) void store.command('close');
    else if (event.key === '0' && store.active && !store.section) void store.command('zoom', 100);
    else if (store.active && !store.section && ['+', '=', '-'].includes(event.key || ''))
      void store.command('zoom', (store.active?.zoom || 100) + (event.key === '-' ? -10 : 10));
  }
  function keydown(event: KeyboardEvent) {
    if (permission.value && event.key === 'Tab') {
      event.preventDefault();
      const buttons = [
        ...(panel.value?.querySelectorAll<HTMLButtonElement>('.nb-permission button') || [])
      ];
      const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
      buttons[(index + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length]?.focus();
      return;
    }
    if (event.key === 'Escape') {
      closePopovers();
      if (store.findOpen) closeFind();
      return;
    }
    if (
      (event.metaKey || event.ctrlKey) &&
      ['l', 'f', 't', 'w', '+', '=', '-', '0'].includes(event.key.toLowerCase())
    ) {
      event.preventDefault();
      event.stopPropagation();
      shortcut({ key: event.key.toLowerCase() });
    }
  }
  function tabKeys(event: KeyboardEvent, id: string) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Delete', 'Enter', ' '].includes(event.key))
      return;
    event.preventDefault();
    if (event.key === 'Delete') {
      void store.command('close', undefined, id);
      return;
    }
    const index = store.tabs.findIndex((tab) => tab.id === id);
    const next =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? store.tabs.length - 1
          : (index +
              (event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0) +
              store.tabs.length) %
            store.tabs.length;
    if (store.tabs[next]) {
      selectTab(store.tabs[next].id);
      void nextTick(() =>
        panel.value?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus()
      );
    }
  }
  function dragStart(event: DragEvent, id: string) {
    dragged.value = id;
    event.dataTransfer?.setData('text/plain', id);
  }
  function drop(id: string) {
    const order = store.tabs.map((tab) => tab.id),
      from = order.indexOf(dragged.value),
      to = order.indexOf(id);
    if (from < 0 || to < 0) return;
    order.splice(to, 0, order.splice(from, 1)[0]);
    dragged.value = '';
    void store.command('reorder', order);
  }
  function tabContext(event: MouseEvent, id: string) {
    closePopovers();
    const box = panel.value!.getBoundingClientRect();
    context.value = {
      x: Math.max(8, Math.min(event.clientX - box.x, box.width - 220)),
      y: Math.min(event.clientY - box.y, box.height - 140),
      type: 'tab',
      id
    };
  }
  function pageContext(event: BrowserEvent) {
    closePopovers();
    const box = panel.value!.getBoundingClientRect(),
      page = surface.value!.getBoundingClientRect();
    context.value = {
      x: Math.max(8, Math.min((event.x || 0) + page.left - box.left, box.width - 220)),
      y: Math.max(8, Math.min((event.y || 0) + page.top - box.top, box.height - 190)),
      type: 'page',
      id: event.tabId || '',
      copy: event.canCopy,
      editable: event.editable
    };
  }
  async function contextAction(action: string) {
    const id = context.value?.id;
    closePopovers();
    if (action === 'closeOthers') {
      for (const tab of [...store.tabs])
        if (tab.id !== id) await store.command('close', undefined, tab.id);
    } else if (action === 'find') await menuAction('find');
    else await store.command(action, undefined, id);
  }
  let stopResize: (() => void) | undefined;
  function startResize(event: PointerEvent) {
    event.preventDefault();
    const handle = event.currentTarget as HTMLElement;
    handle.setPointerCapture(event.pointerId);
    store.resizing = true;
    const move = (moveEvent: PointerEvent) => store.setWidth(innerWidth - moveEvent.clientX);
    const stop = () => {
      store.resizing = false;
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', stop);
      handle.removeEventListener('pointercancel', stop);
      force();
      stopResize = undefined;
    };
    stopResize = stop;
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', stop);
    handle.addEventListener('pointercancel', stop);
  }
  function outside(event: MouseEvent) {
    const target = event.target as Node;
    const insidePopover =
      target instanceof Element &&
      target.closest('.nb-floating-status, .nb-popover, .nb-menu, .nb-permission-scrim');
    if (!insidePopover && (!panel.value?.contains(target) || surface.value?.contains(target)))
      closePopovers();
  }
  onMounted(() => document.addEventListener('pointerdown', outside));
  onBeforeUnmount(() => {
    document.removeEventListener('pointerdown', outside);
    stopResize?.();
  });
  return {
    store,
    panel,
    surface,
    address,
    findInput,
    addressText,
    addressSuggestions,
    focusAddress,
    chooseAddress,
    newtabText,
    dragged,
    picker,
    tabQuery,
    siteInfo,
    downloadsOpen,
    context,
    permission,
    permissionName,
    filteredTabs,
    contextActions,
    toggleMenu,
    togglePicker,
    toggleSite,
    toggleDownloads,
    navigate,
    newTab,
    selectTab,
    find,
    closeFind,
    menuAction,
    device,
    rotate,
    shortcut,
    keydown,
    tabKeys,
    dragStart,
    drop,
    tabContext,
    pageContext,
    contextAction,
    startResize
  };
}
export type BrowserControls = ReturnType<typeof useBrowserControls>;
export const browserControlsKey: InjectionKey<BrowserControls> = Symbol('browser-controls');
