// chrome 视图（顶部标签条）preload：桌面标记（标签 store 以 __ASTRION_DESKTOP__
// 判定启用）+ __ASTRION_CHROME__ 供页面自检加载位置 + astrionShell（标签条专用）。
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('__ASTRION_DESKTOP__', true);
contextBridge.exposeInMainWorld('__ASTRION_CHROME__', true);
contextBridge.exposeInMainWorld('__ASTRION_PLATFORM__', process.platform);

// 首帧主题：后端端口可能变，新 origin 的 localStorage 是空的，
// 从壳层记忆同步取一次，让 chrome.html 的内联脚本在样式生效前就能画对颜色。
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

const subscribe = (channel, callback) => {
  const listener = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
};
contextBridge.exposeInMainWorld('astrionShell', {
  // 乐观切换：壳层立即显示该标签的视图（缺失则创建），随后以后端快照为准。
  show: key => ipcRenderer.send('shell:show', key),
  // chrome 轮询到（或命令响应得到）的快照转交壳层；壳层只接受更高 revision 并广播。
  forwardTabsState: state => ipcRenderer.send('shell:tabs-forward', state),
  // 壳层广播的快照（其他视图转发的更新）。
  onTabsState: callback => subscribe('shell:tabs-state', callback),
  // 壳层请求打开某个页面（如 /settings/quick-chat）：{ route }，chrome 据此发后端 `page` 命令。
  onRequestPage: callback => subscribe('shell:request-page', callback),
  // 所有显示器的 bounds（含菜单栏区域）：{ displays: [{x,y,width,height}] }，供向上拖出计算屏幕上沿余量。
  dragInfo: () => ipcRenderer.invoke('shell:drag-info'),
  // 拖出（浏览器式）：{ key, phase:'tear'|'move'|'drop'|'cancel', screenX, screenY, offsetX?, offsetY? }。
  // tear：越过拖出阈值的瞬间发一次，壳层立即在光标下建独立窗口（不聚焦）并迁入视图，窗口内抓取点偏移为 offsetX/Y；
  // move：rAF 节流，壳层按 指针-偏移 摆放窗口；drop：松手（或 pointercancel/lostpointercapture 于最后位置），
  // 壳层聚焦窗口并请 chrome 发 detach；cancel：指针回到标签条或 Esc，壳层把视图放回主窗口。
  dragTab: payload => ipcRenderer.send('shell:drag-tab', payload),
  // 壳层已把视图迁入独立窗口，请 chrome 发后端 detach 命令：{ key }。
  onRequestDetach: callback => subscribe('shell:request-detach', callback),
  // 用户关闭独立窗口，壳层已把视图放回主窗口，请 chrome 发 dock 命令：{ key }。
  onRequestDock: callback => subscribe('shell:request-dock', callback),
  // 独立窗口或主窗口获得焦点，请 chrome 发 focus 命令：{ key }（主窗口为当前 active key）。
  onWindowFocus: callback => subscribe('shell:window-focus', callback)
});
