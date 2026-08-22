<template>
  <section v-if="visible" class="qd-window" :class="{ 'qd-window-enter': windowEntering }">
    <header class="qd-window__header">
      <svg class="qd-window__icon" viewBox="0 0 16 16" fill="none">
        <path
          d="M2 4.5 3.5 6 6 3.5"
          stroke="currentColor"
          stroke-width="1.4"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
        <path d="M8.5 5H14" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
        <path
          d="M2 11 3.5 12.5 6 10"
          stroke="currentColor"
          stroke-width="1.4"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
        <path d="M8.5 11.5H14" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
      </svg>
      <span class="qd-window__title">待办事项</span>
      <span class="qd-window__counter">{{ doneCount }}/{{ rows.length }}</span>
    </header>
    <ul ref="listRef" class="qd-list">
      <li
        v-for="row in rows"
        :key="row.key"
        class="qd-todo-item"
        :class="{
          'is-done': row.done,
          'just-done': row.justDone,
          'qd-row-enter': row.entering,
          'qd-row-leave': row.leaving
        }"
        :style="rowStyle(row)"
      >
        <span class="qd-todo-dot"></span>
        <span class="qd-todo-text">{{ row.title }}</span>
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { useFileStore } from '@/stores/file';

/**
 * 待办事项窗口
 * 三组动画（参数与 demo 定稿一致）：
 * 1. 创建/整表替换：旧行逐行向左移出（stagger 45ms + 高度收拢）→ 新行逐行从左向右进入（stagger 60ms）
 * 2. 完成：未完全显示的行先平滑滚动露出 → 横线从左往右划过 + 文字变灰 + 状态点弹跳
 * 3. 清空：移出后隐藏窗口
 */

const ENTER_STAGGER = 60;
const LEAVE_STAGGER = 45;
const LEAVE_DURATION = 260;

interface TodoTask {
  index: number;
  title: string;
  status: string;
}

interface Row {
  key: string;
  title: string;
  done: boolean;
  entering: boolean;
  leaving: boolean;
  justDone: boolean;
  enterDelay: number;
  leaveDelay: number;
}

const fileStore = useFileStore();
const todoList = computed(() => fileStore.todoList);

const rows = ref<Row[]>([]);
const visible = ref(false);
const windowEntering = ref(false);
const busy = ref(false);
const listRef = ref<HTMLElement | null>(null);

/** 代际标记：动画流程中途来了新数据时，旧流程不再写状态 */
let gen = 0;

const doneCount = computed(() => rows.value.filter((r) => r.done).length);

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function tasksOf(val: any): TodoTask[] {
  return Array.isArray(val?.tasks) ? val.tasks : [];
}

function signature(tasks: TodoTask[]): string {
  return tasks.map((t) => `${t.index}:${t.title}`).join('|');
}

function isDoneStatus(status: any): boolean {
  return status === 'done' || status === 'completed';
}

function rowStyle(row: Row) {
  if (row.entering && row.enterDelay) {
    return { animationDelay: `${row.enterDelay}ms` };
  }
  if (row.leaving && row.leaveDelay) {
    return { animationDelay: `${row.leaveDelay}ms` };
  }
  return undefined;
}

async function playLeave() {
  const current = rows.value;
  if (!current.length) {
    return;
  }
  current.forEach((row, i) => {
    row.leaveDelay = i * LEAVE_STAGGER;
    row.leaving = true;
  });
  await wait(current.length * LEAVE_STAGGER + LEAVE_DURATION + 20);
}

function renderEntering(tasks: TodoTask[], baseDelay = 0) {
  rows.value = tasks.map((t, i) => ({
    key: `${t.index}:${t.title}`,
    title: t.title,
    done: isDoneStatus(t.status),
    entering: true,
    leaving: false,
    justDone: false,
    // baseDelay：空→有场景让窗口进入动画先播完，条目再依次进入（顺序而非同时）
    enterDelay: baseDelay + i * ENTER_STAGGER,
    leaveDelay: 0
  }));
  // 用 setTimeout 而非 animationend：动画事件会冒泡造成干扰
  const total = baseDelay + tasks.length * ENTER_STAGGER + 340 + 30;
  const myGen = gen;
  setTimeout(() => {
    if (myGen !== gen) {
      return;
    }
    rows.value.forEach((row) => {
      row.entering = false;
      row.enterDelay = 0;
    });
  }, total);
}

