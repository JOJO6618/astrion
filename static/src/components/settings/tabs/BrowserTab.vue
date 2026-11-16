<script setup lang="ts">
import { inject, onMounted, ref } from 'vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';
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
  <section class="stg-page">
    <div class="stg-card">
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('browser.aiDefaultOn') }}</span
          ><span class="stg-row-desc">{{ $t('browser.aiDefaultOnDesc') }}</span></span
        ><input
          type="checkbox"
          :checked="form.browser_tools_default"
          @change="
            personalization.updateField({
              key: 'browser_tools_default',
              value: ($event.target as HTMLInputElement).checked
            })
          " /><UiSwitch decorative :checked="form.browser_tools_default"
      /></label>
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('browser.aiAutoOpen') }}</span
          ><span class="stg-row-desc">{{ $t('browser.aiAutoOpenDesc') }}</span></span
        ><input
          type="checkbox"
          :checked="form.browser_auto_open"
          @change="
            personalization.updateField({
              key: 'browser_auto_open',
              value: ($event.target as HTMLInputElement).checked
            })
          " /><UiSwitch decorative :checked="form.browser_auto_open"
      /></label>
      <label class="stg-textarea-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('browser.aiDownloadDir') }}</span>
          <span class="stg-row-desc">{{ $t('browser.aiDownloadDirDesc') }}</span>
        </span>
        <span class="stg-field">
          <input
            type="text"
            :value="form.browser_download_dir"
            :placeholder="$t('browser.aiDownloadDirPlaceholder')"
            :aria-label="$t('browser.aiDownloadDir')"
            spellcheck="false"
            @input="
              personalization.updateField({
                key: 'browser_download_dir',
                value: ($event.target as HTMLInputElement).value
              })
            "
          />
        </span>
      </label>
    </div>
    <div class="stg-card">
      <div class="stg-textarea-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('browser.aiSites') }}</span>
          <span class="stg-row-desc">{{
            sites.length ? $t('browser.aiSitesDesc') : $t('browser.aiSitesEmpty')
          }}</span>
        </span>
        <div v-if="sites.length" class="stg-domain-list">
          <div v-for="item in sites" :key="item.site" class="stg-domain-row">
            <span class="stg-row-title">{{ item.site }}</span>
            <button
              type="button"
              class="stg-domain-remove"
              :disabled="busy"
              @click="revoke(item.site)"
            >
              {{ $t('browser.aiSiteRevoke') }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
