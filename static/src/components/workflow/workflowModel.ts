/**
 * 工作流数据模型。
 *
 * 五种节点（串行边界：无并行语义）：
 * - start（胶囊）：入口。右 1 出、无入桩；恰好 1 个（校验强制）。可增删拖动。
 * - end（胶囊）：终点。左 n 入、无出桩；至少 1 个。可增删拖动。
 * - stage（矩形）：AI 执行阶段。左 1 入、右 1 出——严格线性，next 为单值；
 *   想分叉必须显式经过分支节点。
 * - review（菱形）：审核智能体把关。左 1 入、右 1 出（通过·蓝线）、
 *   上/下各 1 个驳回出口（同一 rejectTo，方向按目标相对位置自动选）。
 *   驳回路由必须显式连接，否则校验不通过。
 * - branch（虚线矩形）：分线/并线器。左 n 入、右 n 出（next 路由数组，
 *   每条带 condition 条件描述，多条出线时必填 = AI 决策依据）。
 *
 * 上下连接点（stage/branch/review 标配）：上是驳回的入、下也是驳回的入——
 * 上下功能相同，两个方向只是美观（按源/目标相对位置自动选向）。
 *
 * 入侧约定：一个入桩允许收多条线；出侧严格限量。
 *
 * 所有路径显式化：没有隐式终点，阶段/审核的出线必须连到后续节点或结束
 * 节点（next 为 null 校验不通过）；开始/结束是真实节点，无任何派生边。
 *
 * 画布拓扑（nodes + next/rejectTo 路由）是结构约束，节点内行为
 * （goal/instructions/prompt/condition）由自然语言描述。position 仅为画布坐标。
 * 桩位分配是纯渲染层规则（workflowToFlow 时按对端方位分配 handle id），
 * 数据模型不存桩位——连线语义由拉出的连桩类型决定。
 */

import type { Edge, Node } from '@vue-flow/core';
import { MarkerType } from '@vue-flow/core';
import dagre from '@dagrejs/dagre';
import { t } from '@/locales';

// ---------------------------------------------------------------- 类型

export interface WorkflowStartDef {
  kind: 'start';
  id: string;
  name: string;
  /** 右 1 出（白线）；null 校验不通过（开始节点必须连出） */
  next: string | null;
  position?: { x: number; y: number };
}

export interface WorkflowEndDef {
  kind: 'end';
  id: string;
  name: string;
  /** 画布坐标（仅视觉） */
  position?: { x: number; y: number };
}

export interface WorkflowStageDef {
  kind: 'stage';
  id: string;
  name: string;
  /** 本阶段要达成什么（注入提示词的核心） */
  goal: string;
  /** 本阶段的具体工作方式（自然语言） */
  instructions: string;
  /** 唯一后续节点 id（白线，右侧出）；null 校验不通过（必须显式连到后续或结束） */
  next: string | null;
  position?: { x: number; y: number };
}

export interface WorkflowReviewDef {
  kind: 'review';
  id: string;
  name: string;
  /** 审核关注点（注入审核智能体提示词） */
  prompt: string;
  /** 通过去向（蓝线）；null 校验不通过（必须显式连到后续或结束） */
  next: string | null;
  /** 驳回去向（红线，上/下出、目标上/下进）；null 校验不通过 */
  rejectTo: string | null;
  /** 连续驳回上限，超限整个工作流失败终止 */
  maxRejects: number;
  position?: { x: number; y: number };
}

/** 分支节点的一条出线：目标 + 走这条路的条件（自然语言，AI 决策依据） */
export interface WorkflowBranchRoute {
  target: string;
  /** 走这条出线的条件描述；多条出线时必填（校验提示），单出线（并线器）可空 */
  condition: string;
}

export interface WorkflowBranchDef {
  kind: 'branch';
  id: string;
  name: string;
  /** 右侧出候选（白线，n 条 = AI 决策分支；1 条 = 并线器） */
  next: WorkflowBranchRoute[];
  position?: { x: number; y: number };
}

export type WorkflowNodeDef =
  | WorkflowStartDef
  | WorkflowEndDef
  | WorkflowStageDef
  | WorkflowReviewDef
  | WorkflowBranchDef;

