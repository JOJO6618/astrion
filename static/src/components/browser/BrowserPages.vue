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
      <div class="nb-setting nb-setting-wrap">
        <span>{{ $t('browser.theme') }}</span>
        <UiSelect
          :model-value="currentTheme"
          :options="themeOptions"
          :label="$t('browser.theme')"
          align-end
          @update:model-value="chooseTheme"
        />
      </div>
      <div class="nb-setting nb-setting-wrap">
        <span>{{ $t('browser.searchEngine') }}</span>
        <UiSelect
          :model-value="currentEngine"
          :options="searchEngineOptions"
          :label="$t('browser.searchEngine')"
          align-end
          @update:model-value="(value) => store.command('search-engine', value)"
        />
      </div>
      <button
        v-if="importSupported"
        class="nb-setting nb-setting-link"
        @click="store.showSection('import')"
      >
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
        role="switch"
        class="nb-setting"
        :aria-checked="clearSelection[option.key]"
        @click="clearSelection[option.key] = !clearSelection[option.key]"
      >
        <span>{{ $t(`browser.${option.label}`) }}</span
        ><UiSwitch decorative :checked="clearSelection[option.key]" />
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
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
import { useTheme, type ThemeKey } from '@/utils/theme';
import UiSelect from '@/components/ui/UiSelect.vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';
import BrowserIcon from './BrowserIcon.vue';
import BrowserButton from './BrowserButton.vue';
import BrowserSiteIcon from './BrowserSiteIcon.vue';
import BrowserDownloadList from './BrowserDownloadList.vue';
import BrowserImport from './BrowserImport.vue';
import BrowserPasswords from './BrowserPasswords.vue';
import { browserBridge, type BrowserSearchEngine } from './types';
const store = useNativeBrowserStore();
const importSupported = browserBridge()?.credentialImport !== false;
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
const searchEngines: { key: BrowserSearchEngine; label: string }[] = [
  { key: 'google', label: 'searchEngineGoogle' },
  { key: 'bing_cn', label: 'searchEngineBingCn' },
  { key: 'bing', label: 'searchEngineBing' },
  { key: 'baidu', label: 'searchEngineBaidu' }
];
const themeOptions = computed(() =>
  themes.map((theme) => ({ value: theme, label: t(`browser.${theme}`) }))
);
const searchEngineOptions = computed(() =>
  searchEngines.map((engine) => ({ value: engine.key, label: t(`browser.${engine.label}`) }))
);
const currentEngine = computed(() => store.snapshot?.settings?.searchEngine || 'google');
const { setTheme, loadTheme } = useTheme();
const currentTheme = ref(loadTheme());
function chooseTheme(value: string) {
  const theme = value as ThemeKey;
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
