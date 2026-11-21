<script setup lang="ts">
import { inject } from 'vue';

defineOptions({ name: 'AdminTab' });

/**
 * 共享上下文由 usePersonalizationContext（PersonalizationDrawer / SettingsShell 各自 provide 同一份）注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const { openCustomTools } = ctx;
</script>

<template>
  <section class="stg-page admin-monitor-page">
    <div class="stg-card">
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
