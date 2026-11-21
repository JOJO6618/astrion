<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import UiDialog from '@/components/ui/UiDialog.vue';
import UiDialogButton from '@/components/ui/UiDialogButton.vue';
import UiSelect from '@/components/ui/UiSelect.vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';
import { t } from '@/locales';
import type { McpServerConfig, McpTransport } from './useMcpSettings';

const props = defineProps<{
  open: boolean;
  config: McpServerConfig | null;
  busy: boolean;
  error: string;
}>();
const emit = defineEmits<{ close: []; save: [config: Partial<McpServerConfig>] }>();
const draft = ref(blankDraft());
const validation = ref('');
const transports = computed(() => [
  { value: 'stdio', label: t('settings.mcp.stdio') },
  { value: 'streamable_http', label: t('settings.mcp.http') }
]);

function blankDraft() {
  return {
    id: '',
    name: '',
    description: '',
    transport: 'stdio' as McpTransport,
    enabled: true,
    command: '',
    args: '',
    cwd: '',
    env: '',
    url: '',
    headers: '',
    timeout_seconds: 25,
    include_tools: '',
    exclude_tools: ''
  };
}

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    validation.value = '';
    const config = props.config;
    draft.value = config
      ? {
          ...config,
          args: config.args.join('\n'),
          env: Object.keys(config.env).length ? JSON.stringify(config.env, null, 2) : '',
          headers: Object.keys(config.headers).length
            ? JSON.stringify(config.headers, null, 2)
            : '',
          include_tools: config.include_tools.join(', '),
          exclude_tools: config.exclude_tools.join(', ')
        }
      : blankDraft();
  }
);

function parseObject(value: string, field: string): Record<string, string> {
  if (!value.trim()) return {};
  try {
    const decoded: unknown = JSON.parse(value);
    if (!decoded || typeof decoded !== 'object' || Array.isArray(decoded)) throw new Error();
    if (Object.values(decoded).some((item) => typeof item !== 'string')) throw new Error();
    return decoded as Record<string, string>;
  } catch {
    throw new Error(t('settings.mcp.invalidObject', { field }));
  }
}

function split(value: string, separator: string) {
  return value
    .split(separator)
    .map((item) => item.trim())
    .filter(Boolean);
}

function submit() {
  validation.value = '';
  const form = draft.value;
  if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(form.id.trim())) {
    validation.value = t('settings.mcp.invalidId');
    return;
  }
  if (form.transport === 'stdio' ? !form.command.trim() : !form.url.trim()) {
    validation.value = t(
      form.transport === 'stdio' ? 'settings.mcp.commandRequired' : 'settings.mcp.urlRequired'
    );
    return;
  }
  const timeout = Number(form.timeout_seconds);
  if (!Number.isInteger(timeout) || timeout < 1) {
    validation.value = t('settings.mcp.invalidTimeout');
    return;
  }
  try {
    const { args, env, headers, include_tools, exclude_tools, ...base } = form;
    emit('save', {
      ...base,
      id: form.id.trim(),
      name: form.name.trim(),
      timeout_seconds: timeout,
      args: split(args, '\n'),
      env: parseObject(env, t('settings.mcp.env')),
      headers: parseObject(headers, t('settings.mcp.headers')),
      include_tools: split(include_tools, ','),
      exclude_tools: split(exclude_tools, ',')
    });
  } catch (err) {
    validation.value = (err as Error).message;
  }
}
</script>

