<template>
  <div class="collapsible-block modify-block" :class="{ expanded }">
    <div class="collapsible-header" @click="$emit('toggle')">
      <div class="arrow"></div>
      <div class="status-icon">
        <span class="icon icon-sm" :style="iconStyle('hammer')" aria-hidden="true"></span>
      </div>
      <span class="status-text">{{ $t('chatActions.modifyRecord') }}</span>
      <span class="modify-path">{{ action.modify?.path || $t('chat.targetFile') }}</span>
    </div>
    <div class="collapsible-content">
      <div class="modify-details">
        <div class="modify-entry" v-for="entry in action.modify?.details || []" :key="entry.index">
          <div class="entry-header">
            <span class="entry-title">{{ $t('chatActions.entryTitle', { n: entry.index }) }}</span>
            <span class="entry-range">{{ entry.range?.start }} → {{ entry.range?.end }}</span>
          </div>
          <pre class="entry-content">{{ entry.content }}</pre>
        </div>
        <div class="modify-meta" v-if="action.modify">
          <span v-if="action.modify.total !== null && action.modify.total !== undefined"
            >{{ $t('chatActions.modifyTotal', { n: action.modify.total }) }}</span
          >
          <span v-if="action.modify.completed && action.modify.completed.length"
            >{{ $t('chatActions.modifyCompleted', { n: action.modify.completed.length }) }}</span
          >
          <span v-if="action.modify.failed && action.modify.failed.length"
            >{{ $t('chatActions.modifyRemaining', { n: action.modify.failed.length }) }}</span
          >
        </div>
        <div class="modify-warning icon-label" v-if="action.modify?.forced">
          <span class="icon icon-sm" :style="iconStyle('triangleAlert')" aria-hidden="true"></span>
          <span>{{ $t('chatActions.modifyWarning') }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'ModifyDetailsAction' });

defineProps<{
  action: any;
  expanded: boolean;
  iconStyle: (key: string) => Record<string, string>;
}>();

defineEmits<{ (event: 'toggle'): void }>();
</script>
