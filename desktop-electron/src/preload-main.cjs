// main 视图 preload：桌面壳环境标记，页面在任意脚本执行前可读到
// （登录页据此自动免登录；App.vue 的桌面端链接拦截/软件更新入口同据此判定）。
// contextIsolation 下页面主世界读不到 preload 变量，必须走 contextBridge。
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('astrionQuickSettings', {
  info: () => ipcRenderer.invoke('quick:info'),
  configure: (patch) => ipcRenderer.invoke('quick:configure', patch),
  permissions: () => ipcRenderer.invoke('quick:permissions'),
  capturePermission: () => ipcRenderer.invoke('quick:capture-permission'),
  inputPermission: () => ipcRenderer.invoke('quick:input-permission'),
  open: () => ipcRenderer.invoke('quick:open')
});
contextBridge.exposeInMainWorld('__ASTRION_DESKTOP__', true);
contextBridge.exposeInMainWorld('__ASTRION_PLATFORM__', process.platform);

// Only the trusted main renderer receives this bridge. Web pages use no preload.
if (process.platform === 'darwin') {
  const subscribe = (channel, callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on(channel, listener);
    return () => ipcRenderer.removeListener(channel, listener);
  };
  contextBridge.exposeInMainWorld('astrionBrowser', {
    connect: (scope, labels) => ipcRenderer.invoke('browser:connect', scope, labels),
    theme: surfaceColor => ipcRenderer.send('browser:theme', surfaceColor),
    command: payload => ipcRenderer.invoke('browser:command', payload),
    credentials: payload => ipcRenderer.invoke('browser:credentials', payload),
    onCredentialProgress: callback => subscribe('browser:credentials-progress', callback),
    layout: payload => ipcRenderer.send('browser:layout', payload),
    input: payload => ipcRenderer.send('browser:input', payload),
    onState: callback => subscribe('browser:state', callback),
    onShortcut: callback => subscribe('browser:shortcut', callback),
    onContextMenu: callback => subscribe('browser:context-menu', callback),
    onMessage: callback => subscribe('browser:message', callback),
    onAiActivity: callback => subscribe('browser:ai-activity', callback),
    onGeometryRequest: callback => subscribe('browser:geometry-request', callback)
  });
}
