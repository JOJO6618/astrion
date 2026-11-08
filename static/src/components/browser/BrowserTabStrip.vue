<template>
  <header class="nb-tabbar">
    <div class="nb-tabs" role="tablist" :aria-label="$t('browser.title')">
      <div
        v-for="tab in store.tabs"
        :key="tab.id"
        class="nb-tab"
        :class="{ active: tab.id === store.active?.id, dragging: dragged === tab.id }"
        role="tab"
        :aria-selected="tab.id === store.active?.id"
        :tabindex="tab.id === store.active?.id ? 0 : -1"
        draggable="true"
        @click="selectTab(tab.id)"
        @auxclick.middle.prevent="store.command('close', undefined, tab.id)"
        @contextmenu.prevent.stop="tabContext($event, tab.id)"
        @dragstart="dragStart($event, tab.id)"
        @dragover.prevent
        @drop.prevent="drop(tab.id)"
        @dragend="dragged = ''"
        @keydown="tabKeys($event, tab.id)"
      >
        <BrowserIcon v-if="tab.section" :name="internalPageIcons[tab.section]" />
        <BrowserIcon v-else-if="tab.loading && !tab.favicon" name="refreshCw" class="nb-spinning" />
        <BrowserSiteIcon v-else :src="tab.favicon" :url="tab.url" />
        <span class="nb-tab-title">{{ browserTabLabel(tab) }}</span
        ><button
          class="nb-tab-close"
          tabindex="-1"
          :aria-label="$t('browser.closeTab')"
          @click.stop="store.command('close', undefined, tab.id)"
        >
          <BrowserIcon name="x" />
        </button>
      </div>
    </div>
    <BrowserButton
      name="plus"
      :label="$t('browser.newTab')"
      :disabled="!store.snapshot"
      @click="newTab"
    />
    <div class="nb-tabbar-space"></div>
    <BrowserButton
      name="chevronDown"
      :label="$t('browser.tabSearch')"
      @click="togglePicker"
    /><BrowserButton name="x" :label="$t('browser.close')" @click="store.hide()" />
  </header>
</template>
<script setup lang="ts">
import { inject } from 'vue';
import { browserControlsKey } from './useBrowserControls';
import BrowserIcon from './BrowserIcon.vue';
import BrowserButton from './BrowserButton.vue';
import BrowserSiteIcon from './BrowserSiteIcon.vue';
import { browserTabLabel, internalPageIcons } from './internalPages';
const { store, dragged, selectTab, tabContext, dragStart, drop, tabKeys, newTab, togglePicker } =
  inject(browserControlsKey)!;
</script>
