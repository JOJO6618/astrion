import { onBeforeUnmount, ref, watch } from 'vue';
import { ChatApiError, chatApi, command, operationId } from './api';
import type { MediaRef } from './types';

interface Draft {
  text: string;
  media: MediaRef[];
  revision: number;
  dirty?: boolean;
}
interface DraftState {
  account: string;
  key: string;
  revision: number;
  sequence: number;
  value: Draft;
  version: number;
  savedVersion: number;
  writing: boolean;
  retry: number;
  blocked: boolean;
}
export function useChatDraft(report: (cause: unknown) => void) {
  const text = ref('');
  const media = ref<MediaRef[]>([]);
  const identity = ref(0);
  const ready = ref(false);
  const clientId = operationId();
  const states = new Map<string, DraftState>();
  let current: DraftState | null = null;
  let generation = 0;
  let timer = 0;
  let restoring = false;
  let disposed = false;
  const localKey = (state: DraftState) => `astrion:chat:draft:${state.account}:${state.key}`;
  function persist(state: DraftState) {
    try {
      localStorage.setItem(
        localKey(state),
        JSON.stringify({
          ...state.value,
          revision: state.revision,
          dirty: state.version !== state.savedVersion
        })
      );
    } catch (cause) {
      if (!disposed) report(cause);
    }
  }
  function capture() {
    if (!current || restoring) return;
    current.value = { text: text.value, media: [...media.value], revision: current.revision };
    current.version++;
    persist(current);
  }
  async function flush(state: DraftState) {
    if (state.writing || state.blocked || state.savedVersion === state.version) return;
    state.writing = true;
    window.clearTimeout(state.retry);
    state.retry = 0;
    const version = state.version;
    const value = state.value;
    try {
      const stored = await command<Draft>(
        `/drafts/${state.key}`,
        {
          ...value,
          base_revision: state.revision,
          client_id: clientId,
          sequence: ++state.sequence
        },
        'PUT'
      );
      state.revision = stored.revision;
      state.savedVersion = version;
      persist(state);
    } catch (cause) {
      // A conflict keeps local work intact; never overwrite a newer remote draft.
      state.blocked = cause instanceof ChatApiError && cause.status === 409;
      if (!disposed) report(cause);
      if (!disposed && !state.blocked)
        state.retry = window.setTimeout(() => void flush(state), 3000);
    } finally {
      state.writing = false;
    }
    if (!disposed && !state.retry && !state.blocked && state.version !== state.savedVersion)
      void flush(state);
  }
  function save() {
    if (!current || restoring) return Promise.resolve();
    capture();
    return flush(current);
  }
  async function open(account: string, nextKey: string) {
    if (current) void save();
    const token = ++generation;
    identity.value = token;
    ready.value = false;
    window.clearTimeout(timer);
    restoring = true;
    const key = nextKey || 'new';
    const storageKey = `${account}:${key}`;
    let state = states.get(storageKey);
    if (!state) {
      state = {
        account,
        key,
        revision: 0,
        sequence: 0,
        version: 0,
        savedVersion: 0,
        writing: false,
        retry: 0,
        blocked: false,
        value: { text: '', media: [], revision: 0 }
      };
      try {
        const local: Draft | null = JSON.parse(localStorage.getItem(localKey(state)) || 'null');
        if (local) {
          state.value = local;
          state.revision = local.revision || 0;
          state.version = local.dirty ? 1 : 0;
        }
      } catch (cause) {
        report(cause);
      }
      states.set(storageKey, state);
    }
    current = state;
    text.value = state.value.text;
    media.value = state.value.media || [];
    try {
      const remote = await chatApi<Partial<Draft>>(`/drafts/${key}`);
      if (disposed || token !== generation) return;
      if (!state.writing) state.revision = remote.revision || 0;
      if (state.version === state.savedVersion) {
        state.value = {
          text: remote.text || '',
          media: remote.media || [],
          revision: state.revision
        };
        text.value = state.value.text;
        media.value = state.value.media;
      }
    } catch (cause) {
      if (token === generation) report(cause);
    } finally {
      if (!disposed && token === generation) {
        restoring = false;
        ready.value = true;
        if (state.version !== state.savedVersion) void flush(state);
      }
    }
  }
  function clear() {
    text.value = '';
    media.value = [];
    void save();
  }
  watch(
    [text, media],
    () => {
      if (restoring) return;
      capture();
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        if (current) void flush(current);
      }, 400);
    },
    { deep: true, flush: 'sync' }
  );
  const leaving = () => {
    if (current) {
      capture();
      persist(current);
    }
  };
  window.addEventListener('pagehide', leaving);
  onBeforeUnmount(() => {
    leaving();
    void save();
    disposed = true;
    generation++;
    window.clearTimeout(timer);
    for (const state of states.values()) window.clearTimeout(state.retry);
    window.removeEventListener('pagehide', leaving);
  });
  return { text, media, identity, ready, open, save, clear };
}
