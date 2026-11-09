// Pure helpers are shared with the offline tests. No cookie values are logged.
export function selectedSites(text) {
  const entries = text.split(/[\s,]+/).filter(Boolean);
  if (!entries.length || entries.length > 30) throw new Error('请填写 1–30 个网站，分批迁移。');
  return [...new Set(entries.map(entry => {
    const url = new URL(entry.includes('://') ? entry : `https://${entry}`);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || !url.hostname || /[^a-z0-9._-]/i.test(url.hostname) || url.hostname.includes('..') || url.hostname.startsWith('.')) throw new Error('网站地址无效，请填写普通域名或 IPv4 地址。');
    return url.hostname.toLowerCase().replace(/\.$/, '');
  }))];
}
export function permissionOrigins(sites) {
  return sites.flatMap(host => ['http', 'https'].map(scheme => `${scheme}://${host === 'localhost' || /^[\d.]+$/.test(host) ? host : `*.${host}`}/*`));
}
export async function exportCookies(api, sites) {
  const all = [];
  for (const domain of sites) {
    // Empty partitionKey queries all partitions on supported Chrome versions.
    // Also explicitly query the unpartitioned jar; never silently drop CHIPS.
    all.push(...await api.getAll({ domain }), ...await api.getAll({ domain, partitionKey: {} }));
  }
  const seen = new Set();
  const cookies = [];
  for (const row of all) {
    const host = row.domain.replace(/^\./, '').toLowerCase();
    if (!sites.some(site => host === site || host.endsWith(`.${site}`))) continue;
    if (row.partitionKey && typeof row.partitionKey.hasCrossSiteAncestor !== 'boolean') throw new Error('当前 Chrome 无法提供完整分区信息，请更新 Chrome 后重试。');
    const key = JSON.stringify([row.domain, row.name, row.path, row.partitionKey?.topLevelSite ?? null, row.partitionKey?.hasCrossSiteAncestor ?? null]);
    if (seen.has(key)) continue;
    seen.add(key);
    const item = Object.fromEntries(['domain', 'name', 'value', 'path', 'secure', 'httpOnly', 'hostOnly', 'session', 'sameSite'].map(key => [key, row[key]]));
    if (!row.session) item.expirationDate = row.expirationDate;
    if (row.partitionKey) item.partitionKey = { topLevelSite: row.partitionKey.topLevelSite, hasCrossSiteAncestor: row.partitionKey.hasCrossSiteAncestor };
    cookies.push(item);
  }
  if (cookies.length > 10000) throw new Error('超过 10,000 条 Cookie，请缩小网站范围后重试。');
  const content = JSON.stringify({ format: 'astrion-cookie-export', version: 1, createdAt: new Date().toISOString(), sites, cookies });
  if (new TextEncoder().encode(content).byteLength > 4 * 1024 * 1024) throw new Error('文件超过 4 MiB，请缩小网站范围后重试。');
  return { content, count: cookies.length, partitioned: cookies.filter(row => row.partitionKey).length };
}
