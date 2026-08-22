<template>
  <div class="collapsible-block modify-block" :class="{ expanded }">
    <div class="collapsible-header" @click="$emit('toggle')">
      <div class="arrow"></div>
      <div class="status-icon">
        <span class="icon icon-sm" :style="iconStyle('hammer')" aria-hidden="true"></span>
      </div>
      <span class="status-text">修改记录</span>
      <span class="modify-path">{{ action.modify?.path || '目标文件' }}</span>
    </div>
    <div class="collapsible-content">
      <div class="modify-details">
        <div class="modify-entry" v-for="entry in action.modify?.details || []" :key="entry.index">
          <div class="entry-header">
            <span class="entry-title">修改 {{ entry.index }}</span>
            <span class="entry-range">{{ entry.range?.start }} → {{ entry.range?.end }}</span>
          </div>
          <pre class="entry-content">{{ entry.content }}</pre>
        </div>
        <div class="modify-meta" v-if="action.modify">
          <span v-if="action.modify.total !== null && action.modify.total !== undefined"
            >· 共 {{ action.modify.total }} 处</span
          >
          <span v-if="action.modify.completed && action.modify.completed.length"
            >· 已完成 {{ action.modify.completed.length }} 处</span
          >
          <span v-if="action.modify.failed && action.modify.failed.length"
            >· 未完成 {{ action.modify.failed.length }} 处</span
          >
        </div>
        <div class="modify-warning icon-label" v-if="action.modify?.forced">
          <span class="icon icon-sm" :style="iconStyle('triangleAlert')" aria-hidden="true"></span>
          <span>未检测到结束标记，系统已自动处理。</span>
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