/** 有出线的节点（start/stage/review/branch） */
export type WorkflowSourceNodeDef = Exclude<WorkflowNodeDef, WorkflowEndDef>;

export interface WorkflowDef {
  name: string;
  description: string;
  /** 审核智能体取证能力：active = 可调用只读 run_command */
  reviewMode: 'readonly' | 'active';
  /** 单节点最大轮数，防死循环 */
  maxStageRounds: number;
  /** 整体结束方式（自然语言） */
  endConditions: string;
  /** 全局正文：工作方式 / 验证方式 / 结束方式 */
  body: string;
  source: 'builtin' | 'user';
  updatedAt: string;
  nodes: WorkflowNodeDef[];
}

export interface WorkflowIssue {
  level: 'error' | 'warning';
  message: string;
  /** 关联节点 id（用于画布标红） */
  nodeId?: string;
}

// ---------------------------------------------------------------- 新建模板

/** 空白工作流：默认一个开始 + 一个结束并直连，用户在中间插入节点 */
export function createEmptyWorkflow(name: string): WorkflowDef {
  return {
    name,
    description: '',
    reviewMode: 'active',
    maxStageRounds: 20,
    endConditions: '',
    body: t('workflow.defaultBodyTemplate'),
    source: 'user',
    updatedAt: '',
    nodes: [
      { kind: 'start', id: 'start-1', name: t('workflow.defaultStartNode'), next: 'end-1', position: { x: 60, y: 220 } },
      { kind: 'end', id: 'end-1', name: t('workflow.defaultEndNode'), position: { x: 480, y: 220 } },
    ],
  };
}

// ---------------------------------------------------------------- 节点工具

export function findNode(def: WorkflowDef, id: string): WorkflowNodeDef | undefined {
  return def.nodes.find((n) => n.id === id);
}

export function nodeExists(def: WorkflowDef, id: string): boolean {
  return def.nodes.some((n) => n.id === id);
}

/** 节点垂直方位（桩位排序用） */
function nodeY(def: WorkflowDef, id: string): number {
  return findNode(def, id)?.position?.y ?? Number.MAX_SAFE_INTEGER;
}

// ---------------------------------------------------------------- nodes → flow 转换

/** 节点近似尺寸（与节点组件一致；仅用于布局间距计算） */
const NODE_WIDTH = 210;
const NODE_HEIGHT = 118;
// 菱形审核节点更小
const REVIEW_WIDTH = 170;
const REVIEW_HEIGHT = 96;
// 分支节点窄；高度随桩数自适应（布局时估算）
const BRANCH_WIDTH = 150;
const BRANCH_HEIGHT_MIN = 80;
// 开始/结束胶囊
const BOUNDARY_WIDTH = 110;
const BOUNDARY_HEIGHT = 40;

const STROKE_FORWARD = 'var(--text-secondary)';
const STROKE_PASS = 'var(--state-info)';
const STROKE_REJECT = 'var(--state-danger)';

/**
 * 边语义（Visio 式）：
 * - 白线：阶段的下一步 / 分支的出线 / 开始的入口线（右出左进）
 * - 蓝线：审核通过（菱形右出左进）
 * - 红线：审核驳回（菱形上/下出、目标上/下进；目标无上下入桩时回落左入）
 * 不指定 type：Vue Flow 默认 default 即贝塞尔曲线，禁止 smoothstep 直角弯。
 *
 * 桩位分配：分支节点第 i 条右出线占出桩 `out-${i}`，第 j 条入线占入桩
 * `in-${j}`——按对端节点垂直方位排序分配（上方的对端占靠上的桩，线不
 * 交叉、方位一致）；阶段/审核/开始出入桩固定；结束左入桩同分支规则。
 */
