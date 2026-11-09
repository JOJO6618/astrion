import { selectedSites, permissionOrigins, exportCookies } from './export.js';
const sitesInput = document.getElementById('sites');
const consent = document.getElementById('consent');
const button = document.getElementById('export');
const status = document.getElementById('status');
let accepted = false;
let busy = false;
function update() { button.disabled = busy || !accepted; consent.disabled = busy; sitesInput.disabled = busy; }
consent.addEventListener('click', () => {
  accepted = !accepted;
  consent.setAttribute('aria-checked', String(accepted));
  document.getElementById('mark').textContent = accepted ? '☑' : '□';
  update();
});
sitesInput.addEventListener('input', () => {
  accepted = false;
  consent.setAttribute('aria-checked', 'false');
  document.getElementById('mark').textContent = '□';
  update();
});
button.addEventListener('click', async () => {
  if (busy || !accepted) return;
  status.dataset.error = 'false';
  try {
    const sites = selectedSites(sitesInput.value);
    // Call permissions.request directly in the user's click stack.
    const permission = chrome.permissions.request({ origins: permissionOrigins(sites) });
    busy = true;
    update();
    if (!await permission) throw new Error('未获得网站授权，没有读取或导出 Cookie。');
    status.textContent = '正在读取已授权网站…';
    const result = await exportCookies(chrome.cookies, sites);
    if (!result.count) throw new Error('上述网站没有找到 Cookie。请检查网站范围及当前 Chrome 用户资料。');
    const link = document.createElement('a');
    const url = URL.createObjectURL(new Blob([result.content], { type: 'application/json' }));
    link.href = url;
    link.download = `astrion-cookies-${Date.now()}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    status.textContent = `已生成 ${result.count} 条 Cookie 的迁移文件（含 ${result.partitioned} 条分区 Cookie）。请在 Astrion 的导入页面选择此文件，预览后确认导入。`;
  } catch (error) {
    status.dataset.error = 'true';
    status.textContent = error instanceof Error ? error.message : '导出失败，请重试。';
  } finally { busy = false; update(); }
});
chrome.tabs.query({ active: true, currentWindow: true }).then(tabs => {
  try { const url = new URL(tabs[0]?.url); if (['http:', 'https:'].includes(url.protocol) && !sitesInput.value) sitesInput.value = url.hostname; } catch { /* User can enter the site explicitly. */ }
}).catch(() => {});
