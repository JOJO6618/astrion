<template>
  <div class="append-placeholder" :class="{ 'append-error': action.append?.success === false }">
    <div class="append-placeholder-content">
      <template v-if="action.append?.success !== false">
        <div class="icon-label append-status">
          <span class="icon icon-sm" :style="iconStyle('pencil')" aria-hidden="true"></span>
          <span>{{ successText }}</span>
        </div>
      </template>
      <template v-else>
        <div class="icon-label append-status append-error-text">
          <span class="icon icon-sm" :style="iconStyle('x')" aria-hidden="true"></span>
          <span>{{ errorText }}</span>
        </div>
      </template>
      <div class="append-meta" v-if="action.append">
        <span v-if="action.append.path">{{ action.append.path }}</span>
        <span v-if="action.append.lines !== null && action.append.lines !== undefined"
          >{{ $t('chat.linesCount', { n: action.append.lines }) }}</span
        >
        <span v-if="action.append.bytes !== null && action.append.bytes !== undefined"
          >{{ $t('chat.bytesCount', { n: action.append.bytes }) }}</span
        >
      </div>
      <div class="append-warning icon-label" v-if="action.append?.forced">
        <span class="icon icon-sm" :style="iconStyle('triangleAlert')" aria-hidden="true"></span>
        <span>{{ $t('chat.appendWarning') }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { t, currentLocale } from '@/locales';

defineOptions({ name: 'AppendPayloadAction' });

const props = defineProps<{
  action: any;
  iconStyle: (key: string) => Record<string, string>;
  variant: 'payload' | 'summary';
}>();

const successText = computed(() => {
  void currentLocale.value;
  if (props.variant === 'payload') {
    return t('chat.appendSuccess', {
      path: props.action.append?.path || t('chat.targetFile'),
    });
  }
  return props.action.append?.summary || t('chat.appendDone');
});

const errorText = computed(() => {
  void currentLocale.value;
  const path = props.action.append?.path || t('chat.targetFile');
  if (props.variant === 'payload') {
    return t('chat.appendFailed', { path });
  }
  return t('chatActions.appendFailedRetry', { path });
});
</script>
