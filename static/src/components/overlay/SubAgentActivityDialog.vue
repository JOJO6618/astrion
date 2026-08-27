<template>
  <transition name="overlay-fade">
    <div v-if="activeAgent" class="subagent-activity-overlay" @click.self="close">
      <div class="subagent-activity-modal">
        <div class="subagent-activity-header">
          <div class="subagent-activity-title">
            子智能体 #{{ activeAgent.agent_id || activeAgent.task_id }} 进度
          </div>
          <button type="button" class="subagent-activity-close" @click="close">×</button>
        </div>
        <div class="subagent-activity-meta">
          <span class="subagent-activity-status" :class="activeAgent.status || ''">{{
            activeAgent.status || 'running'
          }}</span>
          <span class="subagent-activity-summary" v-if="activeAgent.summary">{{
            activeAgent.summary
          }}</span>
        </div>
        <div class="subagent-activity-actions" v-if="canStop">
          <button
            type="button"
            class="subagent-stop-btn"
            :disabled="stopLoading"
            @click="handleStop"
          >
            {{ stopLoading ? '停止中...' : '手动停止' }}
          </button>
          <span v-if="stopError" class="subagent-activity-error">{{ stopError }}</span>
        </div>
        <div class="subagent-activity-body">
          <div v-if="activityError" class="subagent-activity-error">{{ activityError }}</div>
          <div v-else-if="!timelineItems.length" class="subagent-activity-empty">
            {{ activityLoading ? '正在读取子智能体活动...' : '暂无活动记录' }}
          </div>
          <div v-else class="subagent-activity-list">
            <div
              v-for="item in timelineItems"
              :key="item.key"
              class="subagent-activity-item"
              :class="{ 'subagent-output-item': item.kind === 'output', expanded: item.kind === 'output' && expandedOutputs.has(item.key) }"
              @click="handleItemClick(item)"
            >
              <template v-if="item.kind === 'output'">
                <div class="subagent-output-content">{{ item.content }}</div>
              </template>
              <template v-else>
                <span class="subagent-activity-text">{{ item.text }}</span>
                <span class="subagent-activity-state" :class="item.state">{{ item.stateLabel }}</span>
              </template>
            </div>
          </div>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { storeToRefs } from 'pinia';
import { useSubAgentStore } from '@/stores/subAgent';

type ActivityEntry = {
  type?: string;
  id?: string;
  tool?: string;
  status?: string;
  args?: Record<string, any>;
  ts?: number;
  subtype?: string;
  content?: string;
  is_final?: boolean;
};

const subAgentStore = useSubAgentStore();
const { activeAgent, activityEntries, activityLoading, activityError, stoppingTaskIds } =
  storeToRefs(subAgentStore);
const stopError = ref('');
const expandedOutputs = ref<Set<string>>(new Set());

const close = () => {
  subAgentStore.closeSubAgent();
};

const normalizeStatus = (status?: string) => {
  if (status === 'running' || status === 'in_progress') return 'running';
  if (status === 'calling') return 'calling';
  if (status === 'completed' || status === 'done' || status === 'success') return 'completed';
  if (status === 'failed' || status === 'error') return 'failed';
  return status || 'running';
};

const buildText = (entry: ActivityEntry) => {
  const tool = entry.tool || '';
  const args = entry.args || {};
  if (tool === 'read_file') {
    const path = args.path || args.file_path || '';
    return `阅读 ${path}`;
  }
  if (tool === 'write_file') {
    const path = args.file_path || args.path || '';
    return `写入文件 ${path}`;
  }
  if (tool === 'read_skill') {
    const skillName = args.skill_name || '';
    return `阅读技能 ${skillName}`;
  }
  if (tool === 'web_search') {
    const query = args.query || args.q || '';
    return `在互联网中搜索 ${query}`;
  }
  if (tool === 'extract_webpage') {
    const url = args.url || '';
    return `在互联网中提取 ${url}`;
  }
  if (tool === 'run_command') {
    const command = args.command || '';
    return `运行命令 ${command}`;
  }
  if (tool === 'edit_file') {
    const path = args.path || args.file_path || '';
    return `编辑 ${path}`;
  }
  if (tool === 'read_mediafile') {
    const path = args.path || args.file_path || '';
    return `读取媒体文件 ${path}`;
  }
  return `${tool || '工具'}`;
};

const canStop = computed(() => {
  if (!activeAgent.value?.task_id) return false;
  return !isTerminalStatus(activeAgent.value.status);
});

const stopLoading = computed(() => {
  const taskId = activeAgent.value?.task_id;
  if (!taskId) return false;
  return !!stoppingTaskIds.value?.[taskId];
});

