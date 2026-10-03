import { onMounted } from 'vue';

type ThemeKey = 'classic' | 'light' | 'dark';

const THEME_STORAGE_KEY = 'agents_ui_theme';

const applyTheme = (theme: ThemeKey) => {
  const root = document.documentElement;
  root.setAttribute('data-theme', theme);
  document.body.setAttribute('data-theme', theme);
};

const loadTheme = (): ThemeKey => {
  if (typeof window === 'undefined') return 'classic';
  const saved = window.localStorage.getItem(THEME_STORAGE_KEY) as ThemeKey | null;
  if (saved === 'light' || saved === 'dark' || saved === 'classic') return saved;
  return 'classic';
};

const persistTheme = (theme: ThemeKey) => {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(THEME_STORAGE_KEY, theme);
};

export const installTheme = () => {
  const theme = loadTheme();
  applyTheme(theme);
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
