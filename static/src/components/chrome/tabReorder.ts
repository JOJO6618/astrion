// 标签拖拽排序的纯计算：几何（拖动起点测得的矩形）→ 目标位置、其余标签的让位位移、before_key。
export interface TabRect {
  key: string;
  left: number;
  width: number;
}

export interface ReorderPlan {
  /** 被拖标签在「其余标签」序列中的新位置。 */
  index: number;
  /** 其余标签的让位位移（px）。 */
  shifts: Record<string, number>;
  /** 排序后的 key 序列。 */
  order: string[];
  /** 发给后端 reorder 的 before_key（"" = 放到末尾）；位置未变时为 null。 */
  beforeKey: string | null;
}

export function planReorder(rects: TabRect[], key: string, dx: number): ReorderPlan {
  const from = rects.findIndex((rect) => rect.key === key);
  if (from < 0) return { index: 0, shifts: {}, order: rects.map((r) => r.key), beforeKey: null };
  const dragged = rects[from];
  const center = dragged.left + dragged.width / 2 + dx;
  const others = rects.filter((rect) => rect.key !== key);
  const index = others.filter((rect) => rect.left + rect.width / 2 < center).length;
  const shifts: Record<string, number> = {};
  rects.forEach((rect, position) => {
    if (position === from) return;
    if (from < index && position > from && position <= index) shifts[rect.key] = -dragged.width;
    else if (from > index && position < from && position >= index) shifts[rect.key] = dragged.width;
  });
  const order = others.map((rect) => rect.key);
  order.splice(index, 0, key);
  return { index, shifts, order, beforeKey: index === from ? null : (order[index + 1] ?? '') };
}

/** 拖动阈值：超过后才进入拖拽，避免普通点击被误判。 */
export const DRAG_THRESHOLD_PX = 5;

/** 指针离开标签条垂直方向超过该距离（px）进入「拖出」态。 */
export const TEAR_OFF_PX = 40;

/** 指针到标签条垂直范围（top..bottom）的距离；在条内为 0。 */
export function verticalOutside(y: number, top: number, bottom: number): number {
  return y < top ? top - y : y > bottom ? y - bottom : 0;
}

// ─── 向上拖出的自适应阈值：窗口顶到屏幕上沿（菜单栏下 / 最大化 / 全屏）时指针没有 40px 余量 ───
/** 向上拖出阈值的下限：避免轻微抖动就拖出。 */
export const TEAR_UP_MIN_PX = 6;
/** 阈值与可用余量之间保留的余量。 */
export const TEAR_UP_MARGIN_PX = 3;
/** 指针被屏幕上沿挡住（余量 ≤ 该值）视为“贴边”。 */
export const TEAR_EDGE_EPS_PX = 1.5;
/** 贴边规则：从按下点向上至少移动这么多才拖出（起点离上沿更近时按实际余量缩小）。 */
export const TEAR_EDGE_MOVE_PX = 12;
/** 贴边规则要求起点离上沿至少这么远，否则（本就在边缘）不触发，避免横向排序误拖出。 */
export const TEAR_EDGE_MIN_START_PX = 6;

export interface DisplayRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** 向上拖拽几何（拖拽开始时由壳层提供的显示器信息算出；未知时按旧行为）。 */
export interface DragGeometry {
  /** 标签条上沿的屏幕 y。 */
  stripTop: number;
  /** 指针按下点的屏幕 y。 */
  startY: number;
  /** 指针向上能到达的最高屏幕 y（上方叠放显示器则为叠放链的最顶；贴着屏幕上沿/菜单栏则为该显示器 bounds.y）。 */
  topLimit: number;
}

/**
 * 指针从 (x,y) 向上能到达的最高屏幕 y。以 Electron 显示器 bounds（含菜单栏）为准；
 * 正上方紧邻（允许 2px 误差）的显示器会被串起来，因此“上方有第二块屏”时余量很大。
 */
export function topLimitFor(point: { x: number; y: number }, displays: DisplayRect[]): number {
  const contains = (d: DisplayRect) =>
    point.x >= d.x && point.x < d.x + d.width && point.y >= d.y && point.y < d.y + d.height;
  const distance = (d: DisplayRect) =>
    Math.hypot(
      Math.max(d.x - point.x, 0, point.x - (d.x + d.width)),
      Math.max(d.y - point.y, 0, point.y - (d.y + d.height))
    );
  let current = displays.find(contains);
  if (!current) current = [...displays].sort((a, b) => distance(a) - distance(b))[0];
  if (!current) return Number.NEGATIVE_INFINITY;
  let top = current.y;
  for (let i = 0; i < 8; i++) {
    const above = displays.find(
      (d) =>
        d !== current &&
        point.x >= d.x &&
        point.x < d.x + d.width &&
        Math.abs(d.y + d.height - top) <= 2
    );
    if (!above) break;
    current = above;
    top = above.y;
  }
  return top;
}

/** 向上拖出阈值 = clamp(余量 − 小余量, 下限, 40)。余量未知（Infinity）时即 40。 */
export function upwardThreshold(room: number): number {
  return Math.max(TEAR_UP_MIN_PX, Math.min(TEAR_OFF_PX, room - TEAR_UP_MARGIN_PX));
}

/**
 * 是否进入拖出态。已绑定对话的标签与空白新对话标签（含 Chat）可拖出；页面标签不可。
 * 向下：沿用 40px；向上：超过自适应阈值，或指针被屏幕上沿挡住且自按下点向上已移动足够距离（贴边规则，
 * 此时指针可能根本无法离开标签条）。无几何信息时退回旧行为（纯 40px）。
 */
