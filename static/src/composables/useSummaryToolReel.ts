import { onBeforeUnmount, reactive, watch } from 'vue';

export interface SummaryReelGroup {
  id: string;
  batchKey: string;
  items: string[];
  running: boolean;
  active: boolean;
  ready?: boolean;
}

export interface SummaryReelView {
  items: string[];
  phase: 'idle' | 'rolling' | 'settle';
  offsetPx: number;
}

interface ReelState extends SummaryReelView {
  batchKey: string;
  index: number;
  completing: boolean;
  parked: boolean;
  pendingItems?: string[];
}

const ITEM_HEIGHT = 26;
const INTERVAL_MS = 1450;
const ROLL_MS = 520;
const SETTLE_MS = 170;
const OVERSHOOT_PX = 2;

/** Preserve the original vertical roll and rebound for parallel tools. */
export function useSummaryToolReel(getGroups: () => SummaryReelGroup[]) {
  const states = reactive<Record<string, ReelState>>({});
  const intervals = new Map<string, number>();
  const timeouts = new Map<string, Set<number>>();
  const frames = new Map<string, number>();
  let groupsById = new Map<string, SummaryReelGroup>();

  const clearTimers = (id: string) => {
    const interval = intervals.get(id);
    if (interval !== undefined) window.clearInterval(interval);
    intervals.delete(id);
    timeouts.get(id)?.forEach((timer) => window.clearTimeout(timer));
    timeouts.delete(id);
    const frame = frames.get(id);
    if (frame !== undefined) window.cancelAnimationFrame(frame);
    frames.delete(id);
  };

  const later = (id: string, delay: number, callback: () => void) => {
    const pending = timeouts.get(id) || new Set<number>();
    const timer = window.setTimeout(() => {
      pending.delete(timer);
      callback();
    }, delay);
    pending.add(timer);
    timeouts.set(id, pending);
  };

  const spin = (id: string) => {
    const state = states[id];
    if (!state || state.completing || state.parked || state.items.length < 2) return;
    const nextIndex = (state.index + 1) % state.items.length;
    const visualIndex = nextIndex === 0 ? state.items.length : nextIndex;
    const target = -visualIndex * ITEM_HEIGHT;
    state.phase = 'rolling';
    state.offsetPx = target - OVERSHOOT_PX;
    later(id, ROLL_MS, () => {
      if (states[id] !== state) return;
      state.phase = 'settle';
      state.offsetPx = target;
      state.index = nextIndex;
      later(id, SETTLE_MS, () => {
        if (states[id] !== state) return;
        state.phase = 'idle';
        if (nextIndex === 0) state.offsetPx = 0;
        if (state.pendingItems) {
          state.items = state.pendingItems;
          state.pendingItems = undefined;
        }
      });
    });
  };

  const finish = (id: string) => {
    const state = states[id];
    if (!state || state.completing || state.parked) return;
    clearTimers(id);
    state.completing = true;
    state.phase = 'idle';
    state.offsetPx = -state.index * ITEM_HEIGHT;
    const finalIndex = state.items.length - 1;
    const target = -finalIndex * ITEM_HEIGHT;
    const frame = window.requestAnimationFrame(() => {
      frames.delete(id);
      if (states[id] !== state) return;
      state.phase = 'rolling';
      state.offsetPx = target - OVERSHOOT_PX;
      later(id, ROLL_MS, () => {
        if (states[id] !== state) return;
        state.phase = 'settle';
        state.offsetPx = target;
        state.index = finalIndex;
      });
      later(id, ROLL_MS + SETTLE_MS + 260, () => {
        if (states[id] !== state) return;
        clearTimers(id);
        if (groupsById.get(id)?.running) {
          state.completing = false;
          state.parked = true;
          state.phase = 'idle';
          state.offsetPx = target;
        } else {
          delete states[id];
        }
      });
    });
    frames.set(id, frame);
  };

  watch(getGroups, (groups) => {
    groupsById = new Map(groups.map((group) => [group.id, group]));
    for (const group of groups) {
      let state: ReelState | undefined = states[group.id];
      if (state && state.batchKey !== group.batchKey) {
        clearTimers(group.id);
        delete states[group.id];
        state = undefined;
      }
      if (group.ready === false) {
        clearTimers(group.id);
        delete states[group.id];
        continue;
      }
      if (state?.completing) continue;
      const active = group.running && group.active && group.items.length > 1;
      if (!active) {
        if (state && !state.parked) {
          state.items = group.items;
          state.pendingItems = undefined;
          finish(group.id);
        } else if (!group.running || group.items.length < 2) {
          clearTimers(group.id);
          delete states[group.id];
        } else if (!state) {
          states[group.id] = {
            batchKey: group.batchKey, items: group.items,
            index: group.items.length - 1,
            phase: 'idle', offsetPx: -(group.items.length - 1) * ITEM_HEIGHT,
            completing: false, parked: true
          };
        }
        continue;
      }
      if (!state) {
        states[group.id] = {
          batchKey: group.batchKey, items: group.items, index: 0,
          phase: 'idle', offsetPx: 0, completing: false, parked: false
        };
      } else {
        // Keep the track geometry stable through a roll/rebound, especially
        // its duplicate first row when another full intent appends a member.
        if (state.phase === 'idle') state.items = group.items;
        else state.pendingItems = group.items;
        if (state.parked) state.parked = false;
      }
      if (!intervals.has(group.id)) {
        intervals.set(group.id, window.setInterval(() => spin(group.id), INTERVAL_MS));
      }
    }
    Object.keys(states).forEach((id) => {
      if (groupsById.has(id)) return;
      clearTimers(id);
      delete states[id];
    });
  }, { immediate: true, flush: 'post' });

  const shouldShow = (id: string) => {
    const state = states[id];
    return !!state && (state.completing || !!groupsById.get(id)?.running);
  };
  const getView = (id: string): SummaryReelView => {
    const state = states[id];
    if (!state) return { items: [], phase: 'idle', offsetPx: 0 };
    return {
      items: state.completing ? state.items : [...state.items, state.items[0]],
      phase: state.phase,
      offsetPx: state.offsetPx
    };
  };

  onBeforeUnmount(() => {
    Object.keys(states).forEach((id) => {
      clearTimers(id);
      delete states[id];
    });
  });
  return { shouldShow, getView };
}