const handleStop = async () => {
  const taskId = activeAgent.value?.task_id;
  if (!taskId || stopLoading.value) return;
  stopError.value = '';
  const result = await subAgentStore.terminateSubAgent(taskId);
  if (!result?.success) {
    stopError.value = result?.error || '停止失败';
  }
};

const isTerminalStatus = (status?: string) => {
  const normalized = (status || '').toString().toLowerCase();
  return ['completed', 'failed', 'timeout', 'terminated', 'cancelled'].includes(normalized);
};

const toggleOutput = (key: string) => {
  const next = new Set(expandedOutputs.value);
  if (next.has(key)) {
    next.delete(key);
  } else {
    next.add(key);
  }
  expandedOutputs.value = next;
};

const handleItemClick = (item: any) => {
  if (item.kind === 'output') {
    toggleOutput(item.key);
  }
};

const timelineItems = computed(() => {
  const entries = activityEntries.value || [];
  const rawItems: { kind: 'tool'; key: string; entry: ActivityEntry } | { kind: 'output'; key: string; content: string; isFinal: boolean }[] = [];
  let currentToolGroup: { kind: 'tool'; key: string; entry: ActivityEntry } | null = null;

  const flushToolGroup = () => {
    if (currentToolGroup) {
      rawItems.push(currentToolGroup);
      currentToolGroup = null;
    }
  };

  entries.forEach((entry: ActivityEntry, index: number) => {
    if (entry?.type === 'progress' && entry?.subtype === 'output' && typeof entry.content === 'string') {
      flushToolGroup();
      // 语义顺序修正：工具「正在调用」事件在流式期间先于文本 output 落盘，
      // 但 assistant 消息里文本永远在 tool_calls 之前——把 output 插入到
      // 尾部连续的非终态工具条目（同一条 assistant 消息发起的调用）之前
      const outputItem = {
        kind: 'output' as const,
        key: `output-${entry.ts || index}`,
        content: entry.content,
        isFinal: !!entry.is_final,
      };
      let cut = rawItems.length;
      while (cut > 0) {
        const tail = rawItems[cut - 1];
        if (tail.kind === 'tool' && !isTerminalStatus(tail.entry?.status)) {
          cut -= 1;
        } else {
          break;
        }
      }
      rawItems.splice(cut, 0, outputItem);
      return;
    }

    if (!entry || entry.type !== 'progress' || !entry.tool) return;

    const baseKey = entry.id || `${entry.tool}-${entry.ts || index}`;
    if (
      currentToolGroup &&
      (currentToolGroup.entry.id === entry.id || currentToolGroup.key === baseKey) &&
      !isTerminalStatus(currentToolGroup.entry.status)
    ) {
      currentToolGroup.entry = { ...currentToolGroup.entry, ...entry };
      return;
    }

    // 同一 tool_call id 的历史条目仍非终态时原地更新（「正在调用」事件可能与其他
    // 工具的调用事件交错到达），避免出现永远转圈的重复条目
    if (entry.id) {
      const prior = rawItems.find(
        (item) =>
          item.kind === 'tool' &&
          item.entry.id === entry.id &&
          !isTerminalStatus(item.entry.status)
      );
      if (prior && prior.kind === 'tool') {
        prior.entry = { ...prior.entry, ...entry };
        return;
      }
    }

    flushToolGroup();
    let key = baseKey;
    let suffix = 0;
    while (rawItems.some((item) => item.kind === 'tool' && item.key === key)) {
      suffix++;
      key = `${baseKey}--${suffix}`;
    }
    currentToolGroup = { kind: 'tool', key, entry: { ...entry } };
  });

  flushToolGroup();

  return rawItems.map((item) => {
    if (item.kind === 'output') return item;
    const state = normalizeStatus(item.entry.status);
    return {
      kind: 'tool' as const,
      key: item.key,
      state,
      stateLabel: state === 'completed' ? '完成' : state === 'failed' ? '失败' : state === 'calling' ? '调用中' : '进行中',
      text: buildText(item.entry)
    };
  });
});
</script>

<style scoped>
.subagent-output-section {
  margin-bottom: 16px;
}
.subagent-output-title,
.subagent-activity-list-title {
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 8px;
  color: var(--text-primary);
}
.subagent-output-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.subagent-output-item {
  display: block;
  cursor: pointer;
}
.subagent-output-content {
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.5;
  text-align: left;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}
.subagent-output-item.expanded .subagent-output-content {
  -webkit-line-clamp: unset;
  display: block;
}
</style>
