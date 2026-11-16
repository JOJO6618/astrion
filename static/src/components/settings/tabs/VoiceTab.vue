<script setup lang="ts">
import { inject } from 'vue';

defineOptions({ name: 'VoiceTab' });

/**
 * 共享上下文由 usePersonalizationContext（PersonalizationDrawer / SettingsShell 各自 provide 同一份）注入。
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
  <section class="stg-page">
    <div class="stg-card">
      <div class="stg-action-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.voiceModelTitle') }}</span>
          <span class="stg-row-desc">
            <template v-if="voiceModelReady">{{ $t('personalization.voiceModelReady') }}</template>
            <template v-else-if="voiceDownloading">{{
              $t('personalization.voiceModelDownloading', {
                percent: voiceDownloadPercent,
                msg: voiceDownloadMsg
              })
            }}</template>
            <template v-else-if="voiceModelPartial">{{
              $t('personalization.voiceModelPartial')
            }}</template>
            <template v-else>{{ $t('personalization.voiceModelNotDownloaded') }}</template>
          </span>
        </span>
        <button
          type="button"
          class="stg-secondary-button"
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
      </div>
      <div v-if="voiceDownloading" class="voice-download-bar">
        <div class="voice-download-track">
          <div class="voice-download-fill" :style="{ width: voiceDownloadPercent + '%' }"></div>
        </div>
      </div>
      <div
        v-if="(voiceModelReady || voiceModelPartial) && !voiceDownloading"
        class="stg-action-row"
      >
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.voiceDeleteTitle') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.voiceDeleteDesc') }}</span>
        </span>
        <button type="button" class="stg-secondary-button danger" @click="deleteVoiceModel">
          {{ $t('common.delete') }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.voice-download-bar {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-bottom: 12px;
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
