<template>
  <transition name="quick-menu">
    <div v-if="open" class="quick-menu" @click.stop>
      <div class="quick-menu-list">
      <button
        type="button"
        class="menu-entry"
        data-tutorial="quick-upload"
        @click="$emit('quick-upload')"
        :disabled="!isConnected || uploading"
      >
        {{ uploading ? '上传中...' : '上传文件' }}
      </button>
      <button
        type="button"
        class="menu-entry"
        data-tutorial="quick-review"
        @click.stop="$emit('open-review')"
        :disabled="!isConnected || streamingMessage"
      >
        对话回顾
      </button>
      <button
        v-if="currentModelSupportsImage"
        type="button"
        class="menu-entry"
        data-tutorial="quick-send-image"
        @click.stop="$emit('pick-images')"
        :disabled="!isConnected || streamingMessage"
      >
        发送图片
      </button>
      <button
        v-if="currentModelSupportsVideo"
        type="button"
        class="menu-entry"
        data-tutorial="quick-send-video"
        @click.stop="$emit('pick-video')"
        :disabled="!isConnected || streamingMessage"
      >
        发送视频
      </button>
      <button
        type="button"
        class="menu-entry has-submenu"
        data-tutorial="quick-tool-menu"
        @click.stop="$emit('toggle-tool-menu')"
        :disabled="!isConnected"
      >
        工具禁用
        <span class="entry-arrow">›</span>
      </button>
      <button
        type="button"
        class="menu-entry"
        :class="{ 'goal-entry-active': goalModeArmed || goalRunning || goalCompleted }"
        @click.stop="$emit('toggle-goal-mode')"
        :disabled="!isConnected"
      >
        目标模式
        <span class="entry-arrow">
          <template v-if="goalRunning">运行中</template>
          <template v-else-if="goalCompleted">完成</template>
          <template v-else>{{ goalModeArmed ? '已就绪' : '' }}</template>
        </span>
      </button>
      <button
        type="button"
        class="menu-entry has-submenu"
        data-tutorial="quick-settings-menu"
        @click.stop="$emit('toggle-settings')"
        :disabled="!isConnected"
      >
        设置
        <span class="entry-arrow">›</span>
      </button>
      </div>

      <transition name="submenu-slide">
        <div class="quick-submenu tool-submenu" v-if="toolMenuOpen">
          <div class="submenu-status" v-if="toolSettingsLoading">正在同步工具状态...</div>
          <div v-else-if="!toolSettings.length" class="submenu-empty">暂无可控工具</div>
          <div v-else class="submenu-list tool-submenu-list">
            <button
              v-for="category in toolSettings"
              :key="category.id"
              type="button"
              class="menu-entry submenu-entry"
              :class="{ disabled: !category.enabled || category.locked }"
              @click.stop="$emit('update-tool-category', category.id, !category.enabled)"
              :disabled="streamingMessage || !isConnected || toolSettingsLoading"
            >
              <span class="submenu-label icon-label">
                <span
                  class="icon icon-sm"
                  :style="getIconStyle(toolCategoryIcon(category.id))"
                  aria-hidden="true"
                ></span>
                <span>{{ category.label }}</span>
              </span>
              <span class="entry-arrow">
                <template v-if="category.locked"> 被管理员锁定 </template>
                <template v-else>
                  {{ category.enabled ? '禁用' : '启用' }}
                </template>
              </span>
            </button>
          </div>
        </div>
      </transition>

      <transition name="submenu-slide">
        <div class="quick-submenu settings-submenu" v-if="settingsOpen">
          <div class="submenu-list">
            <button
              type="button"
              class="menu-entry submenu-entry"
              @click="$emit('realtime-terminal')"
              :disabled="!isConnected"
            >
              实时终端
            </button>
            <button
              type="button"
              class="menu-entry submenu-entry"
              data-tutorial="settings-token-panel"
              @click="console.log('[UI_DEBUG] QuickMenu 用量统计按钮 clicked, emitting toggle-token-panel with true'); $emit('toggle-token-panel', true)"
              :disabled="!currentConversationId"
            >
              用量统计
            </button>
            <button
              type="button"
              class="menu-entry submenu-entry"
              @click="$emit('compress-conversation')"
              :disabled="compressing || streamingMessage || !isConnected"
            >
              {{ compressing ? '压缩中...' : '压缩对话' }}
            </button>
            <button
              type="button"
              class="menu-entry submenu-entry"
              @click="$emit('toggle-approval-panel')"
              :disabled="!currentConversationId"
            >
              审批面板
            </button>
            <button
              v-if="executionModeEnabled"
              type="button"
              class="menu-entry submenu-entry"
              @click="$emit('open-path-authorization')"
            >
              路径授权
            </button>
          </div>
        </div>
      </transition>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { computed } from 'vue';

