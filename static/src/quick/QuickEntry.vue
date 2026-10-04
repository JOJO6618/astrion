<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue';
import StatusAvatar from '@/components/avatar/StatusAvatar.vue';
import MarkdownRenderer from '@/components/chat/MarkdownRenderer.vue';
import { toolFaceKey } from '@/utils/avatarFace';
import { t } from '@/locales';
import QuickIcon from './QuickIcon.vue';
import QuickPrompt from './QuickPrompt.vue';
import { useQuickRuntime } from './runtime';
const state = useQuickRuntime();
const {
  draft,
  images,
  workspace,
  conversation,
  running,
  sending,
  reply,
  tools,
  error,
  workspaces,
  sessions,
  config,
  approvals,
  collapsed,
  streaming,
  thinking,
  workspaceLabel
} = state;
const root = ref<HTMLElement>(),
  prompt = ref<InstanceType<typeof QuickPrompt>>(),
  output = ref<HTMLElement>();
const primaryRow = ref<HTMLElement>(),
  parallelRows = ref<HTMLElement>(),
  panelHeight = ref(34),
  panelRenderHeight = ref(34);
const popup = ref<HTMLElement>(),
  menu = ref(''),
  questionAnswer = ref('');
const workspaceButton = ref<HTMLElement>(),
  chatButton = ref<HTMLElement>();
const menuPosition = ref({ left: '0px', bottom: '0px' });
const windowOpen = ref(false),
  closing = ref(false);
const pointer = ref<{ x: number; y: number }>();
const displayedReply = computed(() => [reply.value, error.value].filter(Boolean).join('\n\n'));
const replyStreaming = computed(() => streaming.value && !error.value);
const visible = computed(() =>
  Boolean(thinking.value || displayedReply.value || tools.value.length)
);
// Preserve the last rendered content while the persistent panel slides closed.
const panelContent = ref({
  reply: '',
  streaming: false,
  thinking: false,
  collapsed: false,
  tools: [] as any[],
  key: 'reply'
});
watch(
  [displayedReply, replyStreaming, thinking, tools, error, collapsed, conversation],
  () => {
    if (!visible.value) return;
    panelContent.value = {
      reply: displayedReply.value,
      streaming: replyStreaming.value,
      thinking: thinking.value && !error.value,
      collapsed: collapsed.value,
      tools: error.value ? [] : [...tools.value],
      key: `reply:${conversation.value}`
    };
  },
  { deep: true }
);
const activeTools = computed(() => tools.value.filter((tool) => !tool.done));
const setupVisible = computed(
  () => !workspace.value || config.value.screenPermission !== 'granted'
);
const chatTitle = computed(
  () =>
    sessions.value.find((item) => item.conversation_id === conversation.value)?.title ||
    t('quickEntry.newChat')
);
let observer: ResizeObserver;
let cleanupCapture: () => void,
  cleanupShow: () => void,
  cleanupHide: () => void,
  cleanupPointer: () => void;
let cleanupCaptureError: () => void;
let focusFrame = 0,
  animationFrame = 0,
  entryFrame = 0,
  showGeneration = 0;
let lastRegions = '';

