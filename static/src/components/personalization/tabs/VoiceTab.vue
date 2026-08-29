<script setup lang="ts">
import { inject } from 'vue';

defineOptions({ name: 'VoiceTab' });

/**
 * 共享上下文由 PersonalizationDrawer.vue 通过 provide 注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  deleteVoiceModel,
  downloadVoiceModel,
  voiceDownloadMsg,
  voiceDownloadPercent,
  voiceDownloading,
  voiceModelPartial,
  voiceModelReady
} = ctx;
</script>

<template>
                    <section class="settings-page">
                      <div class="settings-section" style="margin-bottom: 16px">
                        <p
                          class="settings-section-desc"
                          style="
                            margin: 0;
                            color: var(--text-secondary);
                            font-size: 13px;
                            line-height: 1.6;
                          "
                        >{{ $t('personalization.voiceModelIntro') }}
                        </p>
                      </div>
                      <div class="settings-action-row">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">{{ $t('personalization.voiceModelTitle') }}</span>
                          <span class="settings-row-desc">
                            <template v-if="voiceModelReady">{{ $t('personalization.voiceModelReady') }}</template>
                            <template v-else-if="voiceDownloading"
                              >{{ $t('personalization.voiceModelDownloading', { percent: voiceDownloadPercent, msg: voiceDownloadMsg }) }}</template
                            >
                            <template v-else-if="voiceModelPartial"
                              >{{ $t('personalization.voiceModelPartial') }}</template
                            >
                            <template v-else>{{ $t('personalization.voiceModelNotDownloaded') }}</template>
                          </span>
                        </span>
                        <div style="display: flex; gap: 6px">
                          <button
                            type="button"
                            class="settings-secondary-button"
                            :disabled="voiceDownloading"
                            @click="downloadVoiceModel"
                          >
                            {{
                              voiceDownloading
                                ? $t('personalization.voiceDownloadingBtn')
                                : voiceModelReady
                                  ? $t('personalization.voiceRedownload')
                                  : $t('personalization.voiceDownloadModel')
                            }}
                          </button>
                          <button
                            v-if="voiceModelReady || voiceModelPartial"
                            type="button"
                            class="settings-secondary-button"
                            style="color: var(--state-error)"
                            :disabled="voiceDownloading"
                            @click="deleteVoiceModel"
                          >
                            {{ $t('common.delete') }}
                          </button>
                        </div>
                      </div>
                      <div
                        v-if="voiceDownloading"
                        class="voice-download-bar"
                        style="margin: 8px 0 0"
                      >
                        <div class="voice-download-track">
                          <div
                            class="voice-download-fill"
                            :style="{ width: voiceDownloadPercent + '%' }"
                          ></div>
                        </div>
                        <span style="font-size: 11px; color: var(--text-secondary); margin-top: 4px"
                          >{{ voiceDownloadPercent }}%</span
                        >
                      </div>
                    </section>
</template>

<style scoped>
.voice-download-bar {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.voice-download-track {
  width: 100%;
  height: 6px;
  background: var(--surface-muted);
  border-radius: 3px;
  overflow: hidden;
}
.voice-download-fill {
  height: 100%;
  background: var(--accent-primary);
  border-radius: 3px;
  transition: width 0.3s ease;
}
</style>
