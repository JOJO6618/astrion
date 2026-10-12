<template>
  <div ref="container" class="inline-ui" :aria-busy="!closed && streaming">
    <iframe
      v-if="js === 'on' && srcdoc"
      ref="frame"
      class="inline-ui__frame"
      :srcdoc="srcdoc"
      :style="{ height: `${height}px` }"
      :inert="!interactive"
      :tabindex="interactive ? 0 : -1"
      :title="$t('appCore.inlineUiTitle')"
      sandbox="allow-scripts"
      referrerpolicy="no-referrer"
      allow="fullscreen 'none'"
    ></iframe>
    <div v-else ref="staticHost" class="inline-ui__static"></div>
    <div v-if="error" class="inline-ui__error" role="status">{{ $t('appCore.inlineUiScriptError') }}</div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { patchInlineUi, prepareInlineUi } from '@/utils/inlineUi/content';
import {
  applyInlineUiEnvironment,
  observeInlineUiEnvironment,
  readInlineUiEnvironment
} from '@/utils/inlineUi/environment';
import { buildInlineUiSandbox, INLINE_UI_BASE_STYLE } from '@/utils/inlineUi/sandbox';

const props = defineProps<{
  source: string;
  js: 'off' | 'on';
  closed: boolean;
  streaming?: boolean;
}>();
const container = ref<HTMLElement | null>(null);
const staticHost = ref<HTMLElement | null>(null);
const frame = ref<HTMLIFrameElement | null>(null);
const srcdoc = ref('');
const height = ref(0);
const interactive = ref(false);
const error = ref(false);
let root: HTMLElement | null = null;
let instance = '';
let ready = false;
let mounted = false;
let disposed = false;
let committed = false;
let finalSource = '';
let sequence = 0;
let environmentKey = '';
let lastSource: string | null = null;
let lastClosed = false;
let timer: ReturnType<typeof setTimeout> | null = null;
let resize: ResizeObserver | null = null;
let stopEnvironment: (() => void) | null = null;
let generation = 0;

function post(data: Record<string, unknown>) {
  if (!ready || disposed) return;
  frame.value?.contentWindow?.postMessage({ channel: 'astrion-inline-ui', instance, ...data }, '*');
}

function syncEnvironment() {
  if (!container.value || disposed) return;
  const environment = readInlineUiEnvironment(container.value);
  applyInlineUiEnvironment(container.value, environment);
  const key = JSON.stringify(environment);
  if (key === environmentKey) return;
  environmentKey = key;
  post({ type: 'environment', environment });
}

function flush() {
  timer = null;
  if (disposed || !mounted || (props.js === 'on' && !ready)) return;
  syncEnvironment();
  if (props.source === lastSource && props.closed === lastClosed) return;
  const content = prepareInlineUi(props.source, props.js === 'on' && props.closed);
  lastSource = props.source;
  lastClosed = props.closed;
  if (props.js === 'on') {
    if (committed) return;
    if (props.closed) {
      committed = true;
      finalSource = props.source;
    }
    post({ type: 'content', sequence: ++sequence, closed: props.closed, ...content });
  } else if (root) {
    const template = document.createElement('template');
    template.innerHTML = content.html;
    patchInlineUi(root, template.content);
    root.inert = !props.closed;
  }
}

function schedule() {
  if (!mounted || disposed) return;
  if (props.closed) {
    if (timer) clearTimeout(timer);
    flush();
  } else if (!timer) {
    timer = setTimeout(flush, 100);
  }
}

async function initialize() {
  const owner = ++generation;
  if (timer) clearTimeout(timer);
  timer = null;
  ready = false;
  committed = false;
  interactive.value = false;
  error.value = false;
  root = null;
  lastSource = null;
  environmentKey = '';
  instance = crypto.randomUUID();
  await nextTick();
  if (disposed || owner !== generation) return;
  if (props.js === 'on' && container.value) {
    const environment = readInlineUiEnvironment(container.value);
    applyInlineUiEnvironment(container.value, environment);
    srcdoc.value = buildInlineUiSandbox(instance, environment);
  } else if (staticHost.value) {
    const shadow = staticHost.value.shadowRoot || staticHost.value.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = `:host { display: block; font: inherit; color: inherit; background: transparent; } ${INLINE_UI_BASE_STYLE}`;
    root = document.createElement('div');
    root.className = 'inline-ui-content';
    shadow.replaceChildren(style, root);
    root.addEventListener('click', (event) => {
      const anchor = (event.target as Element)?.closest?.('a[href]');
      if (anchor && /^https?:/i.test(anchor.getAttribute('href') || '')) {
        anchor.setAttribute('target', '_blank');
        anchor.setAttribute('rel', 'noopener noreferrer');
      }
    });
    schedule();
  }
  syncEnvironment();
}

function receive(event: MessageEvent) {
  const data = event.data;
  if (disposed || props.js !== 'on' || event.source !== frame.value?.contentWindow ||
      !data || data.channel !== 'astrion-inline-ui' || data.instance !== instance) return;
  if (data.type === 'ready') {
    ready = true;
    environmentKey = '';
    schedule();
  } else if (data.type === 'height' && Number.isFinite(data.height)) {
    height.value = Math.max(0, Math.min(100000, Math.ceil(data.height)));
  } else if (data.type === 'committed') {
    interactive.value = true;
  } else if (data.type === 'error') {
    error.value = true;
    interactive.value = false;
  }
}

watch(() => [props.source, props.js, props.closed] as const, (value, previous) => {
  if (!mounted) return;
  if (value[1] !== previous[1] || (committed && (!props.closed || props.source !== finalSource))) {
    void initialize();
  } else {
    schedule();
  }
});
onMounted(() => {
  mounted = true;
  window.addEventListener('message', receive);
  stopEnvironment = observeInlineUiEnvironment(syncEnvironment);
  resize = new ResizeObserver(syncEnvironment);
  if (container.value) resize.observe(container.value);
  void initialize();
});
onBeforeUnmount(() => {
  disposed = true;
  generation++;
  if (timer) clearTimeout(timer);
  resize?.disconnect();
  stopEnvironment?.();
  window.removeEventListener('message', receive);
  if (frame.value) frame.value.srcdoc = '';
  root = null;
});
</script>

<style scoped>
.inline-ui {
  display: block;
  position: relative;
  width: 100%;
  min-width: 0;
  margin: 12px 0;
  background: transparent;
  font: inherit;
  color: inherit;
  contain: layout style;
}
.inline-ui__frame {
  display: block;
  width: 100%;
  border: 0;
  background: transparent;
}
.inline-ui__static {
  display: block;
  min-width: 0;
  overflow-x: clip;
}
.inline-ui__error {
  margin-top: 6px;
  color: var(--text-secondary);
  font-size: 0.875em;
  line-height: 1.5;
}
</style>