export function shouldTear(
  kind: string,
  move: { outside: number; above?: boolean; screenY?: number },
  geometry?: DragGeometry
): boolean {
  if (kind !== 'conv' && kind !== 'new') return false;
  if (geometry && Number.isFinite(geometry.topLimit) && Number.isFinite(move.screenY)) {
    const screenY = move.screenY as number;
    const startRoom = geometry.startY - geometry.topLimit;
    if (
      screenY - geometry.topLimit <= TEAR_EDGE_EPS_PX &&
      startRoom >= TEAR_EDGE_MIN_START_PX &&
      geometry.startY - screenY >= Math.min(TEAR_EDGE_MOVE_PX, startRoom - TEAR_EDGE_EPS_PX)
    )
      return true;
    if (move.above) {
      const room = geometry.stripTop - geometry.topLimit;
      return move.outside > upwardThreshold(room);
    }
  }
  return move.outside > TEAR_OFF_PX;
}

// ─── 拖拽状态机（纯逻辑）：pressed → reorder ⇄ torn。
// 越过拖出阈值的瞬间发 tear（壳层立即建窗并跟随指针）；torn 期间发 move；
// 指针回到标签条带内发 cancel（回到排序态，可再次排序或再次拖出）；松手在 torn 态发 drop。
export type DragMode = 'pressed' | 'reorder' | 'torn';

export interface DragState {
  key: string;
  kind: string;
  mode: DragMode;
  /** 指针相对按下点的水平位移。 */
  dx: number;
  /** 向上拖出所需的屏幕几何（可选；拖拽开始后由壳层显示器信息异步补上）。 */
  geometry?: DragGeometry;
  /** torn 后指针是否离开过标签条带（贴边拖出时指针仍在条内，需先离开再回来才算“回到标签条”而取消）。 */
  leftBand?: boolean;
  /** 最近一次指针的屏幕坐标（pointercancel / lostpointercapture 时作为 drop 位置）。 */
  lastScreen: { x: number; y: number };
}

export type DragInput =
  | {
      type: 'move';
      dx: number;
      /** 指针到标签条垂直范围的距离（条内为 0）。 */
      outside: number;
      /** 指针是否回到主窗口标签条带内（水平垂直都在条内）。 */
      inBand: boolean;
      /** 指针在标签条上方（向上离开）。 */
      above?: boolean;
      screenX: number;
      screenY: number;
      /** 抓取点相对独立窗口左上角的偏移（tear 时随消息发给壳层）。 */
      offsetX: number;
      offsetY: number;
    }
  | { type: 'end'; screenX: number; screenY: number }
  | { type: 'escape' }
  | { type: 'lost' };

export type ShellDragMessage =
  | { phase: 'tear'; screenX: number; screenY: number; offsetX: number; offsetY: number }
  | { phase: 'move'; screenX: number; screenY: number }
  | { phase: 'drop'; screenX: number; screenY: number }
  | { phase: 'cancel' };

export interface DragResult {
  state: DragState;
  /** 要发给壳层的消息（不含 key，由调用方补）。 */
  shell: ShellDragMessage[];
  /** 结束拖拽时的排序提交（reorder 态松手）。 */
  commitReorder: boolean;
  /** 手势结束（调用方清理监听与视觉状态）。 */
  finished: boolean;
}

export function startDrag(key: string, kind: string, geometry?: DragGeometry): DragState {
  return { key, kind, mode: 'pressed', dx: 0, lastScreen: { x: 0, y: 0 }, geometry };
}

export function dragStep(state: DragState, input: DragInput): DragResult {
  const done = (shell: ShellDragMessage[] = [], commitReorder = false): DragResult => ({
    state: { ...state, mode: 'pressed' },
    shell,
    commitReorder,
    finished: true
  });
  if (input.type === 'move') {
    if (state.mode === 'pressed' && Math.abs(input.dx) < DRAG_THRESHOLD_PX) {
      return { state, shell: [], commitReorder: false, finished: false };
    }
    const lastScreen = { x: input.screenX, y: input.screenY };
    const next = (
      mode: DragMode,
      shell: ShellDragMessage[] = [],
      leftBand = false
    ): DragResult => ({
      state: { ...state, mode, dx: input.dx, lastScreen, leftBand },
      shell,
      commitReorder: false,
      finished: false
    });
    if (state.mode === 'torn') {
      // 回到标签条带内：浏览器式重新吸附——撤销拖出，回到排序态。
      if (input.inBand && state.leftBand) return next('reorder', [{ phase: 'cancel' }]);
      return next(
        'torn',
        [{ phase: 'move', screenX: input.screenX, screenY: input.screenY }],
        Boolean(state.leftBand) || !input.inBand
      );
    }
    if (
      shouldTear(
        state.kind,
        { outside: input.outside, above: input.above, screenY: input.screenY },
        state.geometry
      )
    ) {
      return next(
        'torn',
        [
          {
            phase: 'tear',
            screenX: input.screenX,
            screenY: input.screenY,
            offsetX: input.offsetX,
            offsetY: input.offsetY
          }
        ],
        !input.inBand
      );
    }
    return next('reorder');
  }
  if (input.type === 'end') {
    if (state.mode === 'torn')
      return done([{ phase: 'drop', screenX: input.screenX, screenY: input.screenY }]);
    return done([], state.mode === 'reorder');
  }
  if (input.type === 'escape') {
    return done(state.mode === 'torn' ? [{ phase: 'cancel' }] : []);
  }
  // lost：pointercancel / lostpointercapture。torn 则在最后已知位置落下（避免窗口悬空），否则放弃排序。
  if (state.mode === 'torn')
    return done([{ phase: 'drop', screenX: state.lastScreen.x, screenY: state.lastScreen.y }]);
  return done();
}
