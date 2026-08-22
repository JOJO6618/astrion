<template>
  <section
    v-if="visible"
    class="qd-window"
    :class="{ 'qd-window-enter': windowEntering, 'qd-window-exit': windowExiting }"
  >
    <header class="qd-window__header">
      <!-- 图形与 /static/icons/workflow.svg 保持一致 -->
      <svg
        class="qd-window__icon"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.8"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <circle cx="12" cy="4.6" r="2.6" />
        <path d="M12 7.2 V10.1" />
        <path d="M8.5 12.3 L12 10.1 L15.5 12.3 L12 14.5 Z" stroke-linejoin="miter" />
        <path d="M8.5 12.3 H6.1 Q4.5 12.3 4.5 13.9 V16.3" />
        <path d="M15.5 12.3 H17.9 Q19.5 12.3 19.5 13.9 V16.3" />
        <rect x="2.4" y="16.3" width="4.2" height="4.2" rx="1.1" />
        <rect x="17.4" y="16.3" width="4.2" height="4.2" rx="1.1" />
      </svg>
      <span class="qd-window__title">{{ snapshot.name }}</span>
    </header>
    <div ref="viewportRef" class="wf-viewport">
      <ul ref="listRef" class="wf-list">
        <li v-for="row in rows" :key="row.key" class="wf-row" :class="rowClasses(row)">
          <span class="wf-row__dot"></span>
          <span class="wf-row__name">{{ row.name }}</span>
          <span v-if="row.kind === 'current' && row.reviewing" class="wf-row__meta">
            <span class="wf-spinner"></span>审核中
          </span>
          <span v-else-if="row.rounds != null && row.kind !== 'next'" class="wf-row__meta">
            {{ row.rounds }} 轮
          </span>
        </li>
      </ul>
    </div>
    <div
      v-if="footnote"
      class="wf-footnote"
      :class="[`wf-footnote--${footnote.kind}`, { 'is-entering': footnoteEntering }]"
    >
      <span class="wf-footnote__dot"></span>
      <span class="wf-footnote__text">{{ footnote.text }}</span>
    </div>
  </section>
</template>

<script setup lang="ts">
import { nextTick, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useWorkflowStore } from '@/stores/workflow';
import type { WorkflowFootnote, WorkflowSnapshot } from '@/stores/workflow';

/**
 * 工作流窗口（Quick Dock 第五个窗口，仅工作流活跃时出现）
 *
 * 列表语义：history（已完成，可向上滚动查看）+ current（进行中）+ next（未来仅一步）。
 * 视口固定 3 行高，刚好显示 刚完成/当前/下一步；渲染到「下一步」为止，不可向后滚动。
 *
 * 动画（与 demo/workflow_dock_window.html 定稿一致）：
 * 1. 推进：当前行就地划线+点弹跳 → 列表平滑上滚一行 → 新「下一步」底部进入
 * 2. 审核：当前行点变橙 + spinner「审核中」副状态
 * 3. 驳回：当前行红闪两下 → 未来行淡出 → 驳回目标行「复活」（划线收回）→ 上滚到位
 * 实时事件（live=true）播动画；加载/刷新恢复（live=false）静态呈现。
 */

interface Row {
  key: string;
  name: string;
  kind: 'done' | 'current' | 'next';
  rounds: number | null;
  reviewing: boolean;
  justDone: boolean;
  reviving: boolean;
  leaving: boolean;
  entering: boolean;
  rejectFlash: boolean;
}

const SCROLL_MS = 420;
const REJECT_FLASH_MS = 660;
const LEAVE_MS = 240;
/** 整窗退出动画时长（与 CSS qd-window-out 对齐） */
const EXIT_MS = 240;
/** 工作流完成后停留展示时长，之后播退出动画收起窗口 */
const COMPLETED_LINGER_MS = 1600;

const workflowStore = useWorkflowStore();
const { snapshot } = storeToRefs(workflowStore);

const rows = ref<Row[]>([]);
const visible = ref(false);
const windowEntering = ref(false);
const windowExiting = ref(false);
const footnote = ref<WorkflowFootnote | null>(null);
const footnoteEntering = ref(false);
const viewportRef = ref<HTMLElement | null>(null);

/** 代际标记：动画流程中途来了新数据时，旧流程不再写状态（对齐 TodoWindow） */
let gen = 0;
/** 行 key 去重序号（同名阶段被驳回重访时不冲突） */
let keySeq = 0;

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function rowClasses(row: Row) {
  return {
    'is-done': row.kind === 'done',
    'is-current': row.kind === 'current',
    'is-next': row.kind === 'next',
    'is-just-done': row.justDone,
    'is-reviving': row.reviving,
    'is-leaving': row.leaving,
    'is-entering': row.entering,
    'is-reject-flash': row.rejectFlash,
    'is-reviewing': row.reviewing
  };
}

