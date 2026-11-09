import { createApp, h, nextTick, reactive, ref } from 'vue';
import MinimalBlocks from '@/components/chat/MinimalBlocks.vue';
import StackedBlocks from '@/components/chat/StackedBlocks.vue';
import {
  getToolIcon, getToolAnimationClass, getToolStatusText, getToolDescription,
  formatSearchTopic, formatSearchTime, formatSearchDomains
} from '@/utils/chatDisplay';

const frame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

(window as any).runComponentBenchmark = async (input: any, mode: string, running: boolean) => {
  const longTasks: number[] = [];
  const observer = typeof PerformanceObserver !== 'undefined' &&
    PerformanceObserver.supportedEntryTypes.includes('longtask')
    ? new PerformanceObserver((list) => list.getEntries().forEach((e) => longTasks.push(e.duration)))
    : null;
  observer?.observe({ type: 'longtask' });
  const started = performance.now();
  const actions = reactive(input.message.actions);
  const component = mode === 'minimal' ? MinimalBlocks : StackedBlocks;
  const expandedBlocks = reactive(new Set<string>());
  const collapsingBlocks = reactive(new Set<string>());
  const props = {
    actions, conversationRunning: running, isLatestMessage: true,
    iconStyle: () => ({}), getToolIcon, getToolAnimationClass, getToolStatusText,
    getToolDescription, formatSearchTopic, formatSearchTime, formatSearchDomains,
    expandedBlocks, collapsingBlocks, toggleBlock: () => {}, registerThinkingRef: () => {},
    handleThinkingScroll: () => {},
  };
  const app = createApp({ render: () => h(component as any, props) });
  const errors: string[] = [];
  app.config.errorHandler = (error: any) => errors.push(String(error?.stack || error));
  app.config.warnHandler = () => {};
  app.config.globalProperties.$t = (key: string) => key;
  app.mount('#app');
  const mounted = performance.now();
  await nextTick();
  const patched = performance.now();
  await frame();
  await frame();
  const painted = performance.now();
  const nodes = document.querySelector('#app')!.querySelectorAll('*').length;
  observer?.disconnect();
  const result = {
    mode, running, actions: actions.length,
    mount_ms: +(mounted-started).toFixed(3),
    next_tick_ms: +(patched-started).toFixed(3),
    two_frames_ms: +(painted-started).toFixed(3),
    dom_nodes: nodes, long_tasks: longTasks.map((n) => +n.toFixed(3)), errors,
  };
  app.unmount();
  await nextTick();
  return result;
};
