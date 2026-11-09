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
      <div v-else-if="!store.active || store.active.url === 'about:blank'" class="nb-newtab">
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
          <button @click="store.navigate('https://developer.mozilla.org/zh-CN/')">
            <span class="nb-shortcut-icon"
              ><BrowserSiteIcon url="https://developer.mozilla.org/" /></span
            ><span>MDN</span></button
          ><button @click="store.navigate('https://github.com/')">
            <span class="nb-shortcut-icon"><BrowserSiteIcon url="https://github.com/" /></span
            ><span>GitHub</span></button
          ><button @click="store.showSection('history')">
            <BrowserIcon name="clock" /><span>{{ $t('browser.history') }}</span>
          </button>
        </div>
      </div>
      <BrowserFloatingStatus />
      <BrowserPasswordPrompt />
      <BrowserPageDialog />
    </div>
    <BrowserMenu v-if="store.menu" @action="menuAction" />
    <BrowserPopovers />
  </aside>
</template>
<script setup lang="ts">
import { provide } from 'vue';
import BrowserButton from './BrowserButton.vue';
import BrowserIcon from './BrowserIcon.vue';
import BrowserSiteIcon from './BrowserSiteIcon.vue';
import BrowserFloatingStatus from './BrowserFloatingStatus.vue';
import BrowserPasswordPrompt from './BrowserPasswordPrompt.vue';
import BrowserPageDialog from './BrowserPageDialog.vue';
import BrowserMenu from './BrowserMenu.vue';
import BrowserPages from './BrowserPages.vue';
import BrowserTabStrip from './BrowserTabStrip.vue';
import BrowserToolbar from './BrowserToolbar.vue';
import BrowserPopovers from './BrowserPopovers.vue';
import { useBrowserControls, browserControlsKey } from './useBrowserControls';
const controls = useBrowserControls();
provide(browserControlsKey, controls);
const { store, panel, surface, keydown, startResize, newtabText, menuAction } = controls;
defineExpose({ shortcut: controls.shortcut, pageContext: controls.pageContext });
</script>
<style src="./browser-panel.scss"></style>