export function workflowToFlow(def: WorkflowDef, issueNodeIds: Set<string>): { nodes: Node[]; edges: Edge[] } {
  // 入边收集（target -> {source, isReject} 列表），用于分支/结束节点入桩分配
  interface IncomingEdge {
    source: string;
    isReject: boolean;
  }
  const incomingOf = new Map<string, IncomingEdge[]>();
  const recordIncoming = (target: string, source: string, isReject = false) => {
    if (!incomingOf.has(target)) incomingOf.set(target, []);
    (incomingOf.get(target) as IncomingEdge[]).push({ source, isReject });
  };
  for (const n of def.nodes) {
    if (n.kind === 'end') continue;
    if (n.kind === 'branch') {
      for (const r of n.next) recordIncoming(r.target, n.id);
    } else {
      if (n.next) recordIncoming(n.next, n.id);
      if (n.kind === 'review' && n.rejectTo) recordIncoming(n.rejectTo, n.id, true);
    }
  }

  const yOf = (id: string): number => nodeY(def, id);

  // 左入桩只分配给走左入的前进线（白/蓝）；驳回红线进 stage/branch 时走
  // 上/下入桩不占左桩，仅当目标无上下入桩（start/end/review）时回落占左桩。
  const leftIncomingOf = (target: string): string[] => {
    const targetNode = findNode(def, target);
    const hasVerticalIn = targetNode?.kind === 'stage' || targetNode?.kind === 'branch';
    const list = (incomingOf.get(target) ?? []).filter((e) => !e.isReject || !hasVerticalIn);
    return list.map((e) => e.source).sort((a, b) => yOf(a) - yOf(b));
  };

  const inIndexOf = (target: string, source: string): number => {
    const list = leftIncomingOf(target);
    const idx = list.indexOf(source);
    return idx >= 0 ? idx : 0;
  };

  /**
   * 目标入桩：branch/end 按入边方位序分配独立桩；stage/review 固定单桩 'in'。
   * 驳回红线进 stage/branch 按相对位置选向（上进或下进，纯美观）。
   */
  const targetHandleOf = (target: string, source: string, isRejectEdge: boolean, rejectFromAbove = true): string => {
    const targetNode = findNode(def, target);
    if (isRejectEdge && targetNode && (targetNode.kind === 'stage' || targetNode.kind === 'branch')) {
      return rejectFromAbove ? 'in-top' : 'in-bottom';
    }
    if (targetNode?.kind === 'branch' || targetNode?.kind === 'end') {
      return `in-${inIndexOf(target, source)}`;
    }
    return 'in';
  };

  /** 驳回红线方向：目标在审核上方 → 上出上进；下方 → 下出下进（美观自动选向） */
  const rejectGoesUp = (source: WorkflowNodeDef, targetId: string): boolean => {
    const targetNode = findNode(def, targetId);
    if (!source.position || !targetNode?.position) return true;
    return targetNode.position.y <= source.position.y;
  };

  const nodes: Node[] = def.nodes.map((n) => {
    const isBoundary = n.kind === 'start' || n.kind === 'end';
    return {
      id: n.id,
      type: isBoundary ? 'boundary' : n.kind,
      position: n.position ?? { x: 0, y: 0 },
      data: {
        node: n,
        hasIssue: issueNodeIds.has(n.id),
        // 左入桩已占用数量（只含前进线，不含上/下入的红线）；分支/结束组件会加 1 个常驻空桩渲染
        inCount: leftIncomingOf(n.id).length,
        outCount: n.kind === 'branch' ? n.next.length : 1,
      },
    };
  });
  const edges: Edge[] = [];

  for (const n of def.nodes) {
    if (n.kind === 'end') continue;
    if (n.kind === 'branch') {
      const sortedRoutes = [...n.next].sort((a, b) => yOf(a.target) - yOf(b.target));
      sortedRoutes.forEach((route, i) => {
        // 条件文字作为边标签，超长截断
        const label = route.condition.length > 14 ? `${route.condition.slice(0, 14)}…` : route.condition;
        edges.push({
          id: `${n.id}->${route.target}`,
          source: n.id,
          target: route.target,
          sourceHandle: `out-${i}`,
          targetHandle: targetHandleOf(route.target, n.id, false),
          ...(label ? { label } : {}),
          markerEnd: { type: MarkerType.ArrowClosed, color: STROKE_FORWARD },
        });
      });
      continue;
    }
    if (n.next) {
      // start/stage 白线、review 蓝线
      edges.push({
        id: `${n.id}->${n.next}`,
        source: n.id,
        target: n.next,
        sourceHandle: 'out-0',
        targetHandle: targetHandleOf(n.next, n.id, false),
        ...(n.kind === 'review' ? { class: 'wf-edge-approved' } : {}),
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: n.kind === 'review' ? STROKE_PASS : STROKE_FORWARD,
        },
      });
    }
    if (n.kind === 'review' && n.rejectTo) {
      const up = rejectGoesUp(n, n.rejectTo);
      edges.push({
        id: `reject:${n.id}->${n.rejectTo}`,
        source: n.id,
        target: n.rejectTo,
        sourceHandle: up ? 'reject-out' : 'reject-out-b',
        targetHandle: targetHandleOf(n.rejectTo, n.id, true, up),
        class: 'wf-edge-back',
        markerEnd: { type: MarkerType.ArrowClosed, color: STROKE_REJECT },
      });
    }
  }
  return { nodes, edges };
}

