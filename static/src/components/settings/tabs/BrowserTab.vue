<script setup lang="ts">
import { inject, onMounted, ref } from 'vue';
import FancyCheck from '@/components/common/FancyCheck.vue';
import { workspaceScopedFetch as fetch } from '@/app/methods/workspaceRequestScope';

defineOptions({ name: 'BrowserTab' });

const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const { form, personalization } = ctx;

interface AllowedSite {
  site: string;
  granted_at?: number;
}
const sites = ref<AllowedSite[]>([]);
const supported = ref(true);
const busy = ref(false);

const load = async () => {
  try {
    const response = await fetch('/api/browser-site-permissions');
    const data = await response.json();
    if (data?.success) {
      sites.value = data.sites || [];
      supported.value = data.supported !== false;
    }
  } catch {
    /* keep the empty list */
  }
};
const revoke = async (site: string) => {
  if (busy.value) return;
  busy.value = true;
  try {
    const response = await fetch('/api/browser-site-permissions', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ site })
    });
    const data = await response.json();
    if (data?.success) sites.value = data.sites || [];
  } finally {
    busy.value = false;
  }
};
onMounted(load);
</script>

<template>
  <section class="settings-page">
    <label class="settings-toggle-row"
      ><span class="settings-row-copy"
        ><span class="settings-row-title">{{ $t('browser.aiDefaultOn') }}</span
        ><span class="settings-row-desc">{{ $t('browser.aiDefaultOnDesc') }}</span></span
      ><input
        type="checkbox"
        :checked="form.browser_tools_default"
        @change="
          personalization.updateField({
            key: 'browser_tools_default',
            value: ($event.target as HTMLInputElement).checked
          })
        " /><FancyCheck :checked="form.browser_tools_default"
    /></label>
    <label class="settings-toggle-row"
      ><span class="settings-row-copy"
        ><span class="settings-row-title">{{ $t('browser.aiAutoOpen') }}</span
        ><span class="settings-row-desc">{{ $t('browser.aiAutoOpenDesc') }}</span></span
      ><input
        type="checkbox"
        :checked="form.browser_auto_open"
        @change="
          personalization.updateField({
            key: 'browser_auto_open',
            value: ($event.target as HTMLInputElement).checked
          })
        " /><FancyCheck :checked="form.browser_auto_open"
    /></label>
    <label class="settings-input-row">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ $t('browser.aiDownloadDir') }}</span>
        <span class="settings-row-desc">{{ $t('browser.aiDownloadDirDesc') }}</span>
      </span>
      <input
        type="text"
        :value="form.browser_download_dir"
        placeholder=".astrion/browser_downloads"
        :aria-label="$t('browser.aiDownloadDir')"
        @input="
          personalization.updateField({
            key: 'browser_download_dir',
            value: ($event.target as HTMLInputElement).value
          })
        "
      />
    </label>
    <div class="settings-row-copy browser-sites-head">
      <span class="settings-row-title">{{ $t('browser.aiSites') }}</span>
      <span class="settings-row-desc">{{ $t('browser.aiSitesDesc') }}</span>
    </div>
    <div v-if="!sites.length" class="settings-row-desc browser-sites-empty">
      {{ $t('browser.aiSitesEmpty') }}
    </div>
    <div v-for="item in sites" :key="item.site" class="settings-action-row">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ item.site }}</span>
      </span>
      <button
        type="button"
        class="settings-secondary-button"
        :disabled="busy"
        @click="revoke(item.site)"
      >
        {{ $t('browser.aiSiteRevoke') }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.browser-sites-head {
  margin-top: 16px;
}
.browser-sites-empty {
  padding: 8px 0;
}
</style>