function measureOutput() {
  if (!visible.value) return;
  const height =
    (primaryRow.value?.offsetHeight || 34) + (parallelRows.value?.offsetHeight || 0) + 1;
  const target = Math.min(300, Math.ceil(height));
  // Keep the full panel throughout a downward move; shrink only once it ends.
  panelRenderHeight.value = Math.max(panelRenderHeight.value, panelHeight.value, target);
  panelHeight.value = target;
}
function panelMotionEnded(event: TransitionEvent) {
  if (event.target !== event.currentTarget || event.propertyName !== 'height') return;
  panelRenderHeight.value = panelHeight.value;
}
function measureWindow() {
  if (!root.value) return;
  const bounds = root.value.getBoundingClientRect();
  if (popup.value && menu.value) {
    const anchor = menu.value === 'workspace' ? workspaceButton.value : chatButton.value;
    if (anchor) {
      const button = anchor.getBoundingClientRect(),
        width = popup.value.offsetWidth;
      const scale = bounds.width / root.value.offsetWidth || 1;
      const left = (button.right - bounds.left) / scale - width;
      menuPosition.value = {
        left: `${Math.max(0, Math.min(root.value.offsetWidth - width, left))}px`,
        bottom: `${Math.ceil((bounds.bottom - button.top) / scale + 8)}px`
      };
    }
  }
  void nextTick(syncRegions);
}
function syncRegions() {
  if (!root.value) return;
  const selectors = '.composer, .run-panel, .setup-panel, .popover, .approval-panel';
  const regions = [...root.value.querySelectorAll<HTMLElement>(selectors)].flatMap((element) => {
    const rect = element.getBoundingClientRect();
    const shell = element.classList.contains('run-panel')
      ? element.closest('.run-shell')?.getBoundingClientRect()
      : null;
    const x = Math.max(0, rect.left),
      y = Math.max(0, rect.top, shell?.top || 0);
    const right = Math.min(window.innerWidth, rect.right),
      bottom = Math.min(window.innerHeight, rect.bottom, shell?.bottom ?? Infinity);
    return right > x && bottom > y ? [{ x, y, width: right - x, height: bottom - y }] : [];
  });
  const style = getComputedStyle(document.documentElement);
  const colorNames = ['text', 'border', 'surface', 'hover', 'gradient'];
  const colors = Object.fromEntries(
    colorNames.map((name) => {
      const key = `--quick-entry-${name}`;
      return [key, style.getPropertyValue(key).trim()];
    })
  );
  const presentation = {
    label: t('quickEntry.captureWindow', { app: '{app}' }),
    failure: t('quickEntry.captureFailed'),
    colors
  };
  const serialized = JSON.stringify({ regions, presentation });
  if (serialized !== lastRegions) {
    lastRegions = serialized;
    state.bridge.layout(regions, presentation);
  }
}
function animationStarted(event: AnimationEvent) {
  if (event.target !== root.value) return;
  cancelAnimationFrame(animationFrame);
  const track = () => {
    syncRegions();
    animationFrame = requestAnimationFrame(track);
  };
  animationFrame = requestAnimationFrame(track);
}
function animationEnded(event: AnimationEvent) {
  if (event.target !== root.value) return;
  cancelAnimationFrame(animationFrame);
  syncRegions();
  if (event.animationName === 'quick-close' && closing.value) {
    closing.value = false;
    state.bridge.hidden();
  }
}
async function revealWindow() {
  const ticket = ++showGeneration;
  cancelAnimationFrame(entryFrame);
  closing.value = false;
  windowOpen.value = false;
  await nextTick();
  if (ticket !== showGeneration) return;
  // Commit a hidden, scaled initial frame before starting the visible animation.
  entryFrame = requestAnimationFrame(() => {
    if (ticket !== showGeneration) return;
    entryFrame = requestAnimationFrame(() => {
      if (ticket !== showGeneration) return;
      windowOpen.value = true;
      prompt.value?.focus();
    });
  });
}
async function toggleMenu(kind: string) {
  menu.value = menu.value === kind ? '' : kind;
  await nextTick();
  measureWindow();
  if (menu.value === 'chat') {
    try {
      await state.loadSessions();
    } catch (exception) {
      error.value = String(exception);
    }
  }
}
function closeMenu() {
  menu.value = '';
}
function menuLeft() {
  measureWindow();
}
function freezeToolRow(element: Element) {
  const row = element as HTMLElement;
  row.style.top = `${row.offsetTop}px`;
}
function outsideClick(event: PointerEvent) {
  if (
    popup.value?.contains(event.target as Node) ||
    (event.target as Element).closest('.menu-trigger')
  )
    return;
  closeMenu();
}
function keydown(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    if (menu.value) closeMenu();
    else state.bridge.hide();
  }
}
function addImage(url: string) {
  if (images.value.length < 9) {
    images.value.push(url);
    void state.save();
  }
  cancelAnimationFrame(focusFrame);
  focusFrame = requestAnimationFrame(() => prompt.value?.focus());
}
function readImages(files: FileList | File[]) {
  for (const file of Array.from(files)) {
    if (!file.type.startsWith('image/')) continue;
    const reader = new FileReader();
    reader.onload = () => addImage(String(reader.result));
    reader.readAsDataURL(file);
  }
}
function paste(event: ClipboardEvent) {
  if (event.clipboardData?.files.length) {
    event.preventDefault();
    readImages(event.clipboardData.files);
  }
}
function drop(event: DragEvent) {
  event.preventDefault();
  if (event.dataTransfer) readImages(event.dataTransfer.files);
}
async function chooseWorkspace(id: string) {
  closeMenu();
  try {
    await state.setWorkspace(id);
    config.value.workspace = id;
  } catch (exception) {
    error.value = String(exception);
  }
}
async function chooseConversation(id = '') {
  closeMenu();
  try {
    if (id) await state.restoreConversation(id);
    else await state.newChat();
  } catch (exception) {
    error.value = String(exception);
  }
}
async function askPermission() {
  config.value.screenPermission = await state.bridge.capturePermission();
}
watch([draft, images], state.save, { deep: true });
watch(error, (value) => {
  if (value) collapsed.value = false;
});
watch(
  [displayedReply, collapsed, tools, replyStreaming, thinking, images, setupVisible, approvals],
  async () => {
    await nextTick();
    measureOutput();
    measureWindow();
    if (output.value && replyStreaming.value && panelHeight.value >= 300)
      output.value.scrollTop = output.value.scrollHeight;
  },
  { deep: true }
);
watch(sessions, async () => {
  await nextTick();
  measureWindow();
});
watch(
  [primaryRow, parallelRows],
  (elements, previous) => {
    if (!observer) return;
    for (const element of previous) if (element) observer.unobserve(element);
    for (const element of elements) if (element) observer.observe(element);
  },
  { flush: 'post' }
);
onMounted(async () => {
  document.addEventListener('keydown', keydown);
  document.addEventListener('pointerdown', outsideClick);
  window.addEventListener('resize', measureWindow);
  cleanupCapture = state.bridge.onCapture(addImage);
  cleanupCaptureError = state.bridge.onCaptureError((message) => {
    error.value = message;
    collapsed.value = false;
  });
  cleanupPointer = state.bridge.onPointer((point) => {
    pointer.value = point;
  });
  cleanupShow = state.bridge.onShow(async () => {
    if (!windowOpen.value || closing.value) void revealWindow();
    try {
      await state.syncPreferences();
      await state.reconcile();
    } catch (exception) {
      error.value = String(exception);
    } finally {
      measureWindow();
      if (windowOpen.value) prompt.value?.focus();
    }
  });
  cleanupHide = state.bridge.onHide(() => {
    showGeneration++;
    cancelAnimationFrame(entryFrame);
    closeMenu();
    windowOpen.value = false;
    closing.value = true;
  });
  observer = new ResizeObserver(() => {
    measureOutput();
    measureWindow();
  });
  for (const element of [root.value, primaryRow.value, parallelRows.value])
    if (element) observer.observe(element);
  await state.initialize();
  await nextTick();
  measureOutput();
  measureWindow();
  prompt.value?.focus();
});
onBeforeUnmount(() => {
  observer?.disconnect();
  cleanupCapture?.();
  cleanupCaptureError?.();
  cleanupShow?.();
  cleanupHide?.();
  cleanupPointer?.();
  showGeneration++;
  cancelAnimationFrame(entryFrame);
  cancelAnimationFrame(focusFrame);
  cancelAnimationFrame(animationFrame);
  document.removeEventListener('keydown', keydown);
  document.removeEventListener('pointerdown', outsideClick);
  window.removeEventListener('resize', measureWindow);
  state.dispose();
});
</script>

