// 桌面壳通知偏好（Electron 主进程权威来源）。
//
// 对话弹窗通知由主进程轮询 /api/conversation-tabs/activity 直接产生，不经过后端
// personalization，因此开关也保存在壳层 userData 内，主进程与设置页都从这里读写。
// 只保存壳层自己能决定的行为：关闭后仅停止对话横幅和 Dock/任务栏提醒，
// 系统定时任务通知（schedule-notifications.js）与前端未读标记不受影响。

import { app, ipcMain } from 'electron';
import fs from 'node:fs';
import path from 'node:path';

const FILE_NAME = 'notification-settings.json';

export const DEFAULT_NOTIFICATION_SETTINGS = Object.freeze({
  // Astrion 弹窗通知：对话完成 / 等待审批 / 提问时在屏幕角落弹出横幅并提醒 Dock/任务栏
  conversationNotifications: true,
});

let cached;
const listeners = new Set();

const settingsFile = () => path.join(app.getPath('userData'), FILE_NAME);

/** 只接受已知键与布尔值，其它内容（含损坏文件）一律回退默认值。 */
export function sanitizeNotificationSettings(raw) {
  const result = { ...DEFAULT_NOTIFICATION_SETTINGS };
  if (raw && typeof raw === 'object' && typeof raw.conversationNotifications === 'boolean') {
    result.conversationNotifications = raw.conversationNotifications;
  }
  return result;
}

export function getNotificationSettings() {
  if (!cached) {
    try {
      cached = sanitizeNotificationSettings(JSON.parse(fs.readFileSync(settingsFile(), 'utf8')));
    } catch {
      cached = { ...DEFAULT_NOTIFICATION_SETTINGS };
    }
  }
  return { ...cached };
}

function writeSettings(settings) {
  const target = settingsFile();
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const temp = `${target}.tmp`;
  fs.writeFileSync(temp, `${JSON.stringify(settings, null, 2)}\n`, 'utf8');
  fs.renameSync(temp, target);
}

/**
 * 合并 patch：先落盘，成功后才更新内存并向订阅者广播。
 * 写入失败直接抛出，让设置页回滚开关，而不是返回一个并未保存的状态。
 */
export function setNotificationSettings(patch) {
  if (!patch || typeof patch !== 'object') return getNotificationSettings();
  // 非法值（非布尔）不静默重置为默认，而是保留当前值。
  const current = getNotificationSettings();
  const next = { ...current };
  if (typeof patch.conversationNotifications === 'boolean') {
    next.conversationNotifications = patch.conversationNotifications;
  }
  if (next.conversationNotifications === current.conversationNotifications) return { ...current };
  writeSettings(next);
  cached = next;
  const snapshot = { ...next };
  for (const listener of listeners) {
    try {
      listener(snapshot);
    } catch (error) {
      console.warn('[astrion-desktop] Notification settings listener:', error?.message || error);
    }
  }
  return snapshot;
}

/** 订阅设置变化，返回取消函数。 */
export function onNotificationSettingsChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

let ipcRegistered = false;

/**
 * 注册设置页读写通道。调用方必须注入来源校验（受管内容视图 / 顶层 frame / 设置路由），
 * 避免任意页面或子 frame 都能改壳层通知开关；校验由壳层 window.js 提供。
 */
export function registerNotificationSettingsIpc(isTrustedSender) {
  if (ipcRegistered) return;
  if (typeof isTrustedSender !== 'function') {
    throw new TypeError('registerNotificationSettingsIpc requires a sender guard');
  }
  ipcRegistered = true;
  const trusted = (event) => {
    try {
      return isTrustedSender(event) === true;
    } catch {
      return false;
    }
  };
  ipcMain.handle('notification-settings:get', (event) => {
    if (!trusted(event)) throw new Error('notification settings: untrusted sender');
    return getNotificationSettings();
  });
  ipcMain.handle('notification-settings:set', (event, patch) => {
    if (!trusted(event)) throw new Error('notification settings: untrusted sender');
    return setNotificationSettings(patch);
  });
}
