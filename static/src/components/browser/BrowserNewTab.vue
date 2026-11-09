<template>
  <div class="nb-newtab" @keydown.esc="closeAll">
    <div class="nb-wordmark">Astrion</div>
    <form class="nb-newtab-search" @submit.prevent="store.navigate(newtabText)">
      <BrowserIcon name="search" /><input
        v-model="newtabText"
        :placeholder="$t('browser.address')"
        :aria-label="$t('browser.address')"
        autocomplete="off"
      /><BrowserButton
        class="nb-forward"
        name="arrowLeft"
        :label="$t('browser.go')"
        @click="store.navigate(newtabText)"
      />
    </form>
    <div class="nb-quicklinks">
      <div
        v-for="tile in tiles"
        :key="`${tile.pinned ? 'p' : 'v'}:${tile.url}`"
        class="nb-shortcut"
        :class="{ 'menu-open': menuFor === tile.url }"
      >
        <button class="nb-shortcut-open" :title="tile.url" @click="store.navigate(tile.url)">
          <span class="nb-shortcut-icon"
            ><BrowserSiteIcon :src="tile.favicon" :url="tile.url" initial /></span
          ><span class="nb-shortcut-title">{{ tile.title }}</span>
        </button>
        <button
          class="nb-shortcut-more"
          type="button"
          :aria-label="$t('browser.shortcutMore')"
          :aria-expanded="menuFor === tile.url"
          @click.stop="menuFor = menuFor === tile.url ? '' : tile.url"
        >
          <BrowserIcon name="ellipsis" />
        </button>
        <div v-if="menuFor === tile.url" class="nb-popover nb-shortcut-menu" role="menu">
          <template v-if="tile.pinned">
            <button class="nb-menu-row" role="menuitem" @click="startEdit(tile)">
              <BrowserIcon name="pencil" /><span>{{ $t('browser.shortcutEdit') }}</span>
            </button>
            <button class="nb-menu-row" role="menuitem" @click="unpin(tile)">
              <BrowserIcon name="x" /><span>{{ $t('browser.shortcutUnpin') }}</span>
            </button>
          </template>
          <button
            v-else
            class="nb-menu-row"
            role="menuitem"
            :disabled="pinnedFull"
            @click="pin(tile)"
          >
            <BrowserIcon name="plus" /><span>{{ $t('browser.shortcutPin') }}</span>
          </button>
          <button class="nb-menu-row" role="menuitem" @click="remove(tile)">
            <BrowserIcon name="trash" /><span>{{ $t('browser.shortcutRemove') }}</span>
          </button>
        </div>
      </div>
      <div v-if="!pinnedFull" class="nb-shortcut">
        <button class="nb-shortcut-open" @click="startAdd">
          <span class="nb-shortcut-icon nb-shortcut-add"><BrowserIcon name="plus" /></span
          ><span class="nb-shortcut-title">{{ $t('browser.addShortcut') }}</span>
        </button>
      </div>
    </div>
    <div v-if="editing" class="nb-shortcut-editor-scrim" @pointerdown.self="editing = null">
      <form
        class="nb-shortcut-editor"
        role="dialog"
        aria-modal="true"
        :aria-label="$t(editing.original ? 'browser.editShortcut' : 'browser.addShortcut')"
        @submit.prevent="saveEdit"
      >
        <strong>{{ $t(editing.original ? 'browser.editShortcut' : 'browser.addShortcut') }}</strong>
        <label>
          <span>{{ $t('browser.shortcutName') }}</span>
          <input v-model="editing.title" maxlength="60" autocomplete="off" />
        </label>
        <label>
          <span>{{ $t('browser.shortcutUrl') }}</span>
          <input
            ref="urlInput"
            v-model="editing.url"
            autocomplete="off"
            spellcheck="false"
            :aria-invalid="editing.invalid"
            @input="editing.invalid = false"
          />
        </label>
        <small v-if="editing.invalid" class="nb-shortcut-error">{{
          $t('browser.shortcutInvalidUrl')
        }}</small>
        <footer>
          <button type="button" class="nb-text-button" @click="editing = null">
            {{ $t('browser.cancel') }}</button
          ><button type="submit" class="nb-text-button primary" :disabled="!editing.url.trim()">
            {{ $t('browser.shortcutSave') }}
          </button>
        </footer>
      </form>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
