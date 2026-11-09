import { computed, ref, type Ref } from 'vue';
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
import type { BrowserHistory } from './types';

export function recentAddresses(history: BrowserHistory[], query: string) {
  const needle = query
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, '')
    .replace(/^www\./, '');
  const seen = new Set<string>();
  return [...history]
    .sort((a, b) => b.at - a.at)
    .filter((row) => {
      if (!/^https?:\/\//i.test(row.url) || seen.has(row.url)) return false;
      seen.add(row.url);
      const address = row.url
        .toLowerCase()
        .replace(/^https?:\/\//, '')
        .replace(/^www\./, '');
      return !needle || address.includes(needle) || row.title.toLowerCase().includes(needle);
    })
    .slice(0, 8);
}
export function useAddressSuggestions(text: Ref<string>) {
  const store = useNativeBrowserStore();
  const focused = ref(false),
    edited = ref(false),
    dismissed = ref(false),
    selected = ref(-1);
  const items = computed(() =>
    recentAddresses(store.snapshot?.history || [], edited.value ? text.value : '')
  );
  const open = computed(() => focused.value && !dismissed.value && items.value.length > 0);
  /** quiet: focus without showing recent visits until the user types or presses an arrow key. */
  function focus(quiet = false) {
    focused.value = true;
    edited.value = false;
    dismissed.value = quiet;
    selected.value = -1;
  }
  /** Clicking an already focused, quiet address bar brings the list back. */
  function reopen() {
    if (focused.value) dismissed.value = false;
  }
  function close() {
    dismissed.value = true;
    selected.value = -1;
  }
  function input() {
    edited.value = true;
    dismissed.value = false;
    const prefix = text.value
      .trim()
      .toLowerCase()
      .replace(/^https?:\/\//, '')
      .replace(/^www\./, '');
    selected.value = prefix
      ? items.value.findIndex((row) =>
          row.url
            .toLowerCase()
            .replace(/^https?:\/\//, '')
            .replace(/^www\./, '')
            .startsWith(prefix)
        )
      : -1;
  }
  function keydown(event: KeyboardEvent) {
    if (event.isComposing) return;
    if (event.key === 'Escape') {
      close();
      event.preventDefault();
      event.stopPropagation();
    } else if (['ArrowDown', 'ArrowUp'].includes(event.key) && items.value.length) {
      dismissed.value = false;
      selected.value =
        selected.value < 0
          ? event.key === 'ArrowDown'
            ? 0
            : items.value.length - 1
          : (selected.value + (event.key === 'ArrowDown' ? 1 : -1) + items.value.length) %
            items.value.length;
      event.preventDefault();
      event.stopPropagation();
    }
  }
  // Only user input changes the default choice; navigation closes the list via controls.
  return {
    items,
    open,
    selected,
    focus,
    reopen,
    close,
    input,
    keydown,
    blur: () => {
      focused.value = false;
    }
  };
}
