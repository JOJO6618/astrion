import { onMounted } from 'vue';

type ThemeKey = 'classic' | 'light' | 'dark';

const THEME_STORAGE_KEY = 'agents_ui_theme';

/** 桌面壳注入的首帧主题（preload 经 shell:theme-get 取得，见 window.js）。 */
const SHELL_THEME_KEY = '__ASTRION_INITIAL_THEME__';

const isThemeKey = (value: unknown): value is ThemeKey =>
  value === 'classic' || value === 'light' || value === 'dark';

interface DesktopThemeBridge {
  surface?: (color: string) => void;
  theme?: (theme: ThemeKey) => void;
  onThemeChange?: (callback: (theme: string) => void) => () => void;
}

const desktopBridge = (): DesktopThemeBridge | undefined =>
  (window as { astrionWindow?: DesktopThemeBridge }).astrionWindow;

/** 内容视图走 astrionWindow，标签条（chrome 视图）只有 astrionShell。 */
const themeChangeSource = (): DesktopThemeBridge | undefined => {
  const host = window as {
    astrionWindow?: DesktopThemeBridge;
    astrionShell?: DesktopThemeBridge;
  };
  return host.astrionWindow?.onThemeChange ? host.astrionWindow : host.astrionShell;
};

/** Desktop shell: 上报主题键（壳层记住，下次启动首帧直接用），并在下一帧同步窗口底色。 */
const syncDesktopTheme = (theme: ThemeKey) => {
  const bridge = desktopBridge();
  if (!bridge) return;
  try {
    bridge.theme?.(theme);
  } catch {
    // 壳层不可用时忽略：只影响下次启动首帧。
  }
  // Stylesheets may still be loading on first paint; read after the next frame.
  requestAnimationFrame(() => {
    const color = getComputedStyle(document.documentElement)
      .getPropertyValue('--surface-base')
      .trim();
    if (/^#[a-f\d]{6}$/i.test(color)) bridge.surface?.(color);
  });
};

const applyTheme = (theme: ThemeKey, fromShell = false) => {
  const root = document.documentElement;
  root.setAttribute('data-theme', theme);
  document.body.setAttribute('data-theme', theme);
  // 收到壳层广播时不回报，避免多个视图来回触发 IPC。
  if (!fromShell) syncDesktopTheme(theme);
};

const loadTheme = (): ThemeKey => {
  if (typeof window === 'undefined') return 'classic';
  const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (isThemeKey(saved)) return saved;
  // 桌面壳：后端端口变化后新 origin 的 localStorage 是空的，用壳层记住的上次主题兜底，
  // 否则首帧会先按 classic 画一遍（顶部标签条与对话区颜色不一致）。
  const shellTheme = (window as unknown as Record<string, unknown>)[SHELL_THEME_KEY];
  return isThemeKey(shellTheme) ? shellTheme : 'classic';
};

const persistTheme = (theme: ThemeKey) => {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(THEME_STORAGE_KEY, theme);
};

/** 桌面多视图：隐藏视图挂着不卸载，靠壳层广播把主题变更追上去（只订阅一次）。 */
let themeBroadcastSubscribed = false;
const subscribeThemeBroadcast = () => {
  if (themeBroadcastSubscribed) return;
  const source = themeChangeSource();
  if (!source?.onThemeChange) return;
  themeBroadcastSubscribed = true;
  try {
    source.onThemeChange((theme) => {
      if (isThemeKey(theme)) applyTheme(theme, true);
    });
  } catch {
    // 桥不可用时只影响跨视图同步。
  }
};

export const installTheme = () => {
  const theme = loadTheme();
  applyTheme(theme);
  subscribeThemeBroadcast();
};

export const useTheme = () => {
  const setTheme = (theme: ThemeKey) => {
    applyTheme(theme);
    persistTheme(theme);
  };

  const restore = () => applyTheme(loadTheme());

  onMounted(() => {
    restore();
  });

  return { setTheme, restore, loadTheme };
};

export type { ThemeKey };

/**
 * 统一主题应用入口：个人偏好与智能体工具改主题都必须走这里，
 * 否则只改当前视图的 DOM，桌面其它标签仍是旧配色。
 */
export const applyThemeKey = (theme: unknown) => {
  if (!isThemeKey(theme)) return false;
  applyTheme(theme);
  return true;
};