defineOptions({ name: 'QuickMenu' });

const props = defineProps<{
  open: boolean;
  isConnected: boolean;
  uploading: boolean;
  streamingMessage: boolean;
  thinkingMode: boolean;
  toolMenuOpen: boolean;
  toolSettings: Array<{ id: string; label: string; enabled: boolean }>;
  toolSettingsLoading: boolean;
  settingsOpen: boolean;
  compressing: boolean;
  currentConversationId: string | null;
  iconStyle?: (key: string) => Record<string, string>;
  toolCategoryIcon: (categoryId: string) => string;
  modeMenuOpen: boolean;
  runMode?: 'fast' | 'thinking';
  modelMenuOpen: boolean;
  modelOptions: Array<{
    key: string;
    label: string;
    description: string;
    disabled?: boolean;
    supportsImage?: boolean;
    supportsVideo?: boolean;
  }>;
  currentModelKey: string;
  blockUpload?: boolean;
  blockToolToggle?: boolean;
  blockRealtimeTerminal?: boolean;
  blockFocusPanel?: boolean;
  blockTokenPanel?: boolean;
  blockCompressConversation?: boolean;
  blockConversationReview?: boolean;
  executionModeEnabled?: boolean;
  goalModeArmed?: boolean;
  goalRunning?: boolean;
  goalProgress?: Record<string, any> | null;
}>();

defineEmits<{
  (event: 'quick-upload'): void;
  (event: 'toggle-tool-menu'): void;
  (event: 'toggle-settings'): void;
  (event: 'update-tool-category', id: string, enabled: boolean): void;
  (event: 'realtime-terminal'): void;
  (event: 'toggle-token-panel', fromSettingsMenu?: boolean): void;
  (event: 'compress-conversation'): void;
  (event: 'toggle-approval-panel'): void;
  (event: 'toggle-mode-menu'): void;
  (event: 'select-run-mode', mode: 'fast' | 'thinking'): void;
  (event: 'toggle-model-menu'): void;
  (event: 'select-model', key: string): void;
  (event: 'open-review'): void;
  (event: 'pick-video'): void;
  (event: 'open-path-authorization'): void;
  (event: 'toggle-goal-mode'): void;
}>();

const getIconStyle = (key: string) => (props.iconStyle ? props.iconStyle(key) : {});

const currentModelSupportsImage = computed(() => {
  const found = props.modelOptions?.find((m) => m.key === props.currentModelKey) as any;
  return !!found?.supportsImage;
});

const currentModelSupportsVideo = computed(() => {
  const found = props.modelOptions?.find((m) => m.key === props.currentModelKey) as any;
  return !!found?.supportsVideo;
});

const goalCompleted = computed(() => String(props.goalProgress?.status || '').toLowerCase() === 'done');
</script>

<style scoped>
.quick-menu-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 194px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: color-mix(in srgb, var(--text-primary) 22%, transparent) transparent;
}
.quick-menu-list::-webkit-scrollbar {
  width: 4px;
}
.quick-menu-list::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--text-primary) 22%, transparent);
  border-radius: 2px;
}
.quick-menu-list::-webkit-scrollbar-track {
  background: transparent;
}
.submenu-desc {
  display: block;
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 2px;
}
.menu-entry.goal-entry-active {
  color: var(--accent);
}
.menu-entry.goal-entry-active .entry-arrow {
  color: var(--accent);
  font-size: 12px;
}
.menu-entry.mode-entry-active {
  color: var(--accent);
}
.menu-entry.mode-entry-active .entry-arrow {
  color: var(--accent);
  font-size: 12px;
}
</style>
