// 显示宽度工具：中西文混排时按「显示宽度」对齐（CJK/全角=2，其余=1）。
// 面板的列对齐一律用 padEndWidth，不要用 String.padEnd（按字符数算，中文行会歪）。

/** 单字符显示宽度：CJK 统一表意、全角形式、韩文音节、日文假名等宽区段计 2 */
export function charWidth(cp: number): number {
  if (
    (cp >= 0x1100 && cp <= 0x115f) || // Hangul Jamo
    (cp >= 0x2e80 && cp <= 0x303e) || // CJK Radicals .. CJK Symbols
    (cp >= 0x3041 && cp <= 0x33ff) || // Hiragana .. CJK Compat
    (cp >= 0x3400 && cp <= 0x4dbf) || // CJK Ext A
    (cp >= 0x4e00 && cp <= 0x9fff) || // CJK Unified
    (cp >= 0xa000 && cp <= 0xa4cf) || // Yi
    (cp >= 0xac00 && cp <= 0xd7a3) || // Hangul Syllables
    (cp >= 0xf900 && cp <= 0xfaff) || // CJK Compat Ideographs
    (cp >= 0xfe30 && cp <= 0xfe6f) || // CJK Compat Forms
    (cp >= 0xff01 && cp <= 0xff60) || // Fullwidth Forms
    (cp >= 0xffe0 && cp <= 0xffe6) || // Fullwidth Signs
    (cp >= 0x20000 && cp <= 0x2fffd) || // CJK Ext B..
    (cp >= 0x30000 && cp <= 0x3fffd)
  ) {
    return 2;
  }
  return 1;
}

/** 字符串显示宽度 */
export function displayWidth(text: string): number {
  let w = 0;
  for (const ch of text) {
    w += charWidth(ch.codePointAt(0)!);
  }
  return w;
}

/** 按显示宽度补齐（末尾补空格到 target 显示宽度；已超宽原样返回） */
export function padEndWidth(text: string, target: number, fill: string = ' '): string {
  const diff = target - displayWidth(text);
  return diff > 0 ? text + fill.repeat(diff) : text;
}
