import { homedir } from 'node:os';

export function shortPath(path: string): string {
  const home = homedir();
  return path.startsWith(home) ? `~${path.slice(home.length)}` : path;
}

export function formatContext(used: number, limit: number): string {
  if (!limit || limit <= 0) return formatTokens(used);
  const left = Math.max(0, limit - used);
  const pct = limit > 0 ? Math.round((left / limit) * 100) : 0;
  return `${pct}% left ${formatTokens(used)} / ${formatTokens(limit)}`;
}

export function formatTokens(value: number): string {
  if (value >= 1000) {
    const rounded = Math.round((value / 1000) * 10) / 10;
    return `${rounded}K`;
  }
  return String(value);
}

export function nowSessionId(): string {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function lastLines(lines: string[], limit = 5): string[] {
  return lines.slice(Math.max(0, lines.length - limit));
}