/** 若目标行未完全显示，先平滑滚动到可见位置 */
function scrollRowIntoView(index: number): Promise<void> {
  return new Promise((resolve) => {
    nextTick(() => {
      const listEl = listRef.value;
      const li = listEl?.children[index] as HTMLElement | undefined;
      if (!listEl || !li) {
        resolve();
        return;
      }
      const listRect = listEl.getBoundingClientRect();
      const liRect = li.getBoundingClientRect();
      const fullyVisible = liRect.top >= listRect.top && liRect.bottom <= listRect.bottom;
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
        timer = setTimeout(finish, 90); // 滚动停止 90ms 后认为到位
      };
      listEl.addEventListener('scroll', onScroll);
      li.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      timer = setTimeout(finish, 800); // 兜底
    });
  });
}

function renderStatic(tasks: TodoTask[]) {
  rows.value = tasks.map((t) => ({
    key: `${t.index}:${t.title}`,
    title: t.title,
    done: isDoneStatus(t.status),
    entering: false,
    leaving: false,
    justDone: false,
    enterDelay: 0,
    leaveDelay: 0
  }));
}

watch(
  todoList,
  async (newVal, oldVal) => {
    const myGen = ++gen;
    const newTasks = tasksOf(newVal);
    const oldTasks = tasksOf(oldVal);
    // live=true 来自任务期实时事件（播动画）；false 来自加载/切换/fetch（静态呈现）
    const live = fileStore.todoListLive;

    // 有 → 空：移出后隐藏窗口（加载来源直接消失）
    if (!newTasks.length) {
      if (rows.value.length && live) {
        busy.value = true;
        await playLeave();
        if (myGen !== gen) {
          return;
        }
        busy.value = false;
      }
      rows.value = [];
      visible.value = false;
      return;
    }

    // 空 → 有：实时事件播窗口 + 行进入动画；加载回填静态呈现
    if (!rows.value.length) {
      visible.value = true;
      if (live) {
        windowEntering.value = true;
        setTimeout(() => {
          windowEntering.value = false;
        }, 320);
        // 窗口进入动画 0.3s 播完后条目再逐个进入（顺序播放）
        renderEntering(newTasks, 300);
      } else {
        renderStatic(newTasks);
      }
      return;
    }

    // 有 → 有：title 序列相同 = 状态更新；不同 = 整表替换
    if (signature(oldTasks) === signature(newTasks)) {
      // 加载来源：静态同步状态，不播划线动画
      if (!live) {
        newTasks.forEach((t, i) => {
          const row = rows.value[i];
          if (row) {
            row.done = isDoneStatus(t.status);
          }
        });
        return;
      }
      for (let i = 0; i < newTasks.length; i += 1) {
        const row = rows.value[i];
        if (!row) {
          continue;
        }
        const done = isDoneStatus(newTasks[i].status);
        if (row.done === done) {
          continue;
        }
        // 等待进行中的整表动画结束，避免状态互相覆盖
        while (busy.value) {
          await wait(50);
          if (myGen !== gen) {
            return;
          }
        }
        if (done) {
          await scrollRowIntoView(i); // 区域外先滚动露出
          if (myGen !== gen) {
            return;
          }
        }
        row.done = done;
        if (done) {
          row.justDone = true;
          const target = row;
          setTimeout(() => {
            target.justDone = false;
          }, 400);
        }
      }
      return;
    }

    // 整表替换：实时事件播移出+进入；加载来源直接静态替换
    if (!live) {
      renderStatic(newTasks);
      return;
    }
    busy.value = true;
    await playLeave();
    if (myGen !== gen) {
      return;
    }
    renderEntering(newTasks);
    busy.value = false;
  },
  // immediate 必须存在：窗口随移动端↔桌面端切换卸载重挂时，
  // store 中 todoList 数据未变化不会触发 watch，需要立即按现有数据初始化，
  // 否则窗口会被“吞掉”（与 RunnerWindow/FileWindow 的 immediate: true 对齐）
  { flush: 'sync', immediate: true }
);
</script>
