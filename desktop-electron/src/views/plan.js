// 视图收敛的纯逻辑：快照接受、URL 构造、收敛计划、LRU 与预热顺序。
// 不依赖 Electron，便于用伪对象做 Node 单测。

/** 主窗口内 conv/new 视图（含 Chat）的常驻上限（设计 D2）。 */
export const MAX_RESIDENT_VIEWS = 6;

const isResidentKind = (kind) => kind === 'conv' || kind === 'new';
export const isMainPlacement = (tab) => (tab?.placement || 'main') === 'main';

/** 快照结构校验：只检查壳层依赖的最小字段，其余原样广播。 */
export function validSnapshot(state) {
  return !!state && typeof state === 'object' && Number.isInteger(state.revision) && state.revision >= 0
    && Array.isArray(state.tabs) && state.tabs.every((tab) => tab && typeof tab.key === 'string' && tab.key)
    && (state.active_key === undefined || typeof state.active_key === 'string');
}

/** 只接受 revision 严格更大的快照（current 为 -1 表示尚未收到过）。 */
export function acceptSnapshot(currentRevision, incoming) {
  return validSnapshot(incoming) && incoming.revision > currentRevision;
}

/** 标签 → 内容视图路径。零工作区的 Agent 标签（workspace_id 为空）退回 /new。 */
export function buildViewPath(tab) {
  const withTab = (path, params = {}) => {
    const query = new URLSearchParams();
    for (const [name, value] of Object.entries(params)) if (value) query.set(name, value);
    query.set('tab', tab.key);
    const hash = path.indexOf('#');
    const [base, tail] = hash < 0 ? [path, ''] : [path.slice(0, hash), path.slice(hash)];
    return `${base}${base.includes('?') ? '&' : '?'}${query}${tail}`;
  };
  if (tab.kind === 'page') return withTab(tab.route || '/', {});
  if (tab.conversation_kind === 'chat') {
    return withTab(tab.kind === 'conv' && tab.conversation_id ? `/chat/${encodeURIComponent(stripConv(tab.conversation_id))}` : '/chat/new');
  }
  if (!tab.workspace_id) return withTab('/new');
  if (tab.kind === 'conv' && tab.conversation_id) {
    return withTab(`/${encodeURIComponent(stripConv(tab.conversation_id))}`, { workspace_id: tab.workspace_id });
  }
  return withTab('/new', { workspace_id: tab.workspace_id });
}
const stripConv = (id) => String(id).replace(/^conv_/, '');

export const buildViewUrl = (origin, tab) => `${origin}${buildViewPath(tab)}`;

/** 显示目标：快照 active_key 对应的主窗口标签；缺失时退回第一个主窗口标签。 */
export function pickActiveKey(state) {
  const main = state.tabs.filter(isMainPlacement);
  return main.find((tab) => tab.key === state.active_key)?.key || main[0]?.key || '';
}

/**
 * 把“已由壳层先行处理、后端尚未确认”的归属变化叠加到快照上，避免旧快照把视图挪回去：
 *   dock   —— 关闭独立窗口后已移回主窗口，等待 dock 命令生效（快照仍显示 detached 时按 main 处理）
 *   detach —— 拖出后已建独立窗口，等待 detach 命令生效（快照仍显示 main 时按 detached 处理）
 * 返回有效快照与“已被快照确认（或标签已消失）”的 key，调用方据此清除待确认状态。
 */
export function applyPendingPlacement(state, { dock = new Set(), detach = new Set() } = {}) {
  const satisfied = { dock: [], detach: [] };
  const present = new Set(state.tabs.map((tab) => tab.key));
  const tabs = state.tabs.map((tab) => {
    if (dock.has(tab.key)) {
      if (isMainPlacement(tab)) satisfied.dock.push(tab.key); else return { ...tab, placement: 'main' };
    }
    if (detach.has(tab.key)) {
      if (!isMainPlacement(tab)) satisfied.detach.push(tab.key); else return { ...tab, placement: 'detached' };
    }
    return tab;
  });
  for (const key of dock) if (!present.has(key)) satisfied.dock.push(key);
  for (const key of detach) if (!present.has(key)) satisfied.detach.push(key);
  return { state: { ...state, tabs }, satisfied };
}