<template>
  <UiDialog
    :open="open"
    :title="$t(config ? 'settings.mcp.edit' : 'settings.mcp.add')"
    :busy="busy"
    size="wide"
    @close="emit('close')"
  >
    <form class="mcp-editor" @submit.prevent="submit">
      <label class="ui-dialog-field">
        <span class="ui-dialog-label">{{ $t('settings.mcp.id') }}</span>
        <input
          v-model="draft.id"
          class="ui-dialog-input"
          :disabled="!!config || busy"
          spellcheck="false"
        />
      </label>
      <label class="ui-dialog-field">
        <span class="ui-dialog-label">{{ $t('settings.mcp.name') }}</span>
        <input v-model="draft.name" class="ui-dialog-input" :disabled="busy" />
      </label>
      <div class="ui-dialog-field">
        <span class="ui-dialog-label">{{ $t('settings.mcp.transport') }}</span>
        <UiSelect
          :model-value="draft.transport"
          :options="transports"
          :label="$t('settings.mcp.transport')"
          :disabled="busy"
          @update:model-value="draft.transport = $event as McpTransport"
        />
      </div>
      <template v-if="draft.transport === 'stdio'">
        <label class="ui-dialog-field">
          <span class="ui-dialog-label">{{ $t('settings.mcp.command') }}</span>
          <input
            v-model="draft.command"
            class="ui-dialog-input"
            :disabled="busy"
            spellcheck="false"
          />
        </label>
        <label class="ui-dialog-field">
          <span class="ui-dialog-label">{{ $t('settings.mcp.args') }}</span>
          <textarea
            v-model="draft.args"
            class="ui-dialog-textarea"
            rows="3"
            :disabled="busy"
            spellcheck="false"
          />
        </label>
        <label class="ui-dialog-field">
          <span class="ui-dialog-label">{{ $t('settings.mcp.cwd') }}</span>
          <input v-model="draft.cwd" class="ui-dialog-input" :disabled="busy" spellcheck="false" />
        </label>
        <label class="ui-dialog-field">
          <span class="ui-dialog-label">{{ $t('settings.mcp.env') }}</span>
          <textarea
            v-model="draft.env"
            class="ui-dialog-textarea"
            rows="3"
            :disabled="busy"
            spellcheck="false"
          />
        </label>
      </template>
      <template v-else>
        <label class="ui-dialog-field">
          <span class="ui-dialog-label">{{ $t('settings.mcp.url') }}</span>
          <input v-model="draft.url" class="ui-dialog-input" :disabled="busy" spellcheck="false" />
        </label>
        <label class="ui-dialog-field">
          <span class="ui-dialog-label">{{ $t('settings.mcp.headers') }}</span>
          <textarea
            v-model="draft.headers"
            class="ui-dialog-textarea"
            rows="3"
            :disabled="busy"
            spellcheck="false"
          />
        </label>
      </template>
      <label class="ui-dialog-field">
        <span class="ui-dialog-label">{{ $t('settings.mcp.timeout') }}</span>
        <input
          v-model.number="draft.timeout_seconds"
          class="ui-dialog-input"
          inputmode="numeric"
          :disabled="busy"
        />
      </label>
      <label class="ui-dialog-field">
        <span class="ui-dialog-label">{{ $t('settings.mcp.includeTools') }}</span>
        <input
          v-model="draft.include_tools"
          class="ui-dialog-input"
          :disabled="busy"
          spellcheck="false"
        />
      </label>
      <label class="ui-dialog-field">
        <span class="ui-dialog-label">{{ $t('settings.mcp.excludeTools') }}</span>
        <input
          v-model="draft.exclude_tools"
          class="ui-dialog-input"
          :disabled="busy"
          spellcheck="false"
        />
      </label>
      <label class="ui-dialog-field">
        <span class="ui-dialog-label">{{ $t('settings.mcp.description') }}</span>
        <input v-model="draft.description" class="ui-dialog-input" :disabled="busy" />
      </label>
      <div class="ui-dialog-switch-row">
        <span class="ui-dialog-label">{{ $t('settings.mcp.serviceEnabled') }}</span>
        <UiSwitch
          :checked="draft.enabled"
          :disabled="busy"
          :label="$t('settings.mcp.serviceEnabled')"
          @toggle="draft.enabled = !draft.enabled"
        />
      </div>
      <p v-if="validation || error" class="ui-dialog-error" role="alert">
        {{ validation || error }}
      </p>
    </form>
    <template #actions>
      <UiDialogButton :disabled="busy" @click="emit('close')">{{
        $t('common.cancel')
      }}</UiDialogButton>
      <UiDialogButton variant="primary" :disabled="busy" @click="submit">{{
        $t('common.save')
      }}</UiDialogButton>
    </template>
  </UiDialog>
</template>

<style scoped>
.mcp-editor {
  display: grid;
  gap: 20px;
}
</style>
