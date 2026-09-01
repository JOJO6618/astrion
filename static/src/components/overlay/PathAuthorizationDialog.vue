<template>
  <transition name="path-auth-fade">
    <div v-if="open" class="overlay-backdrop">
      <div class="overlay-card">
        <div class="overlay-header">
          <h3>{{ $t('overlay.pathAuthTitle') }}</h3>
          <CloseButton :label="$t('common.close')" @click="$emit('close')" />
        </div>
        <div class="mode-switch">
          <button
            type="button"
            class="mode-btn"
            :class="{ active: mode === 'writable' }"
            @click="$emit('update:mode', 'writable')"
          >
            {{ $t('overlay.writableMode') }}
          </button>
          <button
            type="button"
            class="mode-btn"
            :class="{ active: mode === 'readable' }"
            @click="$emit('update:mode', 'readable')"
          >
            {{ $t('overlay.readableMode') }}
          </button>
        </div>
        <p class="hint">
          {{
            mode === 'writable'
              ? $t('overlay.writableHint')
              : $t('overlay.readableHint')
          }}
        </p>
        <textarea
          class="path-input"
          :value="value"
          @input="$emit('update:value', ($event.target as HTMLTextAreaElement).value)"
          :placeholder="
            mode === 'writable'
              ? $t('overlay.writablePlaceholder')
              : $t('overlay.readablePlaceholder')
          "
        />
        <div class="actions">
          <button type="button" class="btn" @click="$emit('save')" :disabled="saving">{{ $t('common.save') }}</button>
          <button type="button" class="btn btn-muted" @click="$emit('close')">{{ $t('common.cancel') }}</button>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import CloseButton from '@/components/common/CloseButton.vue';

defineProps<{ open: boolean; value: string; mode: 'writable' | 'readable'; saving?: boolean }>();
defineEmits<{
  (e: 'close'): void;
  (e: 'save'): void;
  (e: 'update:value', v: string): void;
  (e: 'update:mode', v: 'writable' | 'readable'): void;
}>();
</script>

<style scoped>
.overlay-backdrop { position: fixed; inset: 0; background: var(--overlay-scrim); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.overlay-card { width: min(680px, 92vw); background: var(--theme-surface-soft); border: 1px solid var(--theme-control-border); border-radius: 12px; padding: 12px; }
.overlay-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.overlay-header h3 { margin: 0; font-size: 16px; }
.hint { font-size: 12px; color: var(--text-secondary); margin: 0 0 8px 0; }
.mode-switch { display: inline-flex; border: 1px solid var(--theme-control-border); border-radius: 10px; overflow: hidden; margin-bottom: 8px; }
.mode-btn { border: none; background: transparent; padding: 6px 10px; cursor: pointer; font-size: 12px; }
.mode-btn.active { background: var(--theme-tab-active); font-weight: 600; }
.path-input { width: 100%; min-height: 220px; resize: vertical; border: 1px solid var(--theme-control-border); border-radius: 8px; padding: 8px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.actions { margin-top: 10px; display: flex; gap: 8px; justify-content: flex-end; }
.btn { border: 1px solid var(--theme-control-border); background: var(--theme-tab-active); padding: 6px 12px; border-radius: 8px; cursor: pointer; }
.btn-muted { background: transparent; }

.path-auth-fade-enter-active,
.path-auth-fade-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.path-auth-fade-enter-from,
.path-auth-fade-leave-to {
  opacity: 0;
}

.path-auth-fade-enter-from .overlay-card,
.path-auth-fade-leave-to .overlay-card {
  transform: translateY(8px) scale(0.98);
}

.overlay-card {
  transition: transform 0.2s ease;
}
</style>
