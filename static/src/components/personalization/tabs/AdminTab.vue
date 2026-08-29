<script setup lang="ts">
import { inject } from 'vue';

defineOptions({ name: 'AdminTab' });

/**
 * 共享上下文由 PersonalizationDrawer.vue 通过 provide 注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  openAdminPanel,
  openApiAdmin,
  openCustomTools,
  openMcpConfig,
  showMcpConfigEntry
} = ctx;
</script>

<template>
                    <section class="settings-page admin-monitor-page">
                      <div class="settings-action-row" v-if="showMcpConfigEntry">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.mcpConfigTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.mcpConfigDesc') }}</span
                          ></span
                        ><button
                          type="button"
                          class="settings-secondary-button"
                          @click="openMcpConfig"
                        >
                          {{ $t('personalization.open') }}
                        </button>
                      </div>
                      <div class="settings-action-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.adminMonitorTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.adminMonitorDesc') }}</span
                          ></span
                        ><button
                          type="button"
                          class="settings-secondary-button"
                          @click="openAdminPanel"
                        >
                          {{ $t('personalization.open') }}
                        </button>
                      </div>
                      <div class="settings-action-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.apiAdminTitle') }}</span
                          ><span class="settings-row-desc">{{ $t('personalization.apiAdminDesc') }}</span></span
                        ><button
                          type="button"
                          class="settings-secondary-button"
                          @click="openApiAdmin"
                        >
                          {{ $t('personalization.open') }}
                        </button>
                      </div>
                      <div class="settings-action-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.customToolsAdminTitle') }}</span
                          ><span class="settings-row-desc">{{ $t('personalization.customToolsAdminDesc') }}</span></span
                        ><button
                          type="button"
                          class="settings-secondary-button"
                          @click="openCustomTools"
                        >
                          {{ $t('personalization.open') }}
                        </button>
                      </div>
                    </section>
</template>

<style scoped>
.admin-monitor-page {
  display: block;
  padding: 0;
}
</style>
