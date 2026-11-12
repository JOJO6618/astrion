<template>
  <aside
    ref="panel"
    class="native-browser-panel"
    :class="{ 'native-browser-panel--window': popoutWindow }"
    :style="popoutWindow ? undefined : { width: `${store.width}px` }"
    :aria-label="$t('browser.title')"
    @keydown="keydown"
    @contextmenu.prevent
  >
    <div
      v-if="!popoutWindow"
      class="nb-resizer"
      role="separator"
      aria-orientation="vertical"
      tabindex="0"
      :aria-label="$t('browser.resize')"
      @pointerdown="startResize"
      @keydown.left.prevent="store.setWidth(store.width + 30)"
      @keydown.right.prevent="store.setWidth(store.width - 30)"
    ></div>
    <template v-if="store.snapshot?.detached">
      <header class="nb-tabbar">
        <span class="nb-detached-title">{{ $t('browser.title') }}</span>
        <div class="nb-tabbar-space"></div>
        <BrowserButton name="x" :label="$t('browser.close')" @click="store.hide()" />
      </header>
      <div class="nb-detached">
        <BrowserIcon name="popOut" />
        <p>{{ $t('browser.detachedHint') }}</p>
        <div class="nb-detached-actions">
          <button class="nb-text-button" @click="store.command('show-popout')">
            {{ $t('browser.showWindow') }}
          </button>
          <button class="nb-text-button" @click="store.command('dock')">
            {{ $t('browser.dock') }}
          </button>
        </div>
      </div>
    </template>
    <template v-else>
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
    </template>
  </aside>
</template>
<script setup lang="ts">
import { provide } from 'vue';
import BrowserIcon from './BrowserIcon.vue';
import BrowserButton from './BrowserButton.vue';
import BrowserFloatingStatus from './BrowserFloatingStatus.vue';
import BrowserPasswordPrompt from './BrowserPasswordPrompt.vue';
import BrowserMenu from './BrowserMenu.vue';
import BrowserNewTab from './BrowserNewTab.vue';
import BrowserPages from './BrowserPages.vue';
import BrowserTabStrip from './BrowserTabStrip.vue';
import BrowserToolbar from './BrowserToolbar.vue';
import BrowserPopovers from './BrowserPopovers.vue';
import { useBrowserControls, browserControlsKey } from './useBrowserControls';
import { browserPopoutWindow } from './types';
const controls = useBrowserControls();
provide(browserControlsKey, controls);
const { store, panel, surface, keydown, startResize, menuAction } = controls;
const popoutWindow = browserPopoutWindow();
defineExpose({ shortcut: controls.shortcut, pageContext: controls.pageContext });
</script>
<style src="./browser-panel.scss"></style>
