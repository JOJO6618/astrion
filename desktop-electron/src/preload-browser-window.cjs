// 对话浏览器独立窗口（/browser-window）的 preload：暴露与主窗口相同形状的 astrionBrowser 桥，
// 让共享 BrowserPanel 原样工作。连接的对话由主进程在弹出时固定，renderer 传入的 scope 被忽略。
// sandbox preload 不能 require 本地模块，因此与 preload-main.cjs 的桥定义保持同步手写。
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('__ASTRION_DESKTOP__', true);
contextBridge.exposeInMainWorld('__ASTRION_PLATFORM__', process.platform);

const subscribe = (channel, callback) => {
  const listener = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
};
contextBridge.exposeInMainWorld('astrionBrowser', {
  window: 'popout',
  // 能力标记：与主窗口同步，声明本壳支持「跟随智能体」。
  followAi: true,
  connect: (scope, labels) => ipcRenderer.invoke('browser:connect', null, labels),
  theme: theme => ipcRenderer.send('browser:theme', theme),
  command: payload => ipcRenderer.invoke('browser:command', payload),
  credentials: payload => ipcRenderer.invoke('browser:credentials', payload),
  onCredentialProgress: callback => subscribe('browser:credentials-progress', callback),
  layout: payload => ipcRenderer.send('browser:layout', payload),
  input: payload => ipcRenderer.send('browser:input', payload),
  onState: callback => subscribe('browser:state', callback),
  onShortcut: callback => subscribe('browser:shortcut', callback),
  onContextMenu: callback => subscribe('browser:context-menu', callback),
  onMessage: callback => subscribe('browser:message', callback),
  onAiActivity: () => () => {},
  onGeometryRequest: callback => subscribe('browser:geometry-request', callback),
  onWindowState: callback => subscribe('browser:window-state', callback)
});
