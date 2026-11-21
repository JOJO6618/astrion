/**
 * 提供商模型目录自动刷新（进入应用一次 + 每小时一次）。
 *
 * 真正的网络拉取由后端 `POST /api/providers/refresh-all` 完成，并在进程内做
 * 一小时节流——桌面多视图/多标签同时触发时只有一次会真正请求外部接口。
 * 前端每轮结束后重拉模型注册表，让模型选择器即时反映新上线的模型。
 *
 * 非管理员调用会被后端 403 拒绝：静默忽略刷新，但仍拉一次模型列表（拿管理员
 * 那边刷新过的结果）。任何失败都不打扰用户，下一轮再试。
 */
import { useModelStore } from '@/stores/model';

const INTERVAL_MS = 60 * 60 * 1000;
/** 首屏初始化先跑完再触发，避免和状态/对话请求抢带宽 */
const INITIAL_DELAY_MS = 5_000;
/** 后端本轮仍在刷新（其它视图触发）时，等它结束再拉一次列表 */
const RETRY_AFTER_IN_PROGRESS_MS = 30_000;

let intervalTimer: number | null = null;
let initialTimer: number | null = null;
let retryTimer: number | null = null;
let running = false;
let catalogChannel: BroadcastChannel | null = null;

export function notifyModelCatalogChanged(): void {
  catalogChannel?.postMessage('changed');
}

const pullModels = () =>
  useModelStore()
    .fetchModels()
    .catch(() => {});

async function refreshOnce(): Promise<void> {
  if (running) return;
  running = true;
  try {
    const resp = await fetch('/api/providers/refresh-all', { method: 'POST' }).catch(() => null);
    const data = resp ? await resp.json().catch(() => null) : null;
    if (data && data.reason === 'in_progress' && retryTimer === null) {
      // 别的视图正在刷新：等它完成后再同步一次注册表
      retryTimer = window.setTimeout(() => {
        retryTimer = null;
        void pullModels();
      }, RETRY_AFTER_IN_PROGRESS_MS);
    }
    await pullModels();
  } catch {
    /* 静默：网络/权限问题交给下一轮 */
  } finally {
    running = false;
  }
}

export function startModelCatalogAutoRefresh(): void {
  if (intervalTimer !== null) return;
  if (typeof BroadcastChannel !== 'undefined') {
    catalogChannel = new BroadcastChannel('astrion-model-catalog');
    catalogChannel.onmessage = () => void pullModels();
  }
  initialTimer = window.setTimeout(() => {
    initialTimer = null;
    void refreshOnce();
  }, INITIAL_DELAY_MS);
  intervalTimer = window.setInterval(() => void refreshOnce(), INTERVAL_MS);
}

export function stopModelCatalogAutoRefresh(): void {
  catalogChannel?.close();
  catalogChannel = null;
  if (initialTimer !== null) {
    clearTimeout(initialTimer);
    initialTimer = null;
  }
  if (retryTimer !== null) {
    clearTimeout(retryTimer);
    retryTimer = null;
  }
  if (intervalTimer !== null) {
    clearInterval(intervalTimer);
    intervalTimer = null;
  }
}
