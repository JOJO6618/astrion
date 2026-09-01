<template>
  <aside
    class="sidebar right-sidebar tool-approval-panel"
    :class="{ collapsed, 'is-goal-approval-mode': isGoalApprovalMode }"
    :style="{ width: collapsed ? '0px' : width + 'px' }"
  >
    <div class="sidebar-header" :class="{ 'mobile-header': isMobileViewport }">
      <h3 class="icon-label">{{ panelTitle }}</h3>
      <CloseButton :label="$t('shell.closeApprovalPanel')" @click="handleCloseClick" />
    </div>
    <div class="approval-panel-body" v-if="!collapsed">
      <div v-if="!approvals.length && !isGoalApprovalMode" class="no-files">{{ $t('shell.noPendingApprovals') }}</div>
        <div v-else class="approval-list">
          <div v-for="item in approvals" :key="item.approval_id" class="approval-card">
            <div class="approval-card__title">{{ item.tool_name }}</div>
            <div class="approval-card__summary">{{ getToolLabel(item.tool_name) }}</div>

            <div v-if="isEditPreview(item)" class="approval-edit-preview">
              <div class="approval-path">{{ item.preview?.file_path || item.preview?.resolved_path }}</div>
              <div class="tool-result-diff approval-diff">
                <div
                  class="diff-line diff-remove"
                  v-for="(line, idx) in getEditOldLines(item)"
                  :key="`o-${item.approval_id}-${idx}`"
                >
                  - {{ line }}
                </div>
                <div
                  class="diff-line diff-add"
                  v-for="(line, idx) in getEditNewLines(item)"
                  :key="`n-${item.approval_id}-${idx}`"
                >
                  + {{ line }}
                </div>
              </div>
            </div>

            <div v-else-if="isWritePreview(item)" class="approval-edit-preview">
              <div class="approval-path">{{ item.preview?.file_path }}</div>
              <div class="tool-result-diff approval-diff">
                <div
                  class="diff-line diff-add"
                  v-for="(line, idx) in getWriteLines(item)"
                  :key="`w-${item.approval_id}-${idx}`"
                >
                  + {{ line }}
                </div>
              </div>
            </div>

            <div v-else-if="isPathOnlyPreview(item)" class="approval-kv">
              <div>
                <strong>{{ $t('shell.pathLabel') }}</strong
                ><span class="approval-value approval-value--path">{{ resolvePath(item) }}</span>
              </div>
            </div>

            <div v-else-if="isRenamePreview(item)" class="approval-kv">
              <div>
                <strong>{{ $t('shell.renameLabel') }}</strong
                ><span class="approval-value approval-value--path"
                  >{{ item.preview?.old_path }} → {{ item.preview?.new_path }}</span
                >
              </div>
            </div>

            <pre
            v-else-if="isCommandPreview(item)"
            class="approval-lines approval-lines--cmd"
            >{{ item.preview?.code || item.preview?.command || '' }}</pre>

            <div v-else class="approval-kv">
              <div><strong>{{ $t('shell.toolLabel') }}</strong>{{ item.tool_name }}</div>
              <div v-if="item.preview?.summary"><strong>{{ $t('shell.summaryLabel') }}</strong>{{ item.preview.summary }}</div>
            </div>
          <div class="approval-actions">
            <button
              type="button"
              class="approval-btn approval-btn--switch"
              :disabled="isDeciding(item.approval_id)"
              @click="$emit('switch-unrestricted', item.approval_id)"
            >
              {{ $t('shell.switchToUnrestricted') }}
            </button>
            <button
              type="button"
              class="approval-btn approval-btn--approve"
              :disabled="isDeciding(item.approval_id)"
              @click="$emit('approve', item.approval_id)"
            >
              {{ $t('shell.run') }}
            </button>
            <button
              type="button"
              class="approval-btn approval-btn--reject"
              :disabled="isDeciding(item.approval_id)"
              @click="$emit('reject', item.approval_id)"
            >
              {{ $t('shell.reject') }}
            </button>
          </div>
        </div>
      </div>
      <div
        v-if="autoApprovalFeedLines.length || autoApprovalFinalMessage"
        class="auto-approval-block"
        :class="{ 'auto-approval-block--goal': isGoalApprovalMode }"
      >
        <div v-if="!isGoalApprovalMode" class="auto-approval-block__title">{{ autoApprovalTitle }}</div>
        <pre class="auto-approval-block__content">{{ autoApprovalFeedLines.join('\n') }}</pre>
        <div v-if="autoApprovalFinalMessage" class="auto-approval-block__final">
          <div
            v-if="parseFinalMessage(autoApprovalFinalMessage).title"
            :class="[
              'auto-approval-block__decision',
              parseFinalMessage(autoApprovalFinalMessage).isApproved
                ? 'auto-approval-block__decision--approved'
                : 'auto-approval-block__decision--rejected'
            ]"
          >
            {{ parseFinalMessage(autoApprovalFinalMessage).title }}
          </div>
          <div class="auto-approval-block__reason">{{ parseFinalMessage(autoApprovalFinalMessage).reason }}</div>
        </div>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { onMounted, computed } from 'vue';
import { t, currentLocale } from '@/locales';
import CloseButton from '@/components/common/CloseButton.vue';

defineOptions({ name: 'ToolApprovalPanel' });

const props = defineProps<{
  collapsed: boolean;
  width: number;
  approvals: Array<any>;
  decidingApprovalIds?: string[];
  autoApprovalFeedLines?: string[];
  autoApprovalFinalMessage?: string;
  autoApprovalTitle?: string;
}>();

