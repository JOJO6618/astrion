<template>
  <BrowserDownloadsPopover v-if="downloadsOpen" />
  <div v-if="picker" class="nb-popover nb-tab-picker">
    <label class="nb-search"
      ><BrowserIcon name="search" /><input
        v-model="tabQuery"
        :placeholder="$t('browser.tabSearch')"
        :aria-label="$t('browser.tabSearch')" /></label
    ><button
      v-for="tab in filteredTabs"
      :key="tab.id"
      class="nb-menu-row"
      @click="selectTab(tab.id)"
    >
      <BrowserIcon v-if="tab.section" :name="internalPageIcons[tab.section]" />
      <BrowserSiteIcon v-else :src="tab.favicon" :url="tab.url" /><span>{{
        browserTabLabel(tab)
      }}</span
      ><BrowserIcon v-if="tab.id === store.active?.id" name="check" />
    </button>
  </div>
  <div v-if="siteInfo" class="nb-popover nb-site-info">
    <strong>{{ $t('browser.connection') }}</strong>
    <p>{{ store.active?.url }}</p>
    <small v-if="!store.active?.url.startsWith('https:')">{{ $t('browser.unverified') }}</small>
  </div>
  <div
    v-if="context"
    class="nb-popover nb-context"
    role="menu"
    :style="{ left: `${context.x}px`, top: `${context.y}px` }"
  >
    <button
      v-for="item in contextActions"
      :key="item.action"
      role="menuitem"
      class="nb-menu-row"
      @click="contextAction(item.action)"
    >
      {{ $t(`browser.${item.label}`) }}
    </button>
  </div>
  <div v-if="permission" class="nb-permission-scrim">
    <section
      class="nb-permission"
      role="dialog"
      aria-modal="true"
      :aria-label="$t('browser.permissionTitle')"
    >
      <BrowserIcon name="info" />
      <h2>{{ $t('browser.permissionTitle') }}</h2>
      <p>{{ permission.origin }}</p>
      <strong>{{ permissionName }}</strong
      ><small>{{ $t('browser.permissionHint') }}</small>
      <footer>
        <button class="nb-text-button" @click="store.command('permission', false, permission.id)">
          {{ $t('browser.deny') }}</button
        ><button
          class="nb-text-button primary"
          @click="store.command('permission', true, permission.id)"
        >
          {{ $t('browser.allow') }}
        </button>
      </footer>
    </section>
  </div>
</template>
<script setup lang="ts">
import { inject } from 'vue';
import { browserControlsKey } from './useBrowserControls';
import BrowserIcon from './BrowserIcon.vue';
import BrowserSiteIcon from './BrowserSiteIcon.vue';
import BrowserDownloadsPopover from './BrowserDownloadsPopover.vue';
import { browserTabLabel, internalPageIcons } from './internalPages';
const {
  store,
  picker,
  tabQuery,
  siteInfo,
  downloadsOpen,
  context,
  permission,
  permissionName,
  filteredTabs,
  contextActions,
  selectTab,
  contextAction
} = inject(browserControlsKey)!;
</script>