function baseRow(name: string, kind: Row['kind'], rounds: number | null): Row {
  keySeq += 1;
  return {
    key: `${kind}-${name}-${keySeq}`,
    name,
    kind,
    rounds,
    reviewing: false,
    justDone: false,
    reviving: false,
    leaving: false,
    entering: false,
    rejectFlash: false
  };
}

/** 从快照构建静态行集 */
function buildRows(snap: WorkflowSnapshot): Row[] {
  const out: Row[] = snap.history.map((h) => baseRow(h.name, 'done', h.rounds));
  if (snap.current) {
    const cur = baseRow(snap.current.name, 'current', snap.current.rounds);
    cur.reviewing = snap.reviewing;
    out.push(cur);
    if (snap.next) {
      out.push(baseRow(snap.next, 'next', null));
    }
  } else if (snap.status === 'completed') {
    out.push(baseRow('结束', 'done', null));
  }
  return out;
}

function scrollToBottom(smooth: boolean): Promise<void> {
  return nextTick(() => {
    const el = viewportRef.value;
    if (!el) {
      return;
    }
    const target = el.scrollHeight;
    if (!smooth) {
      el.scrollTop = target;
      return;
    }
    return smoothScrollTo(el, target, SCROLL_MS);
  });
}

/** 手写平滑滚动（easeOutCubic），与 demo 定稿参数一致 */
function smoothScrollTo(el: HTMLElement, target: number, ms: number): Promise<void> {
  return new Promise((resolve) => {
    const start = el.scrollTop;
    const delta = target - start;
    if (Math.abs(delta) < 1) {
      el.scrollTop = target;
      resolve();
      return;
    }
    const t0 = performance.now();
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / ms);
      const e = 1 - Math.pow(1 - p, 3);
      el.scrollTop = start + delta * e;
      if (p < 1) {
        requestAnimationFrame(tick);
      } else {
        resolve();
      }
    };
    requestAnimationFrame(tick);
  });
}

/** 全量静态渲染 + 锚底 */
function renderStatic(snap: WorkflowSnapshot) {
  rows.value = buildRows(snap);
  void scrollToBottom(false);
}

/** 变化类型判定（快照 diff） */
function detectChange(
  o: WorkflowSnapshot,
  n: WorkflowSnapshot
): 'advance' | 'reject' | 'update' | 'replace' {
  const oHist = o.history.map((h) => h.name);
  const nHist = n.history.map((h) => h.name);
  // 推进：history 末尾追加的正是旧当前
  if (nHist.length === oHist.length + 1 && nHist[nHist.length - 1] === o.current?.name) {
    return 'advance';
  }
  // 驳回：history 前缀截断（回滚）
  if (nHist.length < oHist.length && oHist.slice(0, nHist.length).every((v, i) => v === nHist[i])) {
    return 'reject';
  }
  // 就地更新：结构不变（审核态/轮数/脚注变化）
  if (
    nHist.join('|') === oHist.join('|') &&
    n.current?.name === o.current?.name &&
    n.next === o.next
  ) {
    return 'update';
  }
  return 'replace';
}

/** 推进动画：就地划线 → 上滚一行 → 新「下一步」进入 */
async function playAdvance(newSnap: WorkflowSnapshot, myGen: number) {
  // 用户上翻历史时先吸回底部
  await scrollToBottom(true);
  if (myGen !== gen) {
    return;
  }
  const curRow = rows.value.find((r) => r.kind === 'current');
  const nextRow = rows.value.find((r) => r.kind === 'next');
  // 旧当前 → 完成（划线 + 点弹跳）
  if (curRow) {
    curRow.kind = 'done';
    curRow.reviewing = false;
    curRow.justDone = true;
  }
  // 旧 next → 新当前；完成态（无新当前）时「结束」行直接落定完成
  if (nextRow) {
    nextRow.kind = newSnap.current ? 'current' : 'done';
  }
  // 追加新「下一步」
  if (newSnap.current && newSnap.next) {
    const entering = baseRow(newSnap.next, 'next', null);
    entering.entering = true;
    rows.value.push(entering);
  }
  // 上滚一行
  await scrollToBottom(true);
  if (myGen !== gen) {
    return;
  }
  // 落定（清动画标志）
  renderStatic(newSnap);
}