// ---------------------------------------------------------------- 结构操作

let idSeq = 0;

function genNodeId(def: WorkflowDef, prefix: string): string {
  idSeq += 1;
  let n = def.nodes.length + idSeq;
  let id = `${prefix}-${n}`;
  while (nodeExists(def, id)) {
    n += 1;
    id = `${prefix}-${n}`;
  }
  return id;
}

export function addStage(def: WorkflowDef, position: { x: number; y: number }): WorkflowStageDef {
  const stage: WorkflowStageDef = {
    kind: 'stage',
    id: genNodeId(def, 'stage'),
    name: t('workflow.defaultStageName', { n: def.nodes.filter((x) => x.kind === 'stage').length + 1 }),
    goal: '',
    instructions: '',
    next: null,
    position,
  };
  def.nodes.push(stage);
  return stage;
}

export function addReview(def: WorkflowDef, position: { x: number; y: number }): WorkflowReviewDef {
  const review: WorkflowReviewDef = {
    kind: 'review',
    id: genNodeId(def, 'review'),
    name: t('workflow.defaultReviewName', { n: def.nodes.filter((x) => x.kind === 'review').length + 1 }),
    prompt: '',
    next: null,
    rejectTo: null,
    maxRejects: 3,
    position,
  };
  def.nodes.push(review);
  return review;
}

export function addBranch(def: WorkflowDef, position: { x: number; y: number }): WorkflowBranchDef {
  const branch: WorkflowBranchDef = {
    kind: 'branch',
    id: genNodeId(def, 'branch'),
    name: t('workflow.defaultBranchName', { n: def.nodes.filter((x) => x.kind === 'branch').length + 1 }),
    next: [],
    position,
  };
  def.nodes.push(branch);
  return branch;
}

export function addStart(def: WorkflowDef, position: { x: number; y: number }): WorkflowStartDef {
  const start: WorkflowStartDef = {
    kind: 'start',
    id: genNodeId(def, 'start'),
    name: t('workflow.defaultStartNode'),
    next: null,
    position,
  };
  def.nodes.push(start);
  return start;
}

export function addEnd(def: WorkflowDef, position: { x: number; y: number }): WorkflowEndDef {
  const end: WorkflowEndDef = {
    kind: 'end',
    id: genNodeId(def, 'end'),
    name: t('workflow.defaultEndNode'),
    position,
  };
  def.nodes.push(end);
  return end;
}

export function removeNode(def: WorkflowDef, nodeId: string): void {
  def.nodes = def.nodes.filter((n) => n.id !== nodeId);
  for (const n of def.nodes) {
    if (n.kind === 'end') continue;
    if (n.kind === 'branch') {
      n.next = n.next.filter((r) => r.target !== nodeId);
    } else {
      if (n.next === nodeId) n.next = null;
      if (n.kind === 'review' && n.rejectTo === nodeId) n.rejectTo = null;
    }
  }
}

/**
 * 前进/通过/开始/分支出线连线。返回错误信息，null 为成功。
 * - start/stage/review 源：单值替换语义（已有出线时被替换，调用方可先读旧值提示）
 * - branch 源：累加语义（重复返回错误）
 */