/** 拖出/关闭当前标签后的继任者：右侧相邻的主窗口标签，无则左侧（与后端 remove_tabs 一致）；没有则空串。 */
export function successorKey(state, key) {
  const main = state.tabs.filter(isMainPlacement);
  const index = main.findIndex((tab) => tab.key === key);
  if (index < 0) return '';
  const rest = main.filter((tab) => tab.key !== key);
  return rest[Math.min(index, rest.length - 1)]?.key || '';
}

/** 乐观显示保护：短时间内不让“旧意图”的快照把可见视图切走。返回应强制显示的 key 或空串。 */
export function guardedActive(state, guard, now) {
  if (!guard) return '';
  // A matching snapshot can belong to an older click (A → B → A).
  if (!guard.requestId && (now >= guard.until || state.active_key === guard.key)) return '';
  const tab = state.tabs.find((row) => row.key === guard.key);
  return tab && isMainPlacement(tab) ? guard.key : '';
}

/**
 * 收敛计划（不执行副作用）。
 * entries: Iterable<{key, kind, placement}>（legacy 视图不传入，由调用方单独处理）。
 * opts.active：乐观显示保护强制的 active（仅当该标签在快照中属于主窗口）。
 * 返回：
 *   active         要显示的 key（可为空）
 *   create         需立即创建的 key（仅 active 缺失时）
 *   hide           需隐藏的主窗口内 key
 *   destroy        快照中已不存在的 key
 *   toDetached     placement=detached 但视图仍在主窗口 → 创建窗口并迁入
 *   createDetached placement=detached 且还没有视图（重启恢复）→ 创建视图与窗口
 *   toMain         placement=main 但视图仍在独立窗口 → 迁回主窗口（隐藏）
 */
export function planReconcile(state, entries, opts = {}) {
  const tabs = new Map(state.tabs.map((tab) => [tab.key, tab]));
  const forced = opts.active && isMainPlacement(tabs.get(opts.active)) ? opts.active : '';
  const active = forced || pickActiveKey(state);
  const plan = { active, create: [], hide: [], destroy: [], toDetached: [], createDetached: [], toMain: [] };
  const have = new Map();
  for (const entry of entries) {
    have.set(entry.key, entry);
    const tab = tabs.get(entry.key);
    if (!tab) { plan.destroy.push(entry.key); continue; }
    const main = isMainPlacement(tab);
    if (main && entry.placement === 'detached') plan.toMain.push(entry.key);
    else if (!main && entry.placement !== 'detached') plan.toDetached.push(entry.key);
    else if (main && entry.key !== active) plan.hide.push(entry.key);
  }
  if (active && !have.has(active)) plan.create.push(active);
  for (const tab of state.tabs) if (!isMainPlacement(tab) && !have.has(tab.key)) plan.createDetached.push(tab.key);
  return plan;
}

/** 常驻上限淘汰：只数主窗口内 conv/new 视图；永不淘汰 active；按 lastShown 升序淘汰。 */
export function chooseEvictions(entries, activeKey, limit = MAX_RESIDENT_VIEWS) {
  const resident = [...entries].filter((entry) => isResidentKind(entry.kind) && entry.placement !== 'detached');
  const excess = resident.length - limit;
  if (excess <= 0) return [];
  return resident
    .filter((entry) => entry.key !== activeKey)
    .sort((a, b) => a.lastShown - b.lastShown)
    .slice(0, excess)
    .map((entry) => entry.key);
}

/** 启动预热顺序：主窗口 conv/new 标签中尚无视图者，按 recency（最近查看优先；无记录按标签栏顺序）。
 *  最多补到上限（已有常驻视图计入）。 */
export function warmupOrder(state, haveKeys, recency, limit = MAX_RESIDENT_VIEWS) {
  const have = new Set(haveKeys);
  const residentTabs = state.tabs.filter((tab) => isResidentKind(tab.kind) && isMainPlacement(tab));
  const room = limit - residentTabs.filter((tab) => have.has(tab.key)).length;
  if (room <= 0) return [];
  return residentTabs
    .map((tab, index) => ({ tab, index, at: Number(recency?.[tab.key]) || 0 }))
    .filter(({ tab }) => !have.has(tab.key))
    .sort((a, b) => b.at - a.at || a.index - b.index)
    .slice(0, room)
    .map(({ tab }) => tab.key);
}
