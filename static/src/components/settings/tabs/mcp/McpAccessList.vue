<script setup lang="ts">
import { computed } from 'vue';
import UiSelect from '@/components/ui/UiSelect.vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';
import { t } from '@/locales';
import type { McpServerSummary } from './useMcpSettings';

const props = defineProps<{
  title: string;
  description: string;
  servers: McpServerSummary[];
  serverIds: string[];
  mode?: 'inherit' | 'custom';
  disabled?: boolean;
}>();
const emit = defineEmits<{
  selection: [ids: string[]];
  mode: [mode: 'inherit' | 'custom'];
}>();
const modes = computed(() => [
  { value: 'inherit', label: t('settings.mcp.inherit') },
  { value: 'custom', label: t('settings.mcp.custom') }
]);

function toggle(id: string) {
  const ids = new Set(props.serverIds);
  if (!ids.delete(id)) ids.add(id);
  emit('selection', [...ids]);
}
</script>

<template>
  <div class="stg-select-row">
    <span class="stg-row-copy">
      <span class="stg-row-title">{{ title }}</span>
      <span class="stg-row-desc">{{ description }}</span>
    </span>
    <UiSelect
      v-if="mode"
      :model-value="mode"
      :options="modes"
      :label="title"
      :disabled="disabled"
      align-end
      @update:model-value="emit('mode', $event === 'inherit' ? 'inherit' : 'custom')"
    />
  </div>
  <div v-for="server in servers" :key="server.id" class="stg-select-row mcp-access-server">
    <span class="stg-row-copy">
      <span class="stg-row-title">{{ server.name }}</span>
      <span v-if="!server.enabled" class="stg-row-desc">{{
        $t('settings.mcp.globallyDisabled')
      }}</span>
    </span>
    <UiSwitch
      :checked="server.enabled && serverIds.includes(server.id)"
      :disabled="disabled || mode === 'inherit' || !server.enabled"
      :label="`${title} · ${server.name}`"
      @toggle="toggle(server.id)"
    />
  </div>
</template>

<style scoped>
.mcp-access-server {
  min-height: 40px;
  padding-inline-start: 16px;
}
</style>
