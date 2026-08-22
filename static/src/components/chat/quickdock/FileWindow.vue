<template>
  <section v-if="rows.length" class="qd-window" :class="{ 'qd-window-enter': windowEntering }">
    <header class="qd-window__header">
      <svg class="qd-window__icon" viewBox="0 0 16 16" fill="none">
        <path
          d="M4 2.5h5.5L12 5v8.5H4V2.5z"
          stroke="currentColor"
          stroke-width="1.3"
          stroke-linejoin="round"
        />
        <path d="M9.5 2.5V5H12" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
      </svg>
      <span class="qd-window__title">文件</span>
      <span class="qd-window__counter">{{ rows.length }}</span>
    </header>
    <ul ref="listRef" class="qd-list">
      <li
        v-for="row in rows"
        :key="row.path"
        class="qd-file-item"
        :class="{ 'is-active': previewPath === row.path, 'qd-row-enter': row.entering }"
        :style="row.pendingEnter ? { opacity: '0' } : undefined"
        :title="row.path"
        @click="openRow(row)"
      >
        <button class="qd-row-menu-btn" title="更多" @click.stop="openMenu($event, row)">
          <svg viewBox="0 0 16 16">
            <circle cx="3.5" cy="8" r="1.3" fill="currentColor" />
            <circle cx="8" cy="8" r="1.3" fill="currentColor" />
            <circle cx="12.5" cy="8" r="1.3" fill="currentColor" />
          </svg>
        </button>
        <span class="qd-file-name">{{ basename(row.path) }}</span>
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { nextTick, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useQuickDockStore } from '@/stores/quickDock';

/**
 * 文件记录窗口
 * 本次对话中编辑/创建过且仍存在的文件；同一 path 只出现一次。
 * 行结构：[⋯] 文件名；点击行 → 右侧预览侧边栏；⋯ → 菜单（下载/打开/复制路径）
 */

interface Row {
  path: string;
  entering: boolean;
  pendingEnter: boolean;
}

const quickDock = useQuickDockStore();
const { editedFiles, previewPath } = storeToRefs(quickDock);

const rows = ref<Row[]>([]);
const windowEntering = ref(false);
const listRef = ref<HTMLElement | null>(null);
let windowEnterShown = false;

function basename(p: string): string {
  return p.split('/').pop() || p;
}

function openRow(row: Row) {
  quickDock.closeMenu();
  quickDock.openPreview(row.path);
}

function openMenu(e: MouseEvent, row: Row) {
  const btn = e.currentTarget as HTMLElement;
  const rect = btn.getBoundingClientRect();
  quickDock.openMenu({
    type: 'file',
    key: row.path,
    left: rect.left, // 与按钮左对齐、向下展开
    top: rect.bottom + 6,
    alignRight: false
  });
}

function scrollElIntoView(el: HTMLElement): Promise<void> {
  return new Promise((resolve) => {
    const listEl = listRef.value;
    if (!listEl) {
      resolve();
      return;
    }
    const listRect = listEl.getBoundingClientRect();
    const elRect = el.getBoundingClientRect();
    const fullyVisible = elRect.top >= listRect.top && elRect.bottom <= listRect.bottom;
    if (fullyVisible) {
      resolve();
      return;
    }
    let done = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const finish = () => {
      if (done) {
        return;
      }
      done = true;
      if (timer) {
        clearTimeout(timer);
      }
      listEl.removeEventListener('scroll', onScroll);
      resolve();
    };
    const onScroll = () => {
      if (timer) {
        clearTimeout(timer);
      }
      timer = setTimeout(finish, 90);
    };
    listEl.addEventListener('scroll', onScroll);
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    timer = setTimeout(finish, 800);
  });
}

async function playEnter(row: Row, delay = 0) {
  if (delay > 0) {
    // 空→有场景：等窗口进入动画播完再开始（与待办窗口的时序一致）
    await new Promise((resolve) => setTimeout(resolve, delay));
  }
  await nextTick();
  const listEl = listRef.value;
  const index = rows.value.indexOf(row);
  const el = listEl?.children[index] as HTMLElement | undefined;
  if (!el) {
    row.pendingEnter = false;
    row.entering = true;
    setTimeout(() => {
      row.entering = false;
    }, 380);
    return;
  }
  await scrollElIntoView(el);
  row.pendingEnter = false;
  row.entering = true;
  setTimeout(() => {
    row.entering = false;
  }, 380);
}

// 首次（重）挂载的水合渲染一律静态：移动端↔桌面端切换时组件随 QuickDock
// 整体 v-if 卸载重挂，immediate watch 立即以现有数据触发；此时 editedFilesLive
// 可能是任务期 socket 事件残留的 true（socket 是最后一个写入者，之后没有
// REST 回填重置它，与待办不同），不拦截会把全部行误判为「运行期新增」重播
// 进入动画。只有挂载后 store 的新更新才允许走 live 动画路径。
let hydrated = false;

watch(
  editedFiles,
  (list) => {
    // live=true 来自任务期实时事件（播动画）；false 来自加载/bootstrap（静态呈现）
    const live = quickDock.editedFilesLive && hydrated;
    hydrated = true;
    const byPath = new Map(list.map((item) => [item.path, item]));

    // 移除已消失的行（文件被删除 / 对话切换清空）
    rows.value = rows.value.filter((row) => byPath.has(row.path));

    // 新增行：实时事件隐身插入 → 滚动露出 → 播进入动画；加载来源直接静态插入
    const existing = new Set(rows.value.map((row) => row.path));
    const added = list.filter((item) => !existing.has(item.path));
    if (added.length) {
      const playWindow = live && !windowEnterShown && !rows.value.length;
      if (playWindow) {
        windowEnterShown = true;
        windowEntering.value = true;
        setTimeout(() => {
          windowEntering.value = false;
        }, 320);
      }
      added.forEach((item, i) => {
        const row: Row = { path: item.path, entering: false, pendingEnter: live };
        rows.value.push(row);
        if (live) {
          // 与待办窗口一致：窗口动画 0.3s 先播完，条目再按 stagger 60ms
          // 逐个从左进入；窗口动画不重播时（非首次）条目直接依次进入。
          // 必须传 reactive 代理（rows.value 尾元素）而非上面的 raw 对象：
          // 直接改 raw 不会 trigger 响应式更新，新行会永远卡在 opacity:0。
          const delay = (playWindow ? 300 : 0) + i * 60;
          void playEnter(rows.value[rows.value.length - 1], delay);
        }
      });
    }
  },
  { immediate: true }
);
</script>
