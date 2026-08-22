#!/usr/bin/env python3
"""
color_map.py —— 硬编码色值 → token 反查。
解析 _tokens.scss 各主题真实值，对给定色值判定它等于哪个 token（A类）或无对应（B类）。

用法:
  python3 scripts/color_map.py <file>        # 分析某文件里所有硬编码色的可映射性
"""
import sys, re, os
from collections import defaultdict

TOK = "static/src/styles/base/_tokens.scss"

def norm(v):
    """归一化色值：小写、去多余空格、#abc->#aabbcc、rgb 空格统一。"""
    v = v.strip().lower()
    v = re.sub(r'\s+', '', v)
    m = re.fullmatch(r'#([0-9a-f])([0-9a-f])([0-9a-f])', v)
    if m:
        v = '#' + ''.join(c*2 for c in m.groups())
    return v

def load_tokens():
    txt = open(TOK, encoding='utf-8').read()
    # semantic blocks only ([data-theme])
    themes = {}
    for m in re.finditer(r":root\[data-theme='([^']+)'\]\s*\{([^}]*)\}", txt):
        name, body = m.group(1), m.group(2)
        d = {}
        for dm in re.finditer(r'(--[a-z0-9-]+)\s*:\s*([^;]+);', body):
            d[norm(dm.group(2))] = dm.group(1)
        themes[name] = d
    return themes

def analyze(path, themes):
    text = open(path, encoding='utf-8').read()
    # only style content for vue
    if path.endswith('.vue'):
        style = '\n'.join(m.group(1) for m in re.finditer(r'<style[^>]*>(.*?)</style>', text, re.S|re.I))
    else:
        style = text
    style = re.sub(r'/\*.*?\*/', '', style, flags=re.S)
    style = re.sub(r'//[^\n]*', '', style)
    vals = re.findall(r'#[0-9a-fA-F]{3,8}\b|rgba?\([0-9 ,.%/]+\)|hsla?\([0-9 ,.%/]+\)', style)
    A = defaultdict(list)  # value -> [(theme, token)]
    B = defaultdict(int)   # value -> count (no match)
    counts = defaultdict(int)
    for raw in vals:
        n = norm(raw)
        counts[raw] += 1
        hits = []
        for tname, d in themes.items():
            if n in d:
                hits.append((tname, d[n]))
        if hits:
            A[raw] = hits
        else:
            B[raw] += 1
    return counts, A, B

def main():
    themes = load_tokens()
    path = sys.argv[1]
    counts, A, B = analyze(path, themes)
    print(f"=== {path} ===")
    print(f"硬编码色值种类: {len(counts)}, 总出现: {sum(counts.values())}")
    print(f"\n--- A类(有 token 对应, 可映射) ---")
    for v in sorted(A, key=lambda x:-counts[x]):
        # 取 classic 优先的 token 名
        toks = {t:tok for t,tok in A[v]}
        cls = toks.get('classic')
        shown = cls or list(toks.values())[0]
        themes_hit = ','.join(sorted(toks.keys()))
        print(f"  {counts[v]:3d}×  {v:32s} -> {shown:28s} (命中主题: {themes_hit})")
    print(f"\n--- B类(无 token 对应, 野色, 需决策) ---")
    for v in sorted(B, key=lambda x:-B[x]):
        print(f"  {B[v]:3d}×  {v}")
    if not B:
        print("  (无 — 此文件可全量安全迁移)")

if __name__ == '__main__':
    main()