import BrowserButton from './BrowserButton.vue';
import BrowserIcon from './BrowserIcon.vue';
import BrowserSiteIcon from './BrowserSiteIcon.vue';
import { browserControlsKey } from './useBrowserControls';
import {
  MAX_SHORTCUTS,
  shortcutKey,
  shortcutTiles,
  shortcutURL,
  type ShortcutTile
} from './newTabShortcuts';
import type { BrowserShortcuts } from './types';

const store = useNativeBrowserStore();
const { newtabText } = inject(browserControlsKey)!;
const shortcuts = computed<BrowserShortcuts>(
  () => store.snapshot?.shortcuts || { pinned: [], hidden: [] }
);
const tiles = computed(() => shortcutTiles(store.snapshot?.history || [], shortcuts.value));
const pinnedFull = computed(() => shortcuts.value.pinned.length >= MAX_SHORTCUTS);
const menuFor = ref('');
const editing = ref<{ original: string; title: string; url: string; invalid: boolean } | null>(
  null
);
const urlInput = ref<HTMLInputElement | null>(null);

function save(next: BrowserShortcuts) {
  menuFor.value = '';
  void store.command('shortcuts', next);
}
function pin(tile: ShortcutTile) {
  if (pinnedFull.value) return;
  const { pinned, hidden } = shortcuts.value;
  save({ pinned: [...pinned, { url: tile.url, title: tile.title }], hidden });
}
function unpin(tile: ShortcutTile) {
  const { pinned, hidden } = shortcuts.value;
  save({ pinned: pinned.filter((row) => row.url !== tile.url), hidden });
}
/** Removing hides the site from "most visited" too, so it does not come straight back. */
function remove(tile: ShortcutTile) {
  const { pinned, hidden } = shortcuts.value;
  save({
    pinned: pinned.filter((row) => row.url !== tile.url),
    hidden: tile.key && !hidden.includes(tile.key) ? [...hidden, tile.key] : hidden
  });
}
async function openEditor(value: { original: string; title: string; url: string }) {
  menuFor.value = '';
  editing.value = { ...value, invalid: false };
  await nextTick();
  urlInput.value?.focus();
}
function startAdd() {
  void openEditor({ original: '', title: '', url: '' });
}
function startEdit(tile: ShortcutTile) {
  void openEditor({ original: tile.url, title: tile.title, url: tile.url });
}
function saveEdit() {
  const draft = editing.value;
  if (!draft) return;
  const url = shortcutURL(draft.url);
  if (!url) {
    draft.invalid = true;
    return;
  }
  const { pinned, hidden } = shortcuts.value;
  const row = { url, title: draft.title.trim() };
  const others = pinned.filter((item) => item.url !== draft.original && item.url !== url);
  const index = pinned.findIndex((item) => item.url === draft.original);
  const next = [...others];
  next.splice(index >= 0 ? Math.min(index, next.length) : next.length, 0, row);
  // Explicitly adding a site overrides an earlier "remove" of the same site.
  const key = shortcutKey(url);
  editing.value = null;
  save({ pinned: next.slice(0, MAX_SHORTCUTS), hidden: hidden.filter((item) => item !== key) });
}
function closeAll() {
  menuFor.value = '';
  editing.value = null;
}
function outside(event: PointerEvent) {
  if (!(event.target instanceof Element) || !event.target.closest('.nb-shortcut.menu-open'))
    menuFor.value = '';
}
onMounted(() => document.addEventListener('pointerdown', outside));
onBeforeUnmount(() => document.removeEventListener('pointerdown', outside));
</script>
