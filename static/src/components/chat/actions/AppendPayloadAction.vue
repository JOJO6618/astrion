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
          >· 行数 {{ action.append.lines }}</span
        >
        <span v-if="action.append.bytes !== null && action.append.bytes !== undefined"
          >· 字节 {{ action.append.bytes }}</span
        >
      </div>
      <div class="append-warning icon-label" v-if="action.append?.forced">
        <span class="icon icon-sm" :style="iconStyle('triangleAlert')" aria-hidden="true"></span>
        <span>未检测到结束标记，请根据提示继续补充。</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'AppendPayloadAction' });

const props = defineProps<{
  action: any;
  iconStyle: (key: string) => Record<string, string>;
  variant: 'payload' | 'summary';
}>();

const successText =
  props.variant === 'payload'
    ? `已写入 ${props.action.append?.path || '目标文件'} 的追加内容（内容已保存至文件）`
    : props.action.append?.summary || '文件追加完成';

const errorText =
  props.variant === 'payload'
    ? `向 ${props.action.append?.path || '目标文件'} 写入失败，内容已截获供后续修复。`
    : `${props.action.append?.path || '目标文件'} 写入失败，请按提示重新尝试。`;
</script>
