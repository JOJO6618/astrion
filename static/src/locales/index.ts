// i18n 核心入口（复刻 utils/theme.ts 的模式）
// 规范见 doc/frontend/i18n_spec.md。
//
// 使用方式：
//   - Vue 模板：{{ $t('common.ok') }}（globalInjection 已开启，响应式）
//   - SFC script / 纯 TS 模块：import { t } from '@/locales'; t('common.ok')
//     —— 注意：这是「调用时求值」，不自带响应式；
//     需要随语言切换自动刷新的 computed/标签，请改用 useI18n() 或在 computed 内读取 currentLocale.value 建立依赖。
import { createI18n } from 'vue-i18n';
import { ref } from 'vue';
import type { App } from 'vue';
import zhCN from './zh-CN';
import enUS from './en-US';

export type LocaleKey = 'zh-CN' | 'en-US';
export type MessageSchema = typeof zhCN;

const LOCALE_STORAGE_KEY = 'agents_ui_locale';

const isLocaleKey = (value: string | null): value is LocaleKey =>
  value === 'zh-CN' || value === 'en-US';

const loadLocale = (): LocaleKey => {
  if (typeof window === 'undefined') return 'zh-CN';
  const saved = window.localStorage.getItem(LOCALE_STORAGE_KEY);
  // 默认固定 zh-CN（不跟随浏览器）：迁移尚未全覆盖，避免英文浏览器用户看到半中半英界面
  return isLocaleKey(saved) ? saved : 'zh-CN';
};

export const i18n = createI18n<[MessageSchema], LocaleKey, false>({
  legacy: false,
  globalInjection: true, // 模板可直接用 $t，便于存量模板渐进迁移
  locale: loadLocale(),
  fallbackLocale: 'zh-CN',
  missingWarn: false,
  fallbackWarn: false,
  messages: {
    'zh-CN': zhCN,
    // enUS 用 DeepString 宽化过（key 奇偶校验在 en-US.ts 里完成），这里断言回 schema 形态
    'en-US': enUS as unknown as MessageSchema,
  },
});

// 当前语言（响应式）。需要在 setup 外/普通 TS 中追踪语言变化时使用。
export const currentLocale = ref<LocaleKey>(i18n.global.locale.value as LocaleKey);

export const setLocale = (locale: LocaleKey) => {
  i18n.global.locale.value = locale;
  currentLocale.value = locale;
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
    document.documentElement.setAttribute('lang', locale);
  }
};

// 非模板环境的统一取词入口（调用时求值，非响应式，见文件头注释）
export const t = i18n.global.t.bind(i18n.global) as typeof i18n.global.t;

export const installI18n = (app: App) => {
  app.use(i18n);
  document.documentElement.setAttribute('lang', loadLocale());
};

export const useLocale = () => {
  return { locale: currentLocale, setLocale, loadLocale };
};
