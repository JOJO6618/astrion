<template>
  <section class="tool-approval-panel" :aria-label="panelTitle">
    <header class="approval-header">
      <strong>{{ panelTitle }}</strong>
      <button
        type="button"
        class="approval-collapse"
        :aria-label="$t('shell.collapseApprovalPanel')"
        @mousedown.prevent
        @click="$emit('close')"
      >
        <ApprovalChevron />
      </button>
    </header>
    <div
      v-if="current"
      class="approval-columns"
      :class="{ 'approval-columns--automatic': automatic }"
    >
      <div class="approval-tool-column">
        <div class="approval-tool-content">
          <div class="approval-tool-heading">
            <code>{{ current.tool_name }}</code>
            <span v-if="historical && historyStatus">{{
              $t(`shell.finalDecision.${historyStatus}`)
            }}</span>
          </div>
          <div v-if="filePath" class="approval-path">{{ filePath }}</div>
          <div v-if="editLines.length" class="approval-preview approval-diff">
            <div
              v-for="(line, index) in editLines"
              :key="index"
              class="diff-line"
              :class="`diff-line--${line.kind}`"
            >
              {{ line.prefix }} {{ line.content }}
            </div>
          </div>
          <pre v-else-if="previewText" class="approval-preview">{{ previewText }}</pre>
          <div v-if="requestReason" class="approval-reason">
            <span>{{ $t('shell.requestReason') }}</span>
            <p>{{ requestReason }}</p>
          </div>
          <div v-if="extraArguments" class="approval-parameters">
            <span>{{ $t('shell.parameters') }}</span>
            <pre>{{ extraArguments }}</pre>
          </div>
        </div>
        <div v-if="!historical" class="approval-actions">
          <button
            type="button"
            class="approval-btn"
            :disabled="busy || terminalStatus"
            @click="$emit('reject', current.approval_id)"
          >
            {{ $t('shell.reject') }}
          </button>
          <button
            type="button"
            class="approval-btn approval-btn--allow"
            :disabled="busy || terminalStatus || humanApproved || reviewStatus === 'rejected'"
            @click="$emit('approve', current.approval_id)"
          >
            {{
              humanApproved
                ? $t('shell.humanDecision.approved')
                : $t(
                    isFullAccessApproval(current) ? 'shell.allowFullAccessExecution' : 'shell.allow'
                  )
            }}
          </button>
        </div>
      </div>
      <div v-if="automatic" class="approval-review">
        <div class="approval-review-heading">
          <span>{{ $t('appTasks.autoApprovalRecordTitle') }}</span>
          <span :class="`review-status--${reviewStatus}`">{{
            historical && ['pending', 'reviewing'].includes(reviewStatus)
              ? $t('shell.reviewStages.complete')
              : $t(`shell.autoReviewStatus.${reviewStatus}`)
          }}</span>
        </div>
        <div class="approval-review-progress">
          <div v-for="(line, index) in reviewLines" :key="index" class="approval-review-line">
            {{ line }}
          </div>
          <p v-if="reviewReason" class="approval-review-reason">{{ reviewReason }}</p>
        </div>
      </div>
    </div>
    <div v-else-if="historyRecord" class="approval-history-content">
      <p v-if="historyRecord.final_decision">
        {{ $t(`shell.finalDecision.${historyRecord.final_decision}`) }}
      </p>
      <div
        v-for="(progress, index) in historyRecord.progress"
        :key="index"
        class="approval-review-line"
      >
        {{ renderProgress(progress) }}
      </div>
      <p v-if="historyRecord.reason" class="approval-review-reason">{{ historyRecord.reason }}</p>
    </div>
    <div v-else class="approval-empty">{{ $t('shell.noApprovalRecords') }}</div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { t, currentLocale } from '@/locales';
import ApprovalChevron from '@/components/input/ApprovalChevron.vue';
import type {
  ToolApproval,
  ApprovalReviewRecord,
  ApprovalProgress
} from '@/components/input/approvalModel';
import { isFullAccessApproval, renderApprovalProgress } from '@/components/input/approvalModel';