export function connectNext(def: WorkflowDef, sourceId: string, targetId: string): string | null {
  if (sourceId === targetId) return t('workflow.connectSelf');
  const source = findNode(def, sourceId);
  if (!source) return t('workflow.sourceMissing');
  if (source.kind === 'end') return t('workflow.endHasNoOut');
  if (!nodeExists(def, targetId)) return t('workflow.targetMissing');
  if (findNode(def, targetId)?.kind === 'start') return t('workflow.connectToStart');
  if (source.kind === 'branch') {
    if (source.next.some((r) => r.target === targetId)) return t('workflow.routeExists');
    source.next.push({ target: targetId, condition: '' });
    return null;
  }
  if (source.kind === 'review' && source.rejectTo === targetId) {
    return t('workflow.targetIsRejectRoute');
  }
  source.next = targetId;
  return null;
}

/**
 * 分支已占用出桩拖新线 = 修改该桩的流向（替换该桩当前目标）。
 * slotIndex 是桩位序号（按对端方位排序，与画布渲染的 out-i 一致）。
 * 返回错误信息，null 为成功。
 */
export function replaceBranchTarget(def: WorkflowDef, sourceId: string, slotIndex: number, newTarget: string): string | null {
  const source = findNode(def, sourceId);
  if (!source) return t('workflow.sourceMissing');
  if (source.kind !== 'branch') return t('workflow.onlyBranchHasOuts');
  if (!nodeExists(def, newTarget)) return t('workflow.targetMissing');
  if (findNode(def, newTarget)?.kind === 'start') return t('workflow.connectToStart');
  const sorted = [...source.next].sort((a, b) => nodeY(def, a.target) - nodeY(def, b.target));
  const oldRoute = sorted[slotIndex];
  if (oldRoute === undefined) return t('workflow.emptySlot');
  if (oldRoute.target === newTarget) return null; // 幂等
  if (source.next.some((r) => r.target === newTarget)) return t('workflow.targetAlreadyOut');
  // 保序替换（条件描述保留）：数组序不变，桩位在下次渲染时按新目标方位重排
  const route = source.next.find((r) => r.target === oldRoute.target);
  if (route) route.target = newTarget;
  return null;
}

/** 驳回连线（菱形上/下连桩拉出，语义相同仅方向不同）。单值替换语义。 */
export function connectRejectTo(def: WorkflowDef, sourceId: string, targetId: string): string | null {
  if (sourceId === targetId) return t('workflow.rejectToSelf');
  const source = findNode(def, sourceId);
  if (!source) return t('workflow.sourceMissing');
  if (source.kind !== 'review') return t('workflow.onlyReviewHasReject');
  if (!nodeExists(def, targetId)) return t('workflow.targetMissing');
  if (findNode(def, targetId)?.kind === 'start') return t('workflow.rejectToStart');
  if (source.next === targetId) return t('workflow.targetIsPassRoute');
  source.rejectTo = targetId;
  return null;
}

export function disconnectNext(def: WorkflowDef, sourceId: string, targetId: string): void {
  const source = findNode(def, sourceId);
  if (!source || source.kind === 'end') return;
  if (source.kind === 'branch') {
    source.next = source.next.filter((r) => r.target !== targetId);
  } else if (source.next === targetId) {
    source.next = null;
  }
}

export function disconnectRejectTo(def: WorkflowDef, sourceId: string): void {
  const source = findNode(def, sourceId);
  if (!source || source.kind !== 'review') return;
  source.rejectTo = null;
}

// ---------------------------------------------------------------- 校验

/** 校验消息用的节点显示名 */
function nodeNameOfLocal(def: WorkflowDef, id: string): string {
  return findNode(def, id)?.name ?? id;
}

