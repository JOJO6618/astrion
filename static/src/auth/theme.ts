export type ThemeKey = 'classic' | 'light' | 'dark';

const THEME_STORAGE_KEY = 'agents_ui_theme';

export const isThemeKey = (value: unknown): value is ThemeKey =>
  value === 'classic' || value === 'light' || value === 'dark';

export const loadTheme = (): ThemeKey => {
  if (typeof window === 'undefined') return 'classic';
  const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
  return isThemeKey(saved) ? saved : 'classic';
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