const autoApprovalTitle = computed(() => {
  void currentLocale.value;
  return props.autoApprovalTitle || t('appTasks.autoApprovalRecordTitle');
});
const isGoalApprovalMode = computed(() => {
  void currentLocale.value;
  return autoApprovalTitle.value === t('appTasks.goalReviewTitle');
});
const panelTitle = computed(() => {
  void currentLocale.value;
  return isGoalApprovalMode.value
    ? t('appTasks.goalReviewTitle')
    : t('shell.toolApprovalTitle', { n: props.approvals.length });
});

const emit = defineEmits<{
  (event: 'approve', approvalId: string): void;
  (event: 'reject', approvalId: string): void;
  (event: 'switch-unrestricted', approvalId: string): void;
  (event: 'close'): void;
}>();

const handleCloseClick = () => {
  emit('close');
};

// 检测是否为移动端视口 - 使用计算属性确保响应式
const isMobileViewport = computed(() => {
  const isMobile = typeof window !== 'undefined' && window.innerWidth <= 768;
  return isMobile;
});

onMounted(() => {
  
  // 检查父元素链
  const aside = document.querySelector('.tool-approval-panel');
  const sidebarHeader = document.querySelector('.tool-approval-panel .sidebar-header');
  if (sidebarHeader) {
    const computedStyle = window.getComputedStyle(sidebarHeader);
  }
});

const isEditPreview = (item: any) => item?.tool_name === 'edit_file' && item?.preview?.edit_context;
const isCommandPreview = (item: any) =>
  ['run_command', 'terminal_input'].includes(item?.tool_name);
const isWritePreview = (item: any) => item?.tool_name === 'write_file';
const isRenamePreview = (item: any) => item?.tool_name === 'rename_file';
const isPathOnlyPreview = (item: any) =>
  ['create_file', 'create_folder', 'delete_file'].includes(item?.tool_name);

const isDeciding = (approvalId: string) =>
  Array.isArray(props.decidingApprovalIds) && props.decidingApprovalIds.includes(approvalId);

const resolvePath = (item: any) => {
  return item?.preview?.path || item?.preview?.file_path || item?.arguments?.path || item?.arguments?.file_path || '';
};

const getToolLabel = (toolName: string) => {
  void currentLocale.value;
  const name = String(toolName || '');
  const map: Record<string, string> = {
    run_command: 'shell.toolRunCommand',
    terminal_input: 'shell.toolTerminalInput',
    create_file: 'shell.toolCreateFile',
    create_folder: 'shell.toolCreateFolder',
    delete_file: 'shell.toolDeleteFile',
    rename_file: 'shell.toolRenameFile',
    write_file: 'shell.toolWriteFile',
    edit_file: 'shell.toolEditFile'
  };
  const labelKey = map[name];
  if (labelKey) return t(labelKey);
  return name || t('shell.pendingApproval');
};

const getWriteLines = (item: any): string[] => {
  const text = String(item?.preview?.content_preview || '');
  return text.split('\n');
};

const getEditOldLines = (item: any): string[] => {
  const rows = Array.isArray(item?.preview?.edit_context?.old) ? item.preview.edit_context.old : [];
  return rows.map((row: any) => String(row?.content ?? ''));
};

const getEditNewLines = (item: any): string[] => {
  const rows = Array.isArray(item?.preview?.edit_context?.new) ? item.preview.edit_context.new : [];
  return rows.map((row: any) => String(row?.content ?? ''));
};

const parseFinalMessage = (text: string) => {
  const raw = String(text || '');
  const lines = raw.split('\n');
  const title = String(lines[0] || '').trim();
  const reason = String(lines.slice(1).join('\n') || '').trim();
  return {
    title,
    reason,
    isApproved: title.includes(t('appTasks.approvalApproved'))
  };
};

</script>

<style scoped>
.auto-approval-block {
  margin-top: 12px;
  padding: 10px;
  border: 1px solid var(--theme-control-border);
  border-radius: 8px;
  background: var(--theme-surface-muted);
}

.auto-approval-block__title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--text-primary);
}

.auto-approval-block__content {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-secondary);
}

.auto-approval-block__final {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.5;
}

.auto-approval-block__decision {
  font-weight: 700;
  margin-bottom: 4px;
  color: var(--text-primary);
}

.auto-approval-block__decision--approved {
  color: var(--state-success);
}

.auto-approval-block__decision--rejected {
  color: var(--state-danger);
}

.auto-approval-block__reason {
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-secondary);
}

.tool-approval-panel.is-goal-approval-mode .auto-approval-block {
  background: var(--theme-surface-card);
  border-color: var(--theme-control-border);
  color: var(--text-primary);
}

.tool-approval-panel.is-goal-approval-mode .auto-approval-block__content,
.tool-approval-panel.is-goal-approval-mode .auto-approval-block__reason {
  color: var(--text-secondary);
}

.tool-approval-panel.is-goal-approval-mode .auto-approval-block__decision {
  color: var(--text-primary);
}

:global(:root[data-theme='dark']) .auto-approval-block,
:global(body[data-theme='dark']) .auto-approval-block {
  background: color-mix(in srgb, var(--theme-surface-muted) 86%, white 4%);
  border-color: var(--theme-control-border-strong);
  color: var(--text-primary);
}
:global(:root[data-theme='dark']) .auto-approval-block__content,
:global(body[data-theme='dark']) .auto-approval-block__content {
  color: var(--text-secondary);
}
</style>
