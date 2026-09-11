// CLI 多语言：直接读 OS 语言设置（LC_ALL > LC_MESSAGES > LANG），不做应用内切换
// 模块加载即检测（import 顺序保证：data.ts 等使用方加载时 locale 已就绪）
import zhCN from './zh-CN';
import enUS from './en-US';

export type Locale = 'zh-CN' | 'en-US';
type Dict = Record<string, string>;

function detectLocale(): Locale {
  const raw = (process.env.LC_ALL || process.env.LC_MESSAGES || process.env.LANG || '').toLowerCase();
  // zh / zh_cn / zh-tw 等均归入中文文案（繁体后续可再分）
  return raw.includes('zh') ? 'zh-CN' : 'en-US';
}

export const locale: Locale = detectLocale();
const dict: Dict = (locale === 'zh-CN' ? zhCN : enUS) as unknown as Dict;
const fallback: Dict = zhCN as unknown as Dict;

/** 取文案：当前语言缺键时回退中文，再缺回显 key（开发期暴露漏译） */
export function t(key: string): string {
  return dict[key] ?? fallback[key] ?? key;
}