<template>
  <main
    ref="root"
    class="quick-window"
    :class="{ 'is-open': windowOpen, 'is-closing': closing }"
    @animationstart="animationStarted"
    @animationend="animationEnded"
    @dragover.prevent
    @drop="drop"
  >
    <div
      class="run-shell"
      :class="{ visible }"
      :style="{ height: visible ? `${panelHeight}px` : '0px' }"
      @transitionend="panelMotionEnded"
    >
      <section
        class="run-panel"
        :class="{ collapsed: panelContent.collapsed }"
        :style="{ height: `${panelRenderHeight}px` }"
        :aria-hidden="!visible"
        :inert="!visible"
        role="log"
      >
        <div
          ref="output"
          class="run-scroll"
          :class="{ 'has-scrollbar': panelHeight >= 300 && !panelContent.collapsed }"
        >
          <div ref="primaryRow" class="primary-row" :class="{ reply: !!panelContent.reply }">
            <Transition name="copy">
              <div v-if="panelContent.thinking" key="thinking" class="tool-copy">
                <span class="status-icon spinner" /><span>{{ t('quickEntry.thinking') }}</span>
              </div>
              <div
                v-else-if="panelContent.reply"
                :key="panelContent.collapsed ? 'collapsed' : panelContent.key"
                class="reply-copy"
                :class="{
                  streaming: panelContent.streaming && !panelContent.collapsed,
                  'summary-copy': panelContent.collapsed
                }"
              >
                <button
                  v-if="panelContent.collapsed"
                  class="reply-summary"
                  @click="collapsed = false"
                >
                  {{ t('quickEntry.reply') }}
                </button>
                <MarkdownRenderer
                  v-else
                  :content="panelContent.reply"
                  :is-streaming="panelContent.streaming"
                />
              </div>
              <div
                v-else-if="panelContent.tools.length"
                :key="panelContent.tools[0].id"
                class="tool-copy"
              >
                <span class="status-icon" :class="{ spinner: !panelContent.tools[0].done }"
                  ><QuickIcon
                    v-if="panelContent.tools[0].done"
                    :name="panelContent.tools[0].failed ? 'circleAlert' : 'check'" /></span
                ><span>{{ panelContent.tools[0].title }}</span>
              </div>
            </Transition>
          </div>
          <div ref="parallelRows" class="parallel-rows">
            <TransitionGroup name="parallel" tag="div" @before-leave="freezeToolRow">
              <div v-for="tool in panelContent.tools.slice(1)" :key="tool.id" class="tool-copy">
                <span class="status-icon" :class="{ spinner: !tool.done }"
                  ><QuickIcon
                    v-if="tool.done"
                    :name="tool.failed ? 'circleAlert' : 'check'" /></span
                ><span>{{ tool.title }}</span>
              </div>
            </TransitionGroup>
          </div>
        </div>
        <div v-if="panelContent.reply && !running" class="collapse-zone">
          <button
            class="collapse-button"
            :class="{ expanded: !panelContent.collapsed }"
            :aria-expanded="!panelContent.collapsed"
            :aria-label="t(panelContent.collapsed ? 'quickEntry.expand' : 'quickEntry.collapse')"
            @click="collapsed = !collapsed"
          >
            <QuickIcon name="chevronDown" />
          </button>
        </div>
      </section>
    </div>
    <section class="composer" :class="{ 'has-draft': !!draft }" @paste="paste">
      <div class="input-row">
        <StatusAvatar
          :size="48"
          :hex-background="false"
          :mode="thinking ? 'think' : activeTools.length ? 'tool' : running ? 'work' : 'idle'"
          :tool-keys="activeTools.map((tool) => toolFaceKey(tool.name))"
          :pointer="pointer"
          tracking
        />
        <div v-if="images.length" class="attachments">
          <div v-for="(image, index) in images" :key="image + index" class="attachment">
            <img :src="image" alt="" /><button
              class="image-remove"
              :aria-label="t('quickEntry.removeImage')"
              @click="images.splice(index, 1)"
            >
              <QuickIcon name="x" />
            </button>
          </div>
        </div>
        <QuickPrompt ref="prompt" v-model="draft" @submit="state.send()" />
        <button
          ref="chatButton"
          class="meta-button conversation-button menu-trigger"
          :class="{ active: menu === 'chat' }"
          :aria-expanded="menu === 'chat'"
          @click="toggleMenu('chat')"
        >
          <span>{{ chatTitle }}</span
          ><QuickIcon name="chevronDown" />
        </button>
        <button
          class="send-button"
          :disabled="sending || (!running && !draft.trim() && !images.length)"
          :aria-label="
            t(running && !draft.trim() && !images.length ? 'quickEntry.stop' : 'quickEntry.send')
          "
          @click="running && !draft.trim() && !images.length ? state.stop() : state.send()"
        >
          <span v-if="running && !draft.trim() && !images.length" class="stop-icon" /><svg
            v-else
            class="send-arrow"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path d="M12 19V5m-6 6 6-6 6 6" />
          </svg>
        </button>
      </div>
    </section>
    <Transition name="setup">
      <section v-if="setupVisible" class="setup-panel">
        <strong>{{ t('quickEntry.setup') }}</strong>
        <div v-if="!workspace">
          <span>{{ t('quickEntry.workspace') }}</span
          ><button
            ref="workspaceButton"
            class="meta-button menu-trigger"
            @click="toggleMenu('workspace')"
          >
            <span>{{ workspaceLabel }}</span
            ><QuickIcon name="chevronDown" />
          </button>
        </div>
        <div v-if="config.screenPermission !== 'granted'">
          <span>{{ t('quickEntry.permission') }}</span
          ><button @click="askPermission">{{ t('quickEntry.permission') }}</button>
        </div>
      </section>
    </Transition>
    <Transition name="menu" @after-leave="menuLeft">
      <nav
        v-if="menu"
        :key="menu"
        ref="popup"
        class="popover"
        :class="{ 'conversation-popover': menu === 'chat' }"
        :style="menuPosition"
        role="menu"
      >
        <span v-if="menu === 'workspace'" class="menu-title">{{ t('quickEntry.workspace') }}</span>
        <template v-if="menu === 'workspace'">
          <button
            v-for="item in workspaces"
            :key="item.workspace_id"
            class="menu-item workspace-item"
            role="menuitemradio"
            :aria-checked="item.workspace_id === workspace"
            @click="chooseWorkspace(item.workspace_id)"
          >
            <span class="menu-copy"
              ><span>{{ item.label }}</span
              ><small>{{ item.path }}</small></span
            ><QuickIcon v-if="item.workspace_id === workspace" name="check" />
          </button>
          <span v-if="!workspaces.length" class="menu-empty">{{
            t('quickEntry.noWorkspaces')
          }}</span>
        </template>
        <template v-if="menu === 'chat'">
          <button class="menu-item" role="menuitem" @click="chooseConversation()">
            <QuickIcon name="plus" /><span class="menu-copy">{{ t('quickEntry.newChat') }}</span>
          </button>
          <div class="menu-separator" />
          <div class="conversation-list">
            <button
              v-for="item in sessions"
              :key="item.conversation_id"
              class="menu-item"
              role="menuitem"
              @click="chooseConversation(item.conversation_id)"
            >
              <span class="menu-copy">{{ item.title || item.conversation_id }}</span
              ><QuickIcon v-if="item.conversation_id === conversation" name="check" />
            </button>
          </div>
        </template>
      </nav>
    </Transition>
    <section
      v-for="item in approvals"
      :key="item.approval_id || item.question_id"
      class="approval-panel"
    >
      <strong>{{ item.question || item.tool_name || t('quickEntry.approval') }}</strong>
      <p>{{ item.context || item.summary }}</p>
      <template v-if="item.kind === 'user-questions'"
        ><button
          v-for="option in item.options || []"
          :key="option.id"
          @click="state.answer(item, true, option.label, option.id)"
        >
          {{ option.label }}</button
        ><textarea v-model="questionAnswer" /><button
          @click="state.answer(item, true, questionAnswer)"
        >
          {{ t('quickEntry.answer') }}
        </button></template
      ><template v-else
        ><button @click="state.answer(item, true)">{{ t('quickEntry.allow') }}</button
        ><button @click="state.answer(item, false)">{{ t('quickEntry.reject') }}</button></template
      >
    </section>
  </main>
</template>
