#!/usr/bin/env python3
"""
strip_fallback.py —— 安全剥离 var(--DEFINED_TOKEN, fallback) 的 fallback。

仅当 token 在 _tokens.scss 中已定义时才剥离（此时 fallback 永不生效，剥离=零视觉变化）。
未定义 token（fallback 是真实值）一律跳过。
带 --write 才落盘，否则只 dry-run 报告。

安全校验：每个被改的位置，改动后只是去掉了 ", fallback"，token 名和其余内容不变。
"""
import sys, re, glob, os

def load_defined():
    txt = open('static/src/styles/base/_tokens.scss', encoding='utf-8').read()
    return set(re.findall(r'(--[a-z0-9-]+)\s*:', txt))

def find_var_spans(s):
    """返回所有 var(...) 的 (start, end, inner) ，支持嵌套，end 是右括号下一位。"""
    spans = []
    i = 0
    while True:
        m = re.search(r'var\(', s[i:])
        if not m:
            break
        start = i + m.start()
        # 从 'var(' 的左括号开始做括号匹配
        depth = 0
        j = start + 3  # points at '('
        assert s[j] == '('
        k = j
        while k < len(s):
            if s[k] == '(':
                depth += 1
            elif s[k] == ')':
                depth -= 1
                if depth == 0:
                    break
            k += 1
        inner = s[j+1:k]
        spans.append((start, k+1, inner))
        i = k + 1
    return spans

def strip_one(s, defined):
    """对字符串 s 处理最外层 var()，递归处理 inner。返回 (新串, 改动数)。"""
    spans = find_var_spans(s)
    if not spans:
        return s, 0
    out = []
    last = 0
    changes = 0
    for start, end, inner in spans:
        out.append(s[last:start])
        # 先递归处理 inner（处理嵌套 var）
        new_inner, c_inner = strip_one(inner, defined)
        changes += c_inner
        # 解析最外层 var(--tok, fallback?) —— 在顶层逗号处分割
        mtok = re.match(r'\s*(--[a-z0-9-]+)\s*', new_inner)
        if mtok:
            tok = mtok.group(1)
            rest = new_inner[mtok.end():]
            if rest.startswith(','):
                # 有 fallback。token 已定义 → 剥离
                if tok in defined:
                    out.append(f'var({tok})')
                    changes += 1
                else:
                    out.append(f'var({new_inner})')
            else:
                out.append(f'var({new_inner})')
        else:
            out.append(f'var({new_inner})')
        last = end
    out.append(s[last:])
    return ''.join(out), changes

def process_file(path, defined, write):
    src = open(path, encoding='utf-8').read()
    new, ch = strip_one(src, defined)
    if ch and write:
        open(path, 'w', encoding='utf-8').write(new)
    return ch, src, new

def verify(src, new):
    """校验：去掉所有空白后，new 必须是 src 删除若干 ', fallback' 的结果。
    粗校验：new 的非空白字符是 src 非空白字符的子序列，且行数不变。"""
    if src.count('\n') != new.count('\n'):
        return False, "行数变化"
    # 每行：new 行必须是 old 行的子序列（只删不增）
    for o, n in zip(src.splitlines(), new.splitlines()):
        oi = 0
        for ch in n:
            oi = o.find(ch, oi)
            if oi == -1:
                return False, f"非子序列: {o!r} -> {n!r}"
            oi += 1
    return True, "ok"

def main():
    write = '--write' in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    defined = load_defined()
    files = args or (glob.glob('static/src/**/*.scss', recursive=True) +
                     glob.glob('static/src/**/*.vue', recursive=True))
    files = [f for f in files if '_tokens' not in f and '_virtual-monitor' not in f]
    total = 0
    for f in sorted(files):
        ch, src, new = process_file(f, defined, write=False)
        if not ch:
            continue
        ok, msg = verify(src, new)
        flag = "OK" if ok else f"FAIL({msg})"
        print(f"{ch:3d} strips  [{flag}]  {f}")
        if ok and write:
            open(f, 'w', encoding='utf-8').write(new)
        total += ch
    print(f"\n{'已写入' if write else 'DRY-RUN'} 总剥离: {total} 处")

if __name__ == '__main__':
    main()
