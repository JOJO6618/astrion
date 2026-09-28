// 应用菜单：两个关键点——
// 1. macOS 必须保留 Edit 菜单（role editMenu），否则渲染进程里 Cmd+C/V/X/A 全失效；
// 2. 绝不占用 Cmd+W / Cmd+T：前端标签条（双视图各自）用它们做「关闭标签/新建标签」，
//    菜单加速器优先级高于页面 keydown，一旦占用页面永远收不到。
//    关窗口挪到 Cmd+Shift+W（对齐浏览器惯例）。

import { app, BaseWindow, Menu } from 'electron';

export function installAppMenu() {
  const isDev = !app.isPackaged;
  const isMac = process.platform === 'darwin';

  /** @type {import('electron').MenuItemConstructorOptions[]} */
  const template = [];

  if (isMac) {
    template.push({ role: 'appMenu' });
  }

  template.push({ role: 'editMenu' });

  /** @type {import('electron').MenuItemConstructorOptions[]} */
  const viewSubmenu = [
    { role: 'reload' },
    { role: 'forceReload' },
    { type: 'separator' },
    { role: 'resetZoom' },
    { role: 'zoomIn' },
    { role: 'zoomOut' }
  ];
  if (isDev) {
    viewSubmenu.push({ type: 'separator' }, { role: 'toggleDevTools' });
  }
  template.push({ label: 'View', submenu: viewSubmenu });

  template.push({
    label: 'Window',
    submenu: [
      { role: 'minimize' },
      { role: 'zoom' },
      { type: 'separator' },
      {
        label: isMac ? 'Close Window' : 'Close',
        accelerator: 'CmdOrCtrl+Shift+W',
        click: () => {
          const win = BaseWindow.getFocusedWindow();
          win?.close();
        }
      }
    ]
  });

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}
