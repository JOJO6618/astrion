import { onMounted } from 'vue';

type ThemeKey = 'classic' | 'light' | 'dark';

const THEME_STORAGE_KEY = 'agents_ui_theme';

/** Desktop shell: keep the native window background equal to the theme surface. */
const syncDesktopSurface = () => {
  const bridge = (window as { astrionWindow?: { surface: (color: string) => void } }).astrionWindow;
  if (!bridge) return;
  // Stylesheets may still be loading on first paint; read after the next frame.
  requestAnimationFrame(() => {
    const color = getComputedStyle(document.documentElement)
      .getPropertyValue('--surface-base')
      .trim();
    if (/^#[a-f\d]{6}$/i.test(color)) bridge.surface(color);
  });
};

const applyTheme = (theme: ThemeKey) => {
  const root = document.documentElement;
  root.setAttribute('data-theme', theme);
  document.body.setAttribute('data-theme', theme);
  syncDesktopSurface();
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
