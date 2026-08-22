<template>
  <section v-if="rows.length" class="qd-window" :class="{ 'qd-window-enter': windowEntering }">
    <header class="qd-window__header">
      <svg v-if="kind === 'agent'" class="qd-window__icon" viewBox="0 0 16 16" fill="none">
        <circle cx="8" cy="5.5" r="2.5" stroke="currentColor" stroke-width="1.4" />
        <path
          d="M3 13c.7-2.6 2.7-4 5-4s4.3 1.4 5 4"
          stroke="currentColor"
          stroke-width="1.4"
          stroke-linecap="round"
        />
      </svg>
      <svg v-else class="qd-window__icon" viewBox="0 0 16 16" fill="none">
        <path
          d="M2.5 4.5 6 8l-3.5 3.5"
          stroke="currentColor"
          stroke-width="1.4"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
        <path d="M8 12h5.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
      </svg>
      <span class="qd-window__title">{{ kind === 'agent' ? '子智能体' : '后台指令' }}</span>
      <span class="qd-window__counter">{{ rows.length }}</span>
    </header>
    <ul ref="listRef" class="qd-list">
      <li
        v-for="row in rows"
        :key="row.id"
        class="qd-run-item"
        :data-kind="kind"
        :class="[
          `is-${row.state}`,
          {
            'is-active': isActive(row),
            'qd-row-enter': row.entering
          }
        ]"
        :style="row.pendingEnter ? { opacity: '0' } : undefined"
        @click="openRow(row)"
      >
        <span class="qd-run-status"></span>
        <span class="qd-run-name" :title="row.name">{{ row.name }}</span>
        <button class="qd-row-menu-btn" title="更多" @click.stop="openMenu($event, row)">
          <svg viewBox="0 0 16 16">
            <circle cx="3.5" cy="8" r="1.3" fill="currentColor" />
            <circle cx="8" cy="8" r="1.3" fill="currentColor" />
            <circle cx="12.5" cy="8" r="1.3" fill="currentColor" />
          </svg>
        </button>
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useSubAgentStore } from '@/stores/subAgent';
import { useBackgroundCommandStore } from '@/stores/backgroundCommand';
import { useQuickDockStore } from '@/stores/quickDock';

/**
 * 子智能体 / 后台指令窗口（两者同构）
 * - 行结构：[●状态点] 名称 [⋯]；运行中状态点呼吸，终态变灰
 * - 新条目：先隐身插入，若在可视区域外先平滑滚动露出，再播进入动画
 * - 点击行 → 左侧详情面板；⋯ → 菜单（强制关闭）
 */

/** 行状态：running=运行中（蓝） / idle=空闲（白） / done=完成（绿） / ended=失败·超时·终止（红）
 *  配色对齐旧工作区面板的 .sub-agent-status 文字色。 */
type RowState = 'running' | 'idle' | 'done' | 'ended';

function agentStateOf(status: string): RowState {
  if (status === 'idle') return 'idle';
  if (status === 'completed') return 'done';
  if (status === 'failed' || status === 'timeout' || status === 'terminated' || status === 'cancelled') {
    return 'ended';
  }
  return 'running';
}

function cmdStateOf(status: string): RowState {
  if (status === 'completed') return 'done';
  if (status === 'failed' || status === 'timeout' || status === 'cancelled') {
    return 'ended';
  }
  return 'running';
}

interface Row {
  id: string;
  name: string;
  state: RowState;
  entering: boolean;
  pendingEnter: boolean;
}

const props = defineProps<{ kind: 'agent' | 'cmd' }>();

const subAgentStore = useSubAgentStore();
const bgStore = useBackgroundCommandStore();
const quickDock = useQuickDockStore();
const { detail } = storeToRefs(quickDock);

const rows = ref<Row[]>([]);
const windowEntering = ref(false);
const listRef = ref<HTMLElement | null>(null);
let windowEnterShown = false;

interface SourceItem {
  id: string;
  name: string;
  state: RowState;
}

const sourceItems = computed<SourceItem[]>(() => {
  if (props.kind === 'agent') {
    return subAgentStore.subAgents
      .map((a) => {
        const id = (a.task_id || '').toString();
        if (!id) {
          return null;
        }
        const status = (a.status || '').toString().toLowerCase();
        return {
          id,
          name: a.display_name || a.summary || `子智能体 ${a.agent_id ?? id}`,
          state: agentStateOf(status)
        };
      })
      .filter((x): x is SourceItem => x !== null);
  }
  return bgStore.commands
    .map((c) => {
      const id = (c.command_id || '').toString();
      if (!id) {
        return null;
      }
      const status = (c.status || '').toString().toLowerCase();
      return {
        id,
        name: c.command || id,
        state: cmdStateOf(status)
      };
    })
    .filter((x): x is SourceItem => x !== null);
});

function isActive(row: Row) {
  return detail.value?.kind === props.kind && detail.value?.id === row.id;
}

function openRow(row: Row) {
  quickDock.closeMenu();
  quickDock.openDetail(props.kind, row.id);
}

function openMenu(e: MouseEvent, row: Row) {
  const btn = e.currentTarget as HTMLElement;
  const rect = btn.getBoundingClientRect();
  quickDock.openMenu({
    type: 'runner',
    kind: props.kind,
    key: row.id,
    left: rect.right, // alignRight：菜单右缘对齐按钮右缘
    top: rect.bottom + 6,
    alignRight: true
  });
}

/** 若目标行未完全显示，先平滑滚动到可见位置 */
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

watch(
  sourceItems,
  (list) => {
    const byId = new Map(list.map((item) => [item.id, item]));

    // 更新现有行状态；移除已消失的行
    rows.value = rows.value.filter((row) => byId.has(row.id));
    rows.value.forEach((row) => {
      const item = byId.get(row.id);
      if (item) {
        row.name = item.name;
        row.state = item.state;
      }
    });

    // 新增行：运行期新增播动画（隐身插入 → 滚动露出 → 从左进入）；
    // 加载回填 / 后端状态恢复直接静态插入。
    const existingIds = new Set(rows.value.map((row) => row.id));
    const added = list.filter((item) => !existingIds.has(item.id));
    // 数据自证：新增条目含 running 才是运行期新增；全是 idle/终态时是
    // 加载回填或后端快照恢复（如多智能体状态恢复）。新启动的条目创建时
    // 一定是 running，加载的历史实例只会是 idle/done/ended——不依赖
    // 「第一次更新」之类的时序锚点，任何空表/竞态/闪断都不会误播。
    const animate = added.some((item) => item.state === 'running');
    if (added.length) {
      const playWindow = animate && !windowEnterShown && !rows.value.length;
      if (playWindow) {
        windowEnterShown = true;
        windowEntering.value = true;
        setTimeout(() => {
          windowEntering.value = false;
        }, 320);
      }
      added.forEach((item, i) => {
        const row: Row = {
          id: item.id,
          name: item.name,
          state: item.state,
          entering: false,
          pendingEnter: animate
        };
        rows.value.push(row);
        if (animate) {
          // 与待办窗口一致：窗口动画 0.3s 先播完，条目再按 stagger 60ms
          // 逐个从左进入；窗口动画不重播时（非首次）条目直接依次进入。
          // 必须传 reactive 代理而非 raw 对象（raw 修改不触发重渲染）。
          const delay = (playWindow ? 300 : 0) + i * 60;
          void playEnter(rows.value[rows.value.length - 1], delay);
        }
      });
    }
  },
  { immediate: true }
);
</script>
