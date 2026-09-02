/**
 * 引用来源 favicon：字母兜底 + 多源竞速决策。
 *
 * 背景：原实现直接 <img src="google favicon 服务"> + onerror 回退字母，
 * 但 google 服务在国内网络被墙时请求长时间挂起（不触发 error），
 * 图标位置长期空白。改为：
 *   1. 立即渲染域名首字母兜底（任何网络环境下不留空白）；
 *   2. 对多个图标源并行探测，2s 窗口内收集结果后统一决策；
 *   3. 全部失败 / 超时 / 确认站点无图标 → 保持字母，不再变化。
 *
 * 各源对「无图标站点」的行为（2026-09 国内直连 + 代理环境实测）：
 *   - google s2：国内被墙；代理环境下最快且对无图标站返回 HTTP 404
 *     （img 触发 onerror），结果**可信**；
 *   - yandex：国内直连可达（~1s），无图标站返回 200 + 1x1 空白 PNG，
 *     用 naturalWidth > 1 判定后结果**可信**；缺点是图标只有 16px；
 *   - icon.horse：国内直连可达（1~2.5s），图标质量最高（可达 256px），
 *     但对无图标站也返回 200 + 灰色字母占位图（免费版不可关闭），
 *     结果**不可信**——仅在任一可信源同时成功（佐证站点确有图标）时才采纳。
 */
interface SourceDef {
  build: (domain: string) => string;
  /** 可信源：能可靠区分「无图标」（404 或占位图可识别），成功结果可单独采纳 */
  trusted: boolean;
  /** 图片最小边长：加载成功但小于该值视为无图标占位（yandex 无图标返回 1x1 空白图） */
  minSize?: number;
}

/** 单域名探测窗口：超时后按已收集结果决策 */
const RACE_WINDOW_MS = 2000;

const SOURCES: SourceDef[] = [
  {
    trusted: true,
    build: (d) => `https://www.google.com/s2/favicons?domain=${encodeURIComponent(d)}&sz=64`
  },
  {
    trusted: true,
    minSize: 2,
    build: (d) => `https://favicon.yandex.net/favicon/${encodeURIComponent(d)}`
  },
  {
    trusted: false,
    build: (d) => `https://icon.horse/icon/${encodeURIComponent(d)}`
  }
];

/** 域名级结果缓存：同一域名全局只探测一次，所有 chip / popover 共享结果 */
const domainRaceCache = new Map<string, Promise<string | null>>();

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** 字母兜底占位 HTML（带域名标记，供 upgradeCitationFavicons 扫描升级） */
export function faviconFallbackHtml(domain: string): string {
  const d = (domain || '').replace(/^www\./, '');
  const letter = escapeHtml((d[0] || '?').toUpperCase());
  return `<span class="chip-letter" data-favicon-domain="${escapeHtml(d)}">${letter}</span>`;
}

type SourceOutcome = { status: 'pending' } | { status: 'ok'; url: string } | { status: 'fail' };

/**
 * 多源并行探测，窗口结束（或全部源返回）后统一决策：
 *   1. icon.horse 成功且任一可信源成功 → 采纳 icon.horse（佐证通过，质量最高）；
 *   2. 否则有可信源成功 → 按 SOURCES 顺序采纳第一个可信结果；
 *   3. 无可信源成功（站点无图标 / 全部失败 / 超时）→ null，保持字母兜底。
 */
function raceDomain(domain: string): Promise<string | null> {
  const cached = domainRaceCache.get(domain);
  if (cached) return cached;

  const race = new Promise<string | null>((resolve) => {
    const outcomes: SourceOutcome[] = SOURCES.map(() => ({ status: 'pending' }));
    const pending = new Set<HTMLImageElement>();
    let decided = false;

    const decide = () => {
      if (decided) return;
      decided = true;
      clearTimeout(timer);
      // 中止仍在挂起的请求（被墙源的连接会挂到 TCP 超时，主动置空 src 回收）
      pending.forEach((img) => {
        img.onload = null;
        img.onerror = null;
        img.src = '';
      });
      pending.clear();

      const firstTrusted = SOURCES.findIndex((s, i) => s.trusted && outcomes[i].status === 'ok');
      const horseIdx = SOURCES.findIndex((s, i) => !s.trusted && outcomes[i].status === 'ok');
      if (firstTrusted >= 0 && horseIdx >= 0) {
        resolve((outcomes[horseIdx] as { status: 'ok'; url: string }).url);
      } else if (firstTrusted >= 0) {
        resolve((outcomes[firstTrusted] as { status: 'ok'; url: string }).url);
      } else {
        resolve(null);
      }
    };

    const timer = setTimeout(decide, RACE_WINDOW_MS);

    SOURCES.forEach((src, i) => {
      const url = src.build(domain);
      const img = new Image();
      pending.add(img);
      img.onload = () => {
        pending.delete(img);
        const minSize = src.minSize ?? 1;
        outcomes[i] =
          img.naturalWidth >= minSize && img.naturalHeight >= minSize
            ? { status: 'ok', url }
            : { status: 'fail' };
        if (!pending.size) decide(); // 全部源已返回，提前结算
      };
      img.onerror = () => {
        pending.delete(img);
        outcomes[i] = { status: 'fail' };
        if (!pending.size) decide();
      };
      img.src = url;
    });
  });

  domainRaceCache.set(domain, race);
  return race;
}

/**
 * 扫描容器内的字母占位元素并启动探测升级：
 * 决策出真实 favicon 后原地替换（URL 已在探测时加载过，命中缓存无二次请求）。
 */
export function upgradeCitationFavicons(container: ParentNode) {
  const els = container.querySelectorAll<HTMLElement>(
    '.chip-letter[data-favicon-domain]:not([data-favicon-racing])'
  );
  els.forEach((el) => {
    const domain = el.dataset.faviconDomain || '';
    if (!domain) return;
    el.dataset.faviconRacing = '1';
    void raceDomain(domain).then((url) => {
      if (!url || !el.isConnected) return;
      const img = document.createElement('img');
      img.className = 'chip-favicon';
      img.alt = '';
      img.src = url;
      el.replaceWith(img);
    });
  });
}
