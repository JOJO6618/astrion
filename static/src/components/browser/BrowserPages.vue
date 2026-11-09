<template>
  <section class="nb-internal">
    <header class="nb-internal-header">
      <h2>{{ heading }}</h2>
      <BrowserButton name="x" :label="$t('browser.closeSection')" @click="store.showSection('')" />
    </header>
    <template v-if="store.section === 'history'">
      <label class="nb-search"
        ><BrowserIcon name="search" /><input
          v-model="query"
          :placeholder="$t('browser.searchHistory')"
          :aria-label="$t('browser.searchHistory')"
      /></label>
      <div v-if="!history.length" class="nb-empty">
        {{ query ? $t('browser.noMatches') : $t('browser.emptyHistory') }}
      </div>
      <div v-for="row in history" :key="row.id" class="nb-data-row">
        <BrowserSiteIcon :url="row.url" /><button
          class="nb-row-main"
          @click="store.navigate(row.url)"
        >
          <span>{{ row.title }}</span
          ><small>{{ row.url }}</small>
        </button>
        <time>{{ date(row.at) }}</time
        ><BrowserButton
          name="x"
          :label="$t('browser.remove')"
          @click="store.command('history-remove', undefined, row.id)"
        />
      </div>
    </template>
    <BrowserDownloadList v-else-if="store.section === 'downloads'" />
    <template v-else-if="store.section === 'settings'">
      <div class="nb-setting">
        <span>{{ $t('browser.theme') }}</span>
        <div class="nb-theme-options">
          <button
            v-for="theme in themes"
            :key="theme"
            :aria-pressed="currentTheme === theme"
            @click="chooseTheme(theme)"
          >
            {{ $t(`browser.${theme}`) }}
          </button>
        </div>
      </div>
      <div class="nb-setting">
        <span>{{ $t('browser.profileScope') }}</span
        ><small>{{ $t('browser.shared') }}</small>
      </div>
      <div class="nb-setting">
        <span>{{ $t('browser.browsingEngine') }}</span
        ><small>{{
          $t(isWindowsDesktopShell() ? 'browser.engineWindows' : 'browser.engineValue')
        }}</small>
      </div>
      <button class="nb-setting nb-setting-link" @click="store.showSection('import')">
        <span>{{ $t('browser.importTitle') }}</span
        ><BrowserIcon name="chevronDown" />
      </button>
      <button class="nb-setting nb-setting-link" @click="store.showSection('passwords')">
        <span>{{ $t('browser.passwords') }}</span
        ><BrowserIcon name="chevronDown" />
      </button>
      <button class="nb-setting nb-setting-link" @click="store.showSection('clear')">
        <span>{{ $t('browser.clear') }}</span
        ><BrowserIcon name="chevronDown" />
      </button>
    </template>
    <template v-else-if="store.section === 'clear'">
      <p class="nb-description">{{ $t('browser.clearDescription') }}</p>
      <button
        v-for="option in clearOptions"
        :key="option.key"
        role="checkbox"
        class="nb-setting nb-check"
        :aria-checked="clearSelection[option.key]"
        @click="clearSelection[option.key] = !clearSelection[option.key]"
      >
        <span>{{ $t(`browser.${option.label}`) }}</span
        ><FancyCheck :checked="clearSelection[option.key]" :size="18" />
      </button>
      <div class="nb-page-footer">
        <button class="nb-text-button" @click="store.showSection('')">
          {{ $t('browser.cancel') }}</button
        ><button
          class="nb-text-button primary"
          :disabled="clearing || !Object.values(clearSelection).some(Boolean)"
          @click="clear"
        >
          {{ $t('browser.clearConfirm') }}
        </button>
      </div>
    </template>
    <BrowserImport v-else-if="store.section === 'import'" :key="store.snapshot?.lease" />
    <BrowserPasswords v-else-if="store.section === 'passwords'" :key="store.snapshot?.lease" />
  </section>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue';
import { t } from '@/locales';
import { isWindowsDesktopShell } from '@/utils/desktopPlatform';
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
import { useTheme, type ThemeKey } from '@/utils/theme';
import FancyCheck from '@/components/common/FancyCheck.vue';
import BrowserIcon from './BrowserIcon.vue';
import BrowserButton from './BrowserButton.vue';
import BrowserSiteIcon from './BrowserSiteIcon.vue';
import BrowserDownloadList from './BrowserDownloadList.vue';
import BrowserImport from './BrowserImport.vue';
import BrowserPasswords from './BrowserPasswords.vue';
const store = useNativeBrowserStore();
const query = ref('');
const heading = computed(() =>
  t(`browser.${store.section === 'import' ? 'importTitle' : store.section}`)
);
const history = computed(() =>
  (store.snapshot?.history || []).filter((row) =>
    `${row.title} ${row.url}`.toLowerCase().includes(query.value.toLowerCase())
  )
);
const themes: ThemeKey[] = ['classic', 'light', 'dark'];
const { setTheme, loadTheme } = useTheme();
const currentTheme = ref(loadTheme());
function chooseTheme(theme: ThemeKey) {
  setTheme(theme);
  currentTheme.value = theme;
}
const date = (at: number) =>
  new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
const clearSelection = ref({ history: true, cookies: false, cache: true });
const clearOptions = [
  { key: 'history', label: 'clearHistory' },
  { key: 'cookies', label: 'cookies' },
  { key: 'cache', label: 'cache' }
] as const;
const clearing = ref(false);
async function clear() {
  clearing.value = true;
  await store.command('clear', { ...clearSelection.value });
  clearing.value = false;
  if (!store.error) store.showSection('');
}
</script>
<style scoped src="./browser-pages.scss"></style>