const props = withDefaults(
  defineProps<{
    approvals: ToolApproval[];
    decidingApprovalIds?: string[];
    reviewRecords?: ApprovalReviewRecord[];
  }>(),
  { decidingApprovalIds: () => [], reviewRecords: () => [] }
);
defineEmits<{
  (event: 'approve', approvalId: string): void;
  (event: 'reject', approvalId: string): void;
  (event: 'close'): void;
}>();
// Live decisions stay separate from read-only history opened by the user.
const historical = computed(() => !props.approvals.length);
const historyRecord = computed(() => props.reviewRecords[props.reviewRecords.length - 1] || null);
const current = computed(() => props.approvals[0] || historyRecord.value?.approval || null);
const historyStatus = computed(() => {
  const status = String(current.value?.status || '');
  return ['approved', 'rejected', 'expired', 'cancelled', 'timeout'].includes(status) ? status : '';
});
const automatic = computed(() => !!current.value?.auto_review_required);
const record = computed(() =>
  props.reviewRecords.find((entry) => entry.approval_id === current.value?.approval_id)
);
const reviewStatus = computed(
  () => current.value?.auto_review_status || record.value?.decision || 'reviewing'
);
const humanApproved = computed(() => current.value?.human_decision === 'approved');
const terminalStatus = computed(() =>
  ['approved', 'rejected', 'expired'].includes(String(current.value?.status || ''))
);
const busy = computed(
  () => !!current.value && props.decidingApprovalIds.includes(current.value.approval_id)
);
const panelTitle = computed(() => {
  void currentLocale.value;
  return t(
    historical.value
      ? 'shell.reviewRecords'
      : isFullAccessApproval(current.value)
        ? 'shell.singleFullAccess'
        : 'shell.approvalTitle'
  );
});
const filePath = computed(() => {
  const preview = current.value?.preview || {};
  if (preview.old_path && preview.new_path) return `${preview.old_path} → ${preview.new_path}`;
  return String(preview.file_path || preview.resolved_path || preview.path || '');
});
const editLines = computed(() => {
  const context = current.value?.preview?.edit_context;
  if (!context) return [];
  return [
    ...(context.old || []).map((line: { content?: string }) => ({
      kind: 'remove',
      prefix: '−',
      content: String(line.content ?? '')
    })),
    ...(context.new || []).map((line: { content?: string }) => ({
      kind: 'add',
      prefix: '+',
      content: String(line.content ?? '')
    }))
  ];
});
const previewText = computed(() => {
  const item = current.value;
  if (!item) return '';
  return String(
    item.preview?.code ||
      item.preview?.command ||
      item.arguments?.command ||
      item.preview?.content_preview ||
      item.preview?.summary ||
      ''
  );
});
const requestReason = computed(() => String(current.value?.arguments?.full_access_reason || ''));
const extraArguments = computed(() => {
  const hidden = new Set([
    'intent',
    'command',
    'code',
    'content',
    'replacements',
    'file_path',
    'path',
    'working_dir',
    'request_full_access',
    'full_access_reason',
    'run_in_background'
  ]);
  const values = Object.fromEntries(
    Object.entries(current.value?.arguments || {}).filter(([key]) => !hidden.has(key))
  );
  return Object.keys(values).length ? JSON.stringify(values, null, 2) : '';
});
const reviewReason = computed(() =>
  String(current.value?.auto_review_reason || record.value?.reason || '')
);
const renderProgress = (progress: ApprovalProgress) => renderApprovalProgress(progress, t);
const reviewLines = computed(() => {
  void currentLocale.value;
  return (current.value?.auto_review_progress || record.value?.progress || [])
    .map(renderProgress)
    .filter(Boolean);
});
</script>

