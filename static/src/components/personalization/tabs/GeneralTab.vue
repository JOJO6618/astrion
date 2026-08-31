<script setup lang="ts">
import { computed, inject, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import FancyCheck from '@/components/common/FancyCheck.vue';
import { useSandboxSetupStore } from '@/stores/sandboxSetup';

defineOptions({ name: 'GeneralTab' });

const { t } = useI18n();
const sandboxSetup = useSandboxSetupStore();

// 沙箱环境区块：进入通用页时补一次检测（复用 store 内部防抖）
onMounted(() => {
  if (!sandboxSetup.status) void sandboxSetup.fetchStatus();
});

const sandboxStatusText = computed(() => {
  const s = sandboxSetup.status;
  if (!s || sandboxSetup.checking) return t('sandbox.sectionChecking');
  if (!s.applicable) return t('sandbox.sectionUnavailable');
  return s.state === 'ready' ? t('sandbox.sectionReady') : t('sandbox.sectionMissing');
});

const sandboxShowWizard = computed(() => {
  const s = sandboxSetup.status;
  return !!s && s.applicable && s.state !== 'ready';
});

/**
 * 共享上下文由 PersonalizationDrawer.vue 通过 provide 注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  personalization,
  form,
  startTutorial,
  isAppShell,
  appCurrentVersionText,
  appUpdateCheckedText,
  appHasUpdate,
  appUpdateStateText,
  appUpdateChecking,
  checkAppUpdate,
  downloadLatestApp
} = ctx;
</script>

<template>
  <section class="settings-page">
    <label class="settings-toggle-row">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ $t('personalization.autoTitleTitle') }}</span>
        <span class="settings-row-desc">{{ $t('personalization.autoTitleDesc') }}</span>
      </span>
      <input
        type="checkbox"
        :checked="form.auto_generate_title"
        @change="
          personalization.updateField({
            key: 'auto_generate_title',
            value: ($event.target as HTMLInputElement).checked
          })
        "
      />
      <FancyCheck :checked="form.auto_generate_title" />
    </label>
    <div class="settings-action-row">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ $t('personalization.tutorialTitle') }}</span>
        <span class="settings-row-desc">{{ $t('personalization.tutorialDesc') }}</span>
      </span>
      <button type="button" class="settings-secondary-button" @click="startTutorial">
        {{ $t('personalization.tutorialButton') }}
      </button>
    </div>
    <div class="settings-action-row" v-if="isAppShell">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ $t('personalization.appUpdateTitle') }}</span>
        <span class="settings-row-desc">{{
          $t('personalization.appUpdateDesc', {
            version: appCurrentVersionText,
            checked: appUpdateCheckedText
          })
        }}</span>
      </span>
      <div class="settings-inline-actions">
        <span class="settings-mini-status" :class="{ warning: appHasUpdate }">{{
          appUpdateStateText
        }}</span>
        <button
          type="button"
          class="settings-secondary-button"
          :disabled="appUpdateChecking"
          @click="checkAppUpdate"
        >
          {{ appUpdateChecking ? $t('personalization.appChecking') : $t('personalization.appCheckUpdate') }}
        </button>
        <button
          v-if="appHasUpdate"
          type="button"
          class="settings-primary-button"
          @click="downloadLatestApp"
        >
          {{ $t('common.download') }}
        </button>
      </div>
    </div>
    <div class="settings-action-row sandbox-env-row">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ $t('sandbox.sectionTitle') }}</span>
        <span class="settings-row-desc">{{ $t('sandbox.sectionDesc') }}</span>
      </span>
      <div class="settings-inline-actions">
        <span class="settings-mini-status" :class="{ warning: sandboxShowWizard }">{{
          sandboxStatusText
        }}</span>
        <span v-if="sandboxSetup.neverAsk" class="settings-mini-status">{{
          $t('sandbox.neverAgainSet')
        }}</span>
        <button
          v-if="sandboxSetup.neverAsk"
          type="button"
          class="settings-secondary-button"
          @click="sandboxSetup.resetNeverAsk()"
        >
          {{ $t('sandbox.resetNeverAgain') }}
        </button>
        <button
          type="button"
          class="settings-secondary-button"
          :disabled="sandboxSetup.checking"
          @click="sandboxSetup.recheck()"
        >
          {{ sandboxSetup.checking ? $t('common.refreshing') : $t('sandbox.recheck') }}
        </button>
        <button
          v-if="sandboxShowWizard"
          type="button"
          class="settings-primary-button"
          @click="sandboxSetup.openWizard()"
        >
          {{ $t('sandbox.openWizard') }}
        </button>
      </div>
    </div>
    <div class="settings-action-row danger-zone">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ $t('personalization.logoutTitle') }}</span>
        <span class="settings-row-desc">{{ $t('personalization.logoutDesc') }}</span>
      </span>
      <button
        type="button"
        class="settings-secondary-button danger"
        @click="personalization.logout()"
      >
        {{ $t('personalization.logoutTitle') }}
      </button>
    </div>
  </section>
</template>

<style scoped>
/* 沙箱环境区块：上下结构（标题描述在上，状态徽标与按钮组整行在下），
   避免默认左右结构中右侧按钮组挤压左侧文案空间 */
.sandbox-env-row {
  grid-template-columns: minmax(0, 1fr);
  gap: 10px;
}

.sandbox-env-row .settings-inline-actions {
  justify-content: flex-start;
}
</style>
