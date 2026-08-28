<template>
  <div class="collapsible-block thinking-block" :class="{ expanded }">
    <div class="collapsible-header" @click="emitToggle(blockId)">
      <div class="arrow"></div>
      <div class="status-icon">
        <span class="thinking-icon" :class="{ 'thinking-animation': action.streaming }">
          <span class="icon icon-sm" :style="iconStyle('brain')" aria-hidden="true"></span>
        </span>
      </div>
      <span class="status-text">{{ action.streaming ? $t('chat.thinkingRunning') : $t('chat.thinking') }}</span>
    </div>
    <div class="collapsible-content">
      <div
        class="content-inner thinking-content"
        :ref="(el) => setRef(blockId, el)"
        @scroll="(event) => handleScroll(blockId, event)"
        style="max-height: 240px; overflow-y: auto"
      >
        {{ action.content }}
      </div>
    </div>
    <div v-if="action.streaming" class="progress-indicator"></div>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'ThinkingAction' });

const props = defineProps<{
  action: any;
  blockId: string;
  expanded: boolean;
  iconStyle: (key: string) => Record<string, string>;
  setThinkingRef: (id: string, el: Element | null) => void;
  handleThinkingScroll: (id: string, event: Event) => void;
  toggleBlock: (id: string) => void;
}>();

const emitToggle = (id: string) => {
  props.toggleBlock(id);
};

const setRef = (id: string, el: Element | null) => {
  props.setThinkingRef(id, el);
};

const handleScroll = (id: string, event: Event) => {
  props.handleThinkingScroll(id, event);
};
</script>
