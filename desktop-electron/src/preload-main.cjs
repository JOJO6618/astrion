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
// 前端主题底色 → 窗口底色（启动与拉伸时未绘制区域的颜色）。
contextBridge.exposeInMainWorld('astrionWindow', {
  surface: color => ipcRenderer.send('window:surface', color)
});
contextBridge.exposeInMainWorld('__ASTRION_PLATFORM__', process.platform);

// Only the trusted main renderer receives this bridge. Web pages use no preload.
if (process.platform === 'darwin') {
  const subscribe = (channel, callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on(channel, listener);
    return () => ipcRenderer.removeListener(channel, listener);
  };
  contextBridge.exposeInMainWorld('astrionBrowser', {
    // 能力标记：本壳支持把对话浏览器弹出到独立窗口（preload-browser-window.cjs 为窗口侧）。
    popout: true,
    // 能力标记：本壳支持「跟随智能体」（AI 操作哪个标签，用户视图就跟到哪个）。
    followAi: true,
    connect: (scope, labels) => ipcRenderer.invoke('browser:connect', scope, labels),
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
    onAiActivity: callback => subscribe('browser:ai-activity', callback),
    onGeometryRequest: callback => subscribe('browser:geometry-request', callback)
  });
}
