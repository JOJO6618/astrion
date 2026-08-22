#!/usr/bin/env python3
"""
lint_check.py —— Node 段错误环境下的 stylelint 替身验证器。

复刻 .stylelintrc.cjs 的三条颜色规则 + 基础语法完整性，用于在迁移每个文件后
确认它已彻底清理干净（可安全从 BASELINE_EXEMPT 移除）。

用法:
  python3 scripts/lint_check.py <file1> [file2] ...
  python3 scripts/lint_check.py --all      # 扫所有 .scss/.vue（排除永久豁免）

规则（与 stylelintrc 对齐）:
  R1 color-no-hex            : 禁止裸 #hex
  R2 literal-rgb-in-color    : 颜色属性值禁止 rgb()/hsl() 字面色
  R3 var-fallback-in-color   : 颜色属性值禁止 var(--x, <fallback>)
  R4 prefers-color-scheme    : 禁止 @media prefers-color-scheme

注意: 只检查 <style> / scss 内容。.vue 文件只扫 <style> 块，跳过 <template>/<script>。
"""
import sys, re, os, glob

PERMANENT_IGNORE = {
    'static/src/styles/base/_tokens.scss',
    'static/src/styles/components/chat/_virtual-monitor.scss',
}

COLOR_PROP = re.compile(
    r'^\s*(-?\w[\w-]*)?\s*('
    r'color|fill|stroke|background|background-color|border|border-color|'
    r'border-top-color|border-right-color|border-bottom-color|border-left-color|'
    r'box-shadow|outline|outline-color|text-decoration-color|caret-color|'
    r'column-rule-color|stop-color|text-shadow'
    r')\s*:', re.I)

HEX = re.compile(r'#[0-9a-fA-F]{3,8}\b')
RGB = re.compile(r'\b(rgba?|hsla?)\s*\(')
VARFALLBACK = re.compile(r'var\(\s*--[^,()]+,')

def extract_style_blocks(text, is_vue):
    """返回 [(start_line, block_text)]。vue 只取 <style>，scss 取全文。"""
    if not is_vue:
        return [(1, text)]
    blocks = []
    for m in re.finditer(r'<style[^>]*>(.*?)</style>', text, re.S | re.I):
        start_line = text[:m.start(1)].count('\n') + 1
        blocks.append((start_line, m.group(1)))
    return blocks

def strip_comments(s):
    s = re.sub(r'/\*.*?\*/', lambda m: '\n' * m.group(0).count('\n'), s, flags=re.S)
    s = re.sub(r'//[^\n]*', '', s)
    return s

def check_text(block_text, base_line):
    errs = []
    clean = strip_comments(block_text)
    clean_lines = clean.splitlines()
    # R1 hex / R4 prefers-color-scheme —— 行级
    for i, line in enumerate(clean_lines):
        ln = base_line + i
        if 'prefers-color-scheme' in line:
            errs.append((ln, 'R4 prefers-color-scheme', line.strip()))
        if HEX.search(line):
            errs.append((ln, 'R1 no-hex', line.strip()))
    # R2/R3 —— 声明级（跨行）。按 ; { } 切成声明段，每段判断是否颜色属性，
    # 段内若含 rgb/hsl 字面色或 var(--x, fallback)，把违规行号定位到该 token
    # 实际出现的那一行（声明可能跨多行，stylelint 报的是声明起始行；这里
    # 我们用 token 出现行，足够定位）。
    def line_of(char_idx):
        return base_line + clean.count('\n', 0, char_idx)
    pos = 0
    for m in re.finditer(r'[;{}]', clean):
        seg = clean[pos:m.start()]
        seg_start = pos
        pos = m.end()
        if not COLOR_PROP.search(seg):
            continue
        for vm in VARFALLBACK.finditer(seg):
            errs.append((line_of(seg_start + vm.start()), 'R3 var-fallback', seg.strip()[:80]))
        for rm in RGB.finditer(seg):
            errs.append((line_of(seg_start + rm.start()), 'R2 literal-rgb', seg.strip()[:80]))
    # 处理最后一段（无尾分隔符的残留，通常为空）
    seg = clean[pos:]
    if COLOR_PROP.search(seg):
        for vm in VARFALLBACK.finditer(seg):
            errs.append((line_of(pos + vm.start()), 'R3 var-fallback', seg.strip()[:80]))
        for rm in RGB.finditer(seg):
            errs.append((line_of(pos + rm.start()), 'R2 literal-rgb', seg.strip()[:80]))
    errs.sort(key=lambda e: e[0])
    return errs

def syntax_check(text, is_vue):
    """大括号配对（针对 style 内容）。"""
    blocks = extract_style_blocks(text, is_vue)
    problems = []
    for base, bt in blocks:
        bt2 = strip_comments(bt)
        if bt2.count('{') != bt2.count('}'):
            problems.append(f"  大括号不配对 (block@L{base}): {{={bt2.count('{')} }}={bt2.count('}')}")
    return problems

def lint_file(path):
    text = open(path, encoding='utf-8').read()
    is_vue = path.endswith('.vue')
    all_errs = []
    for base, bt in extract_style_blocks(text, is_vue):
        all_errs.extend(check_text(bt, base))
    syn = syntax_check(text, is_vue)
    return all_errs, syn

def main():
    args = sys.argv[1:]
    if not args:
        print("用法: python3 scripts/lint_check.py <file...> | --all")
        sys.exit(2)
    if args == ['--all']:
        files = []
        for pat in ('static/src/**/*.scss', 'static/src/**/*.vue'):
            files += glob.glob(pat, recursive=True)
        files = [f for f in files if f.replace('\\', '/') not in PERMANENT_IGNORE]
    else:
        files = args
    total = 0
    for f in sorted(files):
        nf = f.replace('\\', '/')
        if nf in PERMANENT_IGNORE:
            print(f"SKIP (permanent ignore): {f}")
            continue
        errs, syn = lint_file(f)
        if errs or syn:
            print(f"\n✖ {f}")
            for ln, rule, txt in errs:
                print(f"  L{ln:<5} {rule:22} | {txt[:80]}")
            for s in syn:
                print(s)
            total += len(errs) + len(syn)
        else:
            print(f"✔ {f}")
    print(f"\n总问题数: {total}")
    sys.exit(1 if total else 0)

if __name__ == '__main__':
    main()
