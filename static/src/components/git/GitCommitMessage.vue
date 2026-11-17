<script setup lang="ts">
/**
 * 提交标题（17/26，一行）；有正文或标题溢出时，标题右侧出现 26px 展开按钮，
 * 完整提交说明以弹层（UiPopover）展示，不改变页面布局。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import UiIcon from '@/components/ui/UiIcon.vue';
import UiPopover from '@/components/ui/UiPopover.vue';
const props = defineProps<{ subject: string; message?: string }>();
const line = ref<HTMLElement>();
const toggle = ref<HTMLButtonElement | null>(null);
const open = ref(false);
const overflow = ref(false);
const fullMessage = computed(() => props.message?.trim() || props.subject);
const title = computed(() => fullMessage.value.split('\n', 1)[0]);
const body = computed(() => fullMessage.value.slice(title.value.length).replace(/^\n+/, ''));
const expandable = computed(() => fullMessage.value !== props.subject.trim() || overflow.value);
let observer: ResizeObserver | undefined;
function measure() {
  if (line.value) overflow.value = line.value.scrollWidth > line.value.clientWidth + 1;
}
function close(restoreFocus: boolean) {
  open.value = false;
  if (restoreFocus) toggle.value?.focus({ preventScroll: true });
}
watch(
  () => [props.subject, props.message],
  async () => {
    open.value = false;
    overflow.value = false;
    await nextTick();
    measure();
  }
);
onMounted(() => {
  observer = new ResizeObserver(measure);
  if (line.value) observer.observe(line.value);
  measure();
});
onBeforeUnmount(() => observer?.disconnect());
</script>

<template>
  <div class="git-subject">
    <h2 ref="line">{{ subject }}</h2>
    <button
      v-if="expandable"
      ref="toggle"
      type="button"
      class="git-subject__toggle"
      :aria-label="$t('git.expandMessage')"
      :aria-expanded="open"
      aria-haspopup="dialog"
      @click="open = !open"
    >
      <UiIcon name="chevron-down" />
    </button>
    <UiPopover
      :open="open"
      :anchor="toggle"
      role="dialog"
      :label="$t('git.fullMessage')"
      :min-width="560"
      :max-height="360"
      :padding="0"
      @close="close"
    >
      <pre class="git-message"><strong>{{ title }}</strong>{{ body ? `\n\n${body}` : '' }}</pre>
    </UiPopover>
  </div>
</template>