<style scoped>
.tool-approval-panel {
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  width: 100%;
  max-width: 100%;
  height: 404px;
  max-height: max(180px, calc(100dvh - 190px));
  border: 1px solid var(--border-default);
  border-radius: 9px 9px 0 0;
  background: var(--surface-soft);
  overflow: hidden;
}
.approval-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex: 0 0 40px;
  height: 40px;
  padding: 0 15px;
  border-bottom: 1px solid var(--border-default);
  color: var(--text-primary);
  font-size: 12px;
}
.approval-header strong {
  font-weight: 600;
}
.approval-collapse {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 27px;
  height: 27px;
  padding: 0;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
}
.approval-collapse:hover {
  background: var(--hover-bg);
  color: var(--text-primary);
}
.approval-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  color: var(--text-tertiary);
  font-size: 12px;
}
.approval-history-content {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 14px 17px;
}
.approval-columns {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  flex: 1;
  min-height: 0;
}
.approval-columns--automatic {
  grid-template-columns: minmax(0, 1fr) 32%;
}
.approval-tool-column {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.approval-tool-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  padding: 14px 17px;
  overflow: auto;
}
.approval-tool-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  height: 20px;
  color: var(--text-primary);
}
.approval-tool-heading > span {
  color: var(--text-secondary);
  font-size: 11px;
  white-space: nowrap;
}
.approval-tool-heading code {
  font-size: 12px;
  font-weight: 600;
}
.approval-path {
  margin-top: 8px;
  color: var(--text-secondary);
  overflow-wrap: anywhere;
  font: 11px/1.6 monospace;
}
.approval-preview {
  margin: 10px 0 0;
  padding: 9px 10px;
  border-radius: 3px;
  background: var(--surface-muted);
  color: var(--text-primary);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: 11px/1.7 monospace;
}
.diff-line {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.diff-line--remove {
  color: var(--state-danger);
}
.diff-line--add {
  color: var(--state-success);
}
.approval-reason,
.approval-parameters {
  margin-top: 11px;
  font-size: 11px;
  color: var(--text-secondary);
}
.approval-reason > span,
.approval-parameters > span {
  color: var(--text-tertiary);
}
.approval-reason p {
  margin: 4px 0 0;
  line-height: 1.65;
  overflow-wrap: anywhere;
}
.approval-parameters pre {
  margin: 4px 0 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 11px;
}
.approval-review {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 15px;
  border-left: 1px solid var(--border-default);
}
.approval-review-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  height: 22px;
  font-size: 11px;
  color: var(--text-secondary);
}
.approval-review-heading > span:last-child {
  font-size: 10px;
  color: var(--text-tertiary);
}
.approval-review-heading .review-status--approved {
  color: var(--state-success);
}
.approval-review-heading .review-status--rejected {
  color: var(--state-danger);
}
.approval-review-progress {
  margin-top: 9px;
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.approval-review-line {
  margin: 0 0 7px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 11px;
  line-height: 1.65;
  color: var(--text-secondary);
}
.approval-review-reason {
  margin: 8px 0 0;
  font-size: 11px;
  line-height: 1.65;
  color: var(--text-secondary);
}
.approval-actions {
  display: flex;
  align-items: center;
  gap: 7px;
  flex: 0 0 60px;
  height: 60px;
  box-sizing: border-box;
  padding: 0 17px;
  border-top: 1px solid var(--border-default);
}
.approval-btn {
  height: 32px;
  min-width: 0;
  padding: 0 10px;
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 11px;
}
.approval-btn:hover:not(:disabled) {
  background: var(--hover-bg);
  color: var(--text-primary);
}
.approval-btn--allow {
  border-color: var(--accent);
  background: var(--accent);
  color: var(--on-accent);
}
.approval-btn--allow:hover:not(:disabled) {
  border-color: var(--accent-hover);
  background: var(--accent-hover);
  color: var(--on-accent);
}
.approval-btn:disabled {
  opacity: 0.45;
  cursor: default;
}
.approval-tool-content,
.approval-review-progress {
  scrollbar-width: thin;
  scrollbar-color: var(--border-strong) transparent;
}
@media (max-width: 740px) {
  .approval-columns--automatic {
    grid-template-columns: minmax(0, 1fr) 37%;
  }
  .approval-tool-content,
  .approval-review {
    padding: 12px;
  }
  .approval-actions {
    padding: 0 12px;
  }
  .approval-review-heading {
    flex-wrap: wrap;
    height: 34px;
  }
}
@media (max-width: 510px) {
  .approval-columns--automatic {
    grid-template-columns: minmax(0, 1fr) 40%;
  }
  .approval-actions {
    gap: 6px;
  }
}
</style>
