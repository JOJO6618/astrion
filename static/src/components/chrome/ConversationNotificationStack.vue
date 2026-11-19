<template>
  <div class="conversation-notifications" aria-live="polite">
    <div
      v-for="banner in banners"
      :key="banner.id"
      class="conversation-notification"
      :class="{ 'is-dismissing': dismissTimers.has(banner.id) }"
      :inert="dismissTimers.has(banner.id)"
    >
      <button
        type="button"
        class="conversation-notification-body"
        :aria-label="`${t('appUi.conversationNotification.open', { title: titleOf(banner) })} · ${statusOf(banner)}`"
        @click="openConversation(banner.id)"
      >
        <span class="conversation-notification-copy">
          <span class="conversation-notification-title">{{ titleOf(banner) }}</span>
          <span class="conversation-notification-meta">
            <template v-if="banner.workspaceLabel">
              <span class="conversation-notification-workspace">{{ banner.workspaceLabel }}</span>
              <span aria-hidden="true">·</span>
            </template>
            <span class="conversation-notification-status">{{ statusOf(banner) }}</span>
          </span>
        </span>
      </button>
      <div class="conversation-notification-action">
        <button type="button" class="conversation-notification-dismiss" @click="dismiss(banner.id)">
          {{ t('appUi.conversationNotification.dismiss') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, reactive, shallowRef } from 'vue';
import { useI18n } from 'vue-i18n';
import type {
  ConversationBanner,
  ConversationNotificationsBridge
} from './conversationNotificationBridge';

const { bridge } = defineProps<{ bridge: ConversationNotificationsBridge }>();
const { t } = useI18n();
const banners = shallowRef<ConversationBanner[]>([]);
const titleOf = (banner: ConversationBanner) =>
  banner.title || t('appUi.conversationNotification.untitled');
const statusOf = (banner: ConversationBanner) => t(`appUi.conversationNotification.${banner.type}`);
const dismissTimers = reactive(new Map<string, number | null>());

function openConversation(id: string) {
  if (dismissTimers.has(id)) return;
  bridge.open(id);
  dismiss(id);
}

function dismiss(id: string) {
  if (dismissTimers.has(id)) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    bridge.dismiss(id);
    return;
  }
  dismissTimers.set(
    id,
    window.setTimeout(() => {
      if (banners.value.some((banner) => banner.id === id)) dismissTimers.set(id, null);
      else dismissTimers.delete(id);
      bridge.dismiss(id);
    }, 150)
  );
}

let unsubscribe: (() => void) | undefined;
onMounted(() => {
  unsubscribe = bridge.onBanners((next) => {
    banners.value = next;
    for (const [id, timer] of dismissTimers) {
      if (timer === null && !next.some((banner) => banner.id === id)) dismissTimers.delete(id);
    }
  });
  bridge.ready();
});
onUnmounted(() => {
  unsubscribe?.();
  for (const timer of dismissTimers.values()) {
    if (timer !== null) clearTimeout(timer);
  }
});
</script>

<style scoped lang="scss">
.conversation-notifications {
  --conversation-notification-slide-offset: calc(100% + 12px);

  display: grid;
  gap: 8px;
  padding: 12px;
  font-family:
    -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

.conversation-notification {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  height: 68px;
  border: 1px solid var(--popover-border);
  border-radius: 12px;
  background: var(--group-card-bg);
  box-shadow: var(--popover-shadow);
  color: var(--text-primary);
  animation: conversation-notification-enter 220ms cubic-bezier(0.2, 0.8, 0.2, 1);
}

.conversation-notification.is-dismissing {
  animation: conversation-notification-exit 150ms cubic-bezier(0.4, 0, 1, 1) forwards;
}

.conversation-notification-body,
.conversation-notification-dismiss {
  appearance: none;
  border: none;
  background: transparent;
  font: inherit;
  color: inherit;
  cursor: pointer;
  transition:
    background-color 120ms ease,
    color 120ms ease;
}

.conversation-notification-body {
  grid-area: 1 / 1 / 2 / 3;
  display: grid;
  grid-template-columns: subgrid;
  align-items: center;
  padding: 0;
  border-radius: 11px;
  text-align: start;
}

.conversation-notification-body:hover,
.conversation-notification-dismiss:hover {
  background: var(--hover-bg);
  color: var(--text-primary);
}

.conversation-notification-body:focus-visible,
.conversation-notification-dismiss:focus-visible {
  outline: 2px solid var(--text-secondary);
  outline-offset: -3px;
}

.conversation-notification-copy {
  display: grid;
  grid-column: 1;
  gap: 2px;
  min-width: 0;
  padding-inline: 14px 8px;
}

.conversation-notification-title,
.conversation-notification-workspace,
.conversation-notification-status {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conversation-notification-title {
  font-size: 14px;
  line-height: 20px;
  font-weight: 600;
}

.conversation-notification-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 18px;
}

.conversation-notification-workspace {
  min-width: 0;
}
.conversation-notification-status {
  flex: 0 0 auto;
  max-width: 100%;
}

.conversation-notification-action {
  z-index: 1;
  grid-area: 1 / 2;
  display: grid;
  align-content: center;
  padding-inline: 4px 8px;
  pointer-events: none;
}

.conversation-notification-dismiss {
  min-width: 48px;
  height: 32px;
  padding-inline: 10px;
  border-radius: 8px;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  pointer-events: auto;
}

@keyframes conversation-notification-enter {
  from {
    transform: translateX(var(--conversation-notification-slide-offset));
  }
  to {
    transform: translateX(0);
  }
}

@keyframes conversation-notification-exit {
  to {
    transform: translateX(var(--conversation-notification-slide-offset));
  }
}

@media (prefers-reduced-motion: reduce) {
  .conversation-notification,
  .conversation-notification.is-dismissing {
    animation: none;
  }
  .conversation-notification-body,
  .conversation-notification-dismiss {
    transition: none;
  }
}
</style>