export function validateWorkflow(def: WorkflowDef): WorkflowIssue[] {
  const issues: WorkflowIssue[] = [];
  if (!def.name.trim()) {
    issues.push({ level: 'error', message: t('workflow.missingName') });
  }
  if (!def.description.trim()) {
    issues.push({ level: 'warning', message: t('workflow.missingDescription') });
  }
  if (def.nodes.length === 0) {
    issues.push({ level: 'error', message: t('workflow.needStartEnd') });
    return issues;
  }

  const targetValid = (target: string) => nodeExists(def, target);
  const isStart = (id: string) => findNode(def, id)?.kind === 'start';

  const starts = def.nodes.filter((n) => n.kind === 'start');
  const ends = def.nodes.filter((n) => n.kind === 'end');
  if (starts.length === 0) {
    issues.push({ level: 'error', message: t('workflow.missingStart') });
  } else if (starts.length > 1) {
    issues.push({ level: 'error', message: t('workflow.multipleStarts', { n: starts.length }), nodeId: starts[1].id });
  }
  if (ends.length === 0) {
    issues.push({ level: 'error', message: t('workflow.missingEnd') });
  }

  const ids = new Set<string>();
  for (const n of def.nodes) {
    if (ids.has(n.id)) {
      issues.push({ level: 'error', message: t('workflow.dupNodeId', { id: n.id }), nodeId: n.id });
    }
    ids.add(n.id);
    if (!n.name.trim()) {
      issues.push({ level: 'error', message: t('workflow.nodeMissingName', { id: n.id }), nodeId: n.id });
    }
    switch (n.kind) {
      case 'start':
        if (n.next === null) {
          issues.push({ level: 'error', message: t('workflow.startNotConnected'), nodeId: n.id });
        } else if (!targetValid(n.next)) {
          issues.push({ level: 'error', message: t('workflow.startBadTarget', { target: n.next }), nodeId: n.id });
        }
        break;
      case 'end':
        break;
      case 'stage':
        if (!n.goal.trim()) {
          issues.push({ level: 'warning', message: t('workflow.stageMissingGoal', { name: n.name || n.id }), nodeId: n.id });
        }
        if (n.next === null) {
          issues.push({ level: 'error', message: t('workflow.stageNotConnected', { name: n.name || n.id }), nodeId: n.id });
        } else if (!targetValid(n.next)) {
          issues.push({ level: 'error', message: t('workflow.stageBadTarget', { name: n.name, target: n.next }), nodeId: n.id });
        } else if (isStart(n.next)) {
          issues.push({ level: 'error', message: t('workflow.stageToStart', { name: n.name }), nodeId: n.id });
        }
        break;
      case 'review':
        if (!n.prompt.trim()) {
          issues.push({ level: 'warning', message: t('workflow.reviewMissingFocus', { name: n.name || n.id }), nodeId: n.id });
        }
        if (n.next === null) {
          issues.push({ level: 'error', message: t('workflow.reviewNoPassRoute', { name: n.name || n.id }), nodeId: n.id });
        } else if (!targetValid(n.next)) {
          issues.push({ level: 'error', message: t('workflow.reviewBadTarget', { name: n.name, target: n.next }), nodeId: n.id });
        }
        if (n.rejectTo === null) {
          issues.push({ level: 'error', message: t('workflow.reviewNoRejectRoute', { name: n.name || n.id }), nodeId: n.id });
        } else if (!targetValid(n.rejectTo)) {
          issues.push({ level: 'error', message: t('workflow.reviewBadRejectTarget', { name: n.name, target: n.rejectTo }), nodeId: n.id });
        } else if (isStart(n.rejectTo)) {
          issues.push({ level: 'error', message: t('workflow.reviewRejectToStart', { name: n.name }), nodeId: n.id });
        }
        if (!Number.isFinite(n.maxRejects) || n.maxRejects < 1) {
          issues.push({ level: 'error', message: t('workflow.reviewBadMaxRejects', { name: n.name || n.id }), nodeId: n.id });
        }
        break;
      case 'branch':
        if (n.next.length === 0) {
          issues.push({ level: 'warning', message: t('workflow.branchNoOutsNamed', { name: n.name || n.id }), nodeId: n.id });
        }
        for (const route of n.next) {
          if (!targetValid(route.target)) {
            issues.push({ level: 'error', message: t('workflow.branchBadTarget', { name: n.name, target: route.target }), nodeId: n.id });
          } else if (isStart(route.target)) {
            issues.push({ level: 'error', message: t('workflow.branchToStart', { name: n.name }), nodeId: n.id });
          }
        }
        // 多条出线 = AI 决策点，每条都必须写条件，否则 AI 无法选择
        if (n.next.length >= 2) {
          for (const route of n.next) {
            if (!route.condition.trim()) {
              issues.push({
                level: 'warning',
                message: t('workflow.branchRouteMissingCondition', { name: n.name, target: nodeNameOfLocal(def, route.target) }),
                nodeId: n.id,
              });
            }
          }
        }
        break;
    }
  }

  // 从开始节点出发的可达性（驳回路由也算可达路径）
  const start = starts[0];
  if (start && start.kind === 'start' && start.next && targetValid(start.next)) {
    const reachable = new Set<string>([start.id]);
    const queue = [start.next];
    while (queue.length) {
      const cur = queue.shift() as string;
      if (reachable.has(cur)) continue;
      reachable.add(cur);
      const curNode = findNode(def, cur);
      if (!curNode || curNode.kind === 'end') continue;
      const outs: string[] =
        curNode.kind === 'branch'
          ? curNode.next.map((r) => r.target)
          : [curNode.next, curNode.kind === 'review' ? curNode.rejectTo : null].filter((t): t is string => Boolean(t));
      for (const t of outs) {
        if (!reachable.has(t)) queue.push(t);
      }
    }
    for (const n of def.nodes) {
      if (!reachable.has(n.id)) {
        issues.push({ level: 'warning', message: t('workflow.nodeUnreachable', { name: n.name }), nodeId: n.id });
      }
    }
  }
  return issues;
}

