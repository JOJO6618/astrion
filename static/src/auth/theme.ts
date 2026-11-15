export type ThemeKey = 'classic' | 'light' | 'dark';

const THEME_STORAGE_KEY = 'agents_ui_theme';

export const isThemeKey = (value: unknown): value is ThemeKey =>
  value === 'classic' || value === 'light' || value === 'dark';

export const loadTheme = (): ThemeKey => {
  if (typeof window === 'undefined') return 'classic';
  const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (isThemeKey(saved)) return saved;
  // 桌面壳：端口变化后新 origin 的 localStorage 是空的，用壳层记住的上次主题兜底。
  const shellTheme = (window as unknown as Record<string, unknown>).__ASTRION_INITIAL_THEME__;
  return isThemeKey(shellTheme) ? shellTheme : 'classic';
};

export const applyTheme = (theme: ThemeKey) => {
  const root = document.documentElement;
  root.setAttribute('data-theme', theme);
  document.body.setAttribute('data-theme', theme);
};

export const setTheme = (theme: ThemeKey) => {
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }
  applyTheme(theme);
};
