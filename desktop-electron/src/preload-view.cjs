// 内容视图 preload（每个标签一个视图；由原 preload-main.cjs 演化）：桌面壳环境标记，页面在任意脚本执行前可读到
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
// 前端主题底色/主题键 → 壳层（启动与拉伸时未绘制区域的颜色，兼下次启动的首帧主题）。
contextBridge.exposeInMainWorld('astrionWindow', {
  surface: color => ipcRenderer.send('window:surface', color),
  theme: key => ipcRenderer.send('shell:theme', key),
  // 其他标签/窗口切换主题后由壳层广播；返回取消函数。
  onThemeChange: callback => {
    const listener = (_event, theme) => callback(theme);
    ipcRenderer.on('shell:theme-changed', listener);
    return () => ipcRenderer.removeListener('shell:theme-changed', listener);
  }
});
contextBridge.exposeInMainWorld('__ASTRION_PLATFORM__', process.platform);

// 壳层通知偏好（仅当前支持对话弹窗的桌面平台暴露）：设置页「通用 → 通知」读写。
// 通知由主进程产生，这里只做开关通道；Tauri 壳无此桥，设置页据此隐藏该分组。
if (['darwin', 'win32'].includes(process.platform)) {
  contextBridge.exposeInMainWorld('astrionNotificationSettings', {
    get: () => ipcRenderer.invoke('notification-settings:get'),
    set: patch => ipcRenderer.invoke('notification-settings:set', patch)
  });
}

// 首帧主题：后端端口可能变，新 origin 的 localStorage 是空的，
// 从壳层记忆同步取一次，让页面内联脚本在样式生效前就能画对颜色。
{
  let initialTheme = '';
  try {
    initialTheme = ipcRenderer.sendSync('shell:theme-get') || '';
  } catch {
    initialTheme = '';
  }
  if (initialTheme === 'classic' || initialTheme === 'light' || initialTheme === 'dark') {
    contextBridge.exposeInMainWorld('__ASTRION_INITIAL_THEME__', initialTheme);
    try {
      if (document.documentElement) document.documentElement.setAttribute('data-theme', initialTheme);
    } catch {
      // 文档尚未就绪时由页面内联脚本处理。
    }
  }
}

// 壳层桥（设计 docs/desktop_multi_view.md §4.2）。身份由主进程按 sender 判定，页面不得自报。
{
  const subscribe = (channel, callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on(channel, listener);
    return () => ipcRenderer.removeListener(channel, listener);
  };
  contextBridge.exposeInMainWorld('astrionShell', {
    persistentSidebar: true,
    browserPanelReady: () => ipcRenderer.send('shell:browser-panel-ready'),
    reportBrowserPanel: open => ipcRenderer.send('shell:browser-panel-report', open),
    onBrowserPanelOpen: callback => subscribe('shell:browser-panel-open', callback),
    // 独立对话窗口里的浏览器开关：已有独立窗口则前置，否则开关内嵌面板；
    // 主进程按 sender 自定对话并校验该视图确实在该对话的独立窗口中。
    toggleBrowserPanel: () => ipcRenderer.send('shell:browser-panel-toggle'),
    // → { key, placement: 'main'|'detached', visible, legacy, state }；legacy 兜底页 key 为空。
    identity: () => ipcRenderer.invoke('shell:identity'),
    // 壳层广播的导航快照（revision 更大者才会被广播；转发者自己不会收到回显）。
    onTabsState: callback => subscribe('shell:tabs-state', callback),
    // 页面执行命令拿到更新的快照后转交壳层（壳层只接受更高 revision）。
    forwardTabsState: state => ipcRenderer.send('shell:tabs-forward', state),
    // 壳层显示/隐藏本视图时通知：{ visible }。
    onVisibility: callback => subscribe('shell:visibility', callback),
    // 前置主窗口（P2 独立窗口里点设置等按钮后调用）。
    focusMain: () => ipcRenderer.send('shell:focus-main'),
    // 本视图的归属变化（拖出/放回）：{ key, placement }，页面据此即时切换独立窗口布局。
    onPlacement: callback => subscribe('shell:placement', callback),
    // 前置持有该标签的窗口（open/workspace 命令返回 focus:"detached" 后调用）；主窗口标签则切过去。
    focusTab: key => ipcRenderer.send('shell:focus-tab', key)
  });
}

// Only the trusted main renderer receives this bridge. Web pages use no preload.
if (['darwin', 'win32'].includes(process.platform)) {
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
    credentialFileImport: process.platform === 'win32',
    connect: (scope, labels) => ipcRenderer.invoke('browser:connect', scope, labels),
    openConversationLink: url => ipcRenderer.invoke('browser:open-conversation-link', url),
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
