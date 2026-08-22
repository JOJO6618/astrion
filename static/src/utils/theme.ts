import { onMounted } from 'vue';

type ThemeKey = 'classic' | 'light' | 'dark';

const THEME_STORAGE_KEY = 'agents_ui_theme';

const applyTheme = (theme: ThemeKey) => {
  const root = document.documentElement;
  root.setAttribute('data-theme', theme);
  document.body.setAttribute('data-theme', theme);

  // 调试信息

  // 检查样式是否生效
  setTimeout(() => {

    // 输入栏底色
    const stadiumShell = document.querySelector('.stadium-shell');
    if (stadiumShell) {
      const styles = window.getComputedStyle(stadiumShell);
    }

    // 对话区域顶部的模型选择器
    const ribbonSelector = document.querySelector('.conversation-ribbon__selector');
    if (ribbonSelector) {
      const styles = window.getComputedStyle(ribbonSelector);
    }

    const selectorModel = document.querySelector('.selector-model');
    if (selectorModel) {
      const styles = window.getComputedStyle(selectorModel);
    }

    const selectorMode = document.querySelector('.selector-mode');
    if (selectorMode) {
      const styles = window.getComputedStyle(selectorMode);
    }

    // 下拉菜单
    const dropdown = document.querySelector('.model-mode-dropdown');
    if (dropdown) {
      const styles = window.getComputedStyle(dropdown);
    }

    const dropdownItem = document.querySelector('.dropdown-item');
    if (dropdownItem) {
      const styles = window.getComputedStyle(dropdownItem);
    }

  }, 100);
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