/** 驳回动画：红闪 → 未来行淡出 → 目标行复活 → 上滚到位 */
async function playReject(oldSnap: WorkflowSnapshot, newSnap: WorkflowSnapshot, myGen: number) {
  // 1. 当前行红闪两下
  const curRow = rows.value.find((r) => r.kind === 'current');
  if (curRow) {
    curRow.rejectFlash = true;
  }
  await wait(REJECT_FLASH_MS);
  if (myGen !== gen) {
    return;
  }

  const keepCount = newSnap.history.length; // 新 history 是旧的前缀截断
  // 2. 被吃掉的行淡出：截断区 done 行（下标 keepCount+1 起）+ 原 next 行
  //    （下标 keepCount 的旧 done 行 = 驳回目标，将复活为当前）
  const targetRow = rows.value[keepCount];
  rows.value.forEach((row, i) => {
    if (i > keepCount && row.kind !== 'current') {
      row.leaving = true;
    }
  });
  // 3. 目标行「复活」：划线从右往左收回 + 点变呼吸 + 文字恢复
  if (targetRow && targetRow.kind === 'done') {
    targetRow.kind = 'current';
    targetRow.reviving = true;
    targetRow.rounds = newSnap.current?.rounds ?? null;
  }
  // 4. 原当前行降级为「下一步」（被驳回的步骤）
  if (curRow) {
    curRow.rejectFlash = false;
    curRow.kind = 'next';
    curRow.rounds = null;
    curRow.reviewing = false;
  }
  await wait(LEAVE_MS);
  if (myGen !== gen) {
    return;
  }
  // 5. 移除淡出行，上滚到位
  rows.value = rows.value.filter((r) => !r.leaving);
  await scrollToBottom(true);
  if (myGen !== gen) {
    return;
  }
  renderStatic(newSnap);
}

/** 就地更新（结构不变：审核态/轮数） */
function applyInlineUpdate(newSnap: WorkflowSnapshot) {
  const curRow = rows.value.find((r) => r.kind === 'current');
  if (curRow && newSnap.current) {
    curRow.reviewing = newSnap.reviewing;
    curRow.rounds = newSnap.current.rounds;
  }
}

/** 整窗退出动画：淡出上移后由调用方隐藏 */
async function playWindowExit(myGen: number) {
  windowExiting.value = true;
  await wait(EXIT_MS);
  if (myGen !== gen) {
    return;
  }
  windowExiting.value = false;
}

watch(
  snapshot,
  async (newSnap, oldSnap) => {
    const myGen = ++gen;
    const live = workflowStore.live;


    // 工作流消失（停用/切换对话清空）
    if (!newSnap.active) {
      // 同对话内事件驱动消失（live=true，如 slash 停用）：与出现对称播退出动画；
      // 切换对话/刷新恢复（live=false）：瞬间校正不播；
      // completed 场景由完成流程自己播完退出动画后 reset，走到这时 rows 已空，直接隐藏。
      if (oldSnap?.active && live && rows.value.length) {
        await playWindowExit(myGen);
        if (myGen !== gen) {
          return;
        }
      }
      rows.value = [];
      footnote.value = null;
      visible.value = false;
      return;
    }
    footnote.value = newSnap.footnote;

    // 首次出现（激活/回填）
    if (!oldSnap?.active || !rows.value.length) {
      visible.value = true;
      renderStatic(newSnap);
      if (live) {
        windowEntering.value = true;
        setTimeout(() => {
          windowEntering.value = false;
        }, 320);
      }
      return;
    }

    // 加载/恢复来源：静态呈现，不播动画
    if (!live) {
      renderStatic(newSnap);
      return;
    }

    const change = detectChange(oldSnap, newSnap);
    if (change === 'advance') {
      await playAdvance(newSnap, myGen);
    } else if (change === 'reject') {
      await playReject(oldSnap, newSnap, myGen);
    } else if (change === 'update') {
      applyInlineUpdate(newSnap);
    } else {
      renderStatic(newSnap);
    }

    // 工作流完成：落定动画播完后停留展示片刻，再播退出动画收起窗口并清空状态。
    // store.reset()（live=false）使 dock 的 hasContent 联动收起；watch 消失分支幂等。
    if (newSnap.status === 'completed' && myGen === gen) {
      await wait(COMPLETED_LINGER_MS);
      if (myGen !== gen) {
        return;
      }
      await playWindowExit(myGen);
      if (myGen !== gen) {
        return;
      }
      rows.value = [];
      footnote.value = null;
      visible.value = false;
      workflowStore.reset();
    }
  },
  // immediate + flush:sync 对齐 TodoWindow：窗口随布局切换卸载重挂时立即按现有数据初始化
  { flush: 'sync', immediate: true }
);

// 退出动画（deactivate 广播 / slash 退出）：store 收到 live 的 active=false 时
// 置 exiting 并保留快照，此处播整窗退出动画后调 finishExit 真正清空。
// 动画期间 QuickDock 的 hasContent 因 exiting 保持展开，容器不会提前收起吞动画。
watch(
  () => workflowStore.exiting,
  async (exiting) => {
    if (!exiting) {
      return;
    }
    const myGen = ++gen;
    await playWindowExit(myGen);
    if (myGen !== gen) {
      // 被新快照/对话切换打断：复位退出态，交给 snapshot watch 处理
      windowExiting.value = false;
      return;
    }
    windowExiting.value = false;
    rows.value = [];
    footnote.value = null;
    visible.value = false;
    workflowStore.finishExit();
  },
  { flush: 'sync' }
);

// 脚注进入动画标志
watch(
  () => snapshot.value.footnote,
  (f, old) => {
    if (f && f !== old) {
      footnoteEntering.value = true;
      setTimeout(() => {
        footnoteEntering.value = false;
      }, 260);
    }
  }
);
</script>
