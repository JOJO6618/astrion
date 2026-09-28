// chrome 视图（顶部标签条）preload：桌面标记（标签 store 以 __ASTRION_DESKTOP__
// 判定启用）+ __ASTRION_CHROME__ 供页面自检加载位置。
const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('__ASTRION_DESKTOP__', true);
contextBridge.exposeInMainWorld('__ASTRION_CHROME__', true);
