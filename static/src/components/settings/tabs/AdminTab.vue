<script setup lang="ts">
import { inject } from 'vue';

defineOptions({ name: 'AdminTab' });

/**
 * 共享上下文由 usePersonalizationContext（PersonalizationDrawer / SettingsShell 各自 provide 同一份）注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const { openAdminPanel, openApiAdmin, openCustomTools, openMcpConfig, showMcpConfigEntry } = ctx;
</script>

<template>
  <section class="stg-page admin-monitor-page">
    <div class="stg-card">
      <div class="stg-action-row" v-if="showMcpConfigEntry">
        <span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.mcpConfigTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.mcpConfigDesc') }}</span></span
        ><button type="button" class="stg-secondary-button" @click="openMcpConfig">
          {{ $t('personalization.open') }}
        </button>
      </div>
      <div class="stg-action-row">
        <span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.adminMonitorTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.adminMonitorDesc') }}</span></span
        ><button type="button" class="stg-secondary-button" @click="openAdminPanel">
          {{ $t('personalization.open') }}
        </button>
      </div>
      <div class="stg-action-row">
        <span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.apiAdminTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.apiAdminDesc') }}</span></span
        ><button type="button" class="stg-secondary-button" @click="openApiAdmin">
          {{ $t('personalization.open') }}
        </button>
      </div>
      <div class="stg-action-row">
        <span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.customToolsAdminTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.customToolsAdminDesc') }}</span></span
        ><button type="button" class="stg-secondary-button" @click="openCustomTools">
          {{ $t('personalization.open') }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.admin-monitor-page {
  display: block;
  padding: 0;
}
</style>