// ---------------------------------------------------------------- 自动排版（dagre 有向图布局，从左到右）

/**
 * dagre 负责分层、层内交叉最小化与回边路由通道。审核节点（菱形，带驳回
 * 路由）布局后下移一行：分层算法对循环只能把回边反转后直线分层，下移让
 * 驳回回路与主线上下并列，红线上出上进不穿主线。
 */
export function autoLayout(def: WorkflowDef): void {
  if (def.nodes.length === 0) return;
  // 分支/结束节点高度随桩数：先统计入边数（只计前进线）
  const incomingCount = new Map<string, number>();
  for (const n of def.nodes) {
    if (n.kind === 'end') continue;
    const outs: string[] =
      n.kind === 'branch' ? n.next.map((r) => r.target) : n.next ? [n.next] : [];
    for (const t of outs) {
      if (nodeExists(def, t)) incomingCount.set(t, (incomingCount.get(t) ?? 0) + 1);
    }
  }
  const sizeOf = (n: WorkflowNodeDef): { width: number; height: number } => {
    switch (n.kind) {
      case 'start':
      case 'end':
        return { width: BOUNDARY_WIDTH, height: BOUNDARY_HEIGHT };
      case 'review':
        return { width: REVIEW_WIDTH, height: REVIEW_HEIGHT };
      case 'branch': {
        const rows = Math.max(incomingCount.get(n.id) ?? 1, n.next.length, 2);
        return { width: BRANCH_WIDTH, height: Math.max(BRANCH_HEIGHT_MIN, rows * 24 + 40) };
      }
      default:
        return { width: NODE_WIDTH, height: NODE_HEIGHT };
    }
  };

  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', nodesep: 70, ranksep: 130, marginx: 60, marginy: 60 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of def.nodes) {
    g.setNode(n.id, sizeOf(n));
  }
  for (const n of def.nodes) {
    if (n.kind === 'end') continue;
    const outs: string[] =
      n.kind === 'branch'
        ? n.next.map((r) => r.target)
        : [n.next, n.kind === 'review' ? n.rejectTo : null].filter((t): t is string => Boolean(t));
    for (const t of outs) {
      if (nodeExists(def, t)) g.setEdge(n.id, t);
    }
  }
  dagre.layout(g);
  for (const n of def.nodes) {
    const info = g.node(n.id);
    const { width, height } = sizeOf(n);
    // dagre 输出节点中心坐标，转为 Vue Flow 左上角坐标
    n.position = { x: Math.round(info.x - width / 2), y: Math.round(info.y - height / 2) };
  }
  // 审核节点下移一行，驳回红线（上出上进）与主线上下并列
  for (const n of def.nodes) {
    if (n.kind === 'review' && n.rejectTo !== null && n.position) {
      n.position = { x: n.position.x, y: n.position.y + 170 };
    }
  }
}
