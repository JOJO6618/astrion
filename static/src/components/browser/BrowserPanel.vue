<template>
  <aside
    ref="panel"
    class="native-browser-panel"
    :style="{ width: `${store.width}px` }"
    :aria-label="$t('browser.title')"
    @keydown="keydown"
    @contextmenu.prevent
  >
    <div
      class="nb-resizer"
      role="separator"
      aria-orientation="vertical"
      tabindex="0"
      :aria-label="$t('browser.resize')"
      @pointerdown="startResize"
      @keydown.left.prevent="store.setWidth(store.width + 30)"
      @keydown.right.prevent="store.setWidth(store.width - 30)"
    ></div>
    <BrowserTabStrip />

    <BrowserToolbar />
    <div ref="surface" class="nb-surface">
      <BrowserPages v-if="store.section" :key="store.active?.id" />
      <div v-else-if="store.connecting || !store.snapshot" class="nb-empty">
        {{
          store.error
            ? $t('browser.cannotConnect')
            : store.connecting
              ? $t('browser.loading')
              : $t('browser.waitingForTab')
        }}
      </div>
      <div
        v-else-if="store.navigationPending || store.active?.pending"
        class="nb-navigation-blank"
      ></div>
      <div v-else-if="store.active?.error" class="nb-load-error">
        <BrowserIcon name="globe" />
        <h2>{{ $t('browser.loadFailed') }}</h2>
        <p>{{ store.active.error }}</p>
        <button class="nb-text-button" @click="store.command('reload')">
          {{ $t('browser.retry') }}
        </button>
      </div>
      <BrowserNewTab v-else-if="!store.active || store.active.url === 'about:blank'" />
      <BrowserFloatingStatus />
      <BrowserPasswordPrompt />
    </div>
    <BrowserMenu v-if="store.menu" @action="menuAction" />
    <BrowserPopovers />
  </aside>
</template>
<script setup lang="ts">
import { provide } from 'vue';
import BrowserIcon from './BrowserIcon.vue';
import BrowserFloatingStatus from './BrowserFloatingStatus.vue';
import BrowserPasswordPrompt from './BrowserPasswordPrompt.vue';
import BrowserMenu from './BrowserMenu.vue';
import BrowserNewTab from './BrowserNewTab.vue';
import BrowserPages from './BrowserPages.vue';
import BrowserTabStrip from './BrowserTabStrip.vue';
import BrowserToolbar from './BrowserToolbar.vue';
import BrowserPopovers from './BrowserPopovers.vue';
import { useBrowserControls, browserControlsKey } from './useBrowserControls';
const controls = useBrowserControls();
provide(browserControlsKey, controls);
const { store, panel, surface, keydown, startResize, menuAction } = controls;
defineExpose({ shortcut: controls.shortcut, pageContext: controls.pageContext });
</script>
<style src="./browser-panel.scss"></style>
