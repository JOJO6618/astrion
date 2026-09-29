// 桌面壳平台判定（唯一入口）。
//
// 背景：两个壳注入的平台标记口径不同，历史上各处分别写死，容易出现
// 「同一个判断在两套方言里各写一遍、其中一处永远不成立」的隐患：
//   - Electron 壳（desktop-electron）：preload 用 contextBridge 注入
//     process.platform → 'darwin' / 'win32' / 'linux'
//   - Tauri 壳（desktop，Windows 现行）：initialization_script 注入
//     → 'macos' / 'windows' / 'linux'
// 本模块把两种口径归一成 'macos' | 'windows' | 'linux'。

export type DesktopPlatform = 'macos' | 'windows' | 'linux';

/** 是否运行在桌面壳内（两个壳都会注入该标记；网页版为 false） */
export function isDesktopShell(): boolean {
  return Boolean((window as unknown as { __ASTRION_DESKTOP__?: boolean }).__ASTRION_DESKTOP__);
}

/** 归一化后的桌面壳平台；不在壳内或标记缺失时返回 null */
export function desktopPlatform(): DesktopPlatform | null {
  const raw = (window as unknown as { __ASTRION_PLATFORM__?: unknown }).__ASTRION_PLATFORM__;
  if (typeof raw !== 'string') return null;
  switch (raw.toLowerCase()) {
    case 'darwin':
    case 'macos':
    case 'mac':
      return 'macos';
    case 'win32':
    case 'windows':
    case 'win':
      return 'windows';
    case 'linux':
      return 'linux';
    default:
      return null;
  }
}

/** 是否 macOS 桌面壳 */
export function isMacDesktopShell(): boolean {
  return isDesktopShell() && desktopPlatform() === 'macos';
}

/** 是否 Windows 桌面壳 */
export function isWindowsDesktopShell(): boolean {
  return isDesktopShell() && desktopPlatform() === 'windows';
}
