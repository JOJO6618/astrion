// 真实 Electron 回归：隐藏窗口里的 NativeBrowser + BrowserAI，逐项调用 AI 浏览器操作。
// 运行：desktop-electron/node_modules/.bin/electron test/2026-11-09_AI浏览器工具/electron_harness.mjs
// 结果：_experiments/ai-browser-harness/<时间戳>/results.json
import { app, BaseWindow, WebContentsView } from 'electron';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../..');
const outDir = path.join(repo, '_experiments/ai-browser-harness', String(Date.now()));
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'astrion-ai-browser-'));
app.setPath('userData', path.join(temp, 'electron'));

const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = [];
function check(name, ok, detail = '') { results.push({ name, ok: !!ok, detail: String(detail).slice(0, 600) }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok ? '' : ` :: ${String(detail).slice(0, 300)}`}`); }
const refOf = (text, needle) => {
  const line = String(text || '').split('\n').find(l => l.includes(needle) && /\[ref=e\d+\]/.test(l));
  return line?.match(/\[ref=(e\d+)\]/)?.[1];
};

app.whenReady().then(async () => {
  const fixture = path.join(here, 'fixture');
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    if (url.pathname === '/api/delay') { setTimeout(() => { res.setHeader('Content-Type', 'text/plain'); res.end('ok'); }, Number(url.searchParams.get('ms')) || 300); return; }
    const file = path.join(fixture, url.pathname === '/' ? 'index.html' : url.pathname);
    if (!file.startsWith(fixture) || !fs.existsSync(file)) { res.statusCode = 404; res.end(); return; }
    res.setHeader('Content-Type', 'text/html; charset=utf-8'); res.end(fs.readFileSync(file));
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const { NativeBrowser, setBrowserDataRoot } = await import(pathToFileURL(path.join(repo, 'desktop-electron/src/browser/index.js')).href);
  setBrowserDataRoot(path.join(temp, 'data'));
  const win = new BaseWindow({ show: false, width: 1200, height: 800 });
  const main = new WebContentsView();
  win.contentView.addChildView(main); main.setBounds({ x: 0, y: 0, width: 1200, height: 800 });
  const nb = new NativeBrowser(win, main, origin);
  const identity = { username: 'host', workspace_id: 'ws1', conversation_id: 'conv_test' };
  let seq = 0;
  const call = (op, args = {}, extra = {}) => nb.ai.call({ call_id: `c${++seq}`, op, args, identity, epoch: 0, any_site: true, ...extra })
    .catch(error => ({ success: false, code: error.code || 'thrown', error: error.message }));
  const timed = async (op, args, extra) => { const t = Date.now(); const r = await call(op, args, extra); r._ms = Date.now() - t; return r; };
  try {
    let r = await timed('navigate', { url: `${origin}/index.html` });
    check('navigate 返回完整快照', r.success && r.snapshot?.kind === 'full' && r.snapshot.text.includes('点击计数'), JSON.stringify(r).slice(0, 400));
    check('tab 短 ID 为 t1', r.tab?.id === 't1', JSON.stringify(r.tab));
    let snap = r.snapshot.text;
    { const t = nb.tabs.values().next().value; console.log('DIAG bounds', JSON.stringify(t.view.getBounds()), t.view.getVisible(), nb.window.contentView.children.length); }
    console.log('DIAG', JSON.stringify(await call('evaluate', { function: '() => [innerWidth, innerHeight, document.getElementById("count").getBoundingClientRect().toJSON()]' })));
    r = await call('site');
    check('site 预检返回主机+端口', r.success && /^127\.0\.0\.1:\d+$/.test(r.site), JSON.stringify(r));
    r = await call('find', { text: '计数' });
    check('find 命中', r.success && r.total >= 1, JSON.stringify(r));
    r = await timed('click', { target: refOf(snap, '点击计数') });
    check('click 返回差异', r.success && r.snapshot?.kind === 'diff' && r.snapshot.text.includes('已点击 1'), JSON.stringify(r));
    check('click 耗时合理(<2.5s)', r._ms < 2500, r._ms);
    r = await call('click', { target: refOf(snap, '点击计数') });
    check('同一 ref 再次点击（名称已变）', r.success || r.code === 'stale_ref', JSON.stringify(r).slice(0, 300));
    r = await call('snapshot'); snap = r.snapshot.text;
    r = await call('type', { target: refOf(snap, '名字'), text: 'abc', submit: true });
    check('type + submit', r.success && JSON.stringify(r.snapshot).includes('提交:abc'), JSON.stringify(r));
    r = await call('type', { target: refOf(snap, '密码'), text: 'S3cretValue' });
    check('密码不出现在快照', r.success && !JSON.stringify(r).includes('S3cretValue'), JSON.stringify(r));
    nb.credentials.vault = () => ({ matches: origin => [{ username: 'alice', password: 'Hunter2Secret', origin }] });
    r = await call('fill_saved_login', { password_target: refOf(snap, '密码'), username_target: refOf(snap, '名字') });
    check('fill_saved_login 填写且不泄露密码', r.success && !JSON.stringify(r).includes('Hunter2Secret') && JSON.stringify(r.snapshot).includes('alice'), JSON.stringify(r));
    r = await call('evaluate', { function: '() => document.getElementById("pw").value' });
    check('密码确实已填入页面', r.result === 'Hunter2Secret', JSON.stringify(r).replace('Hunter2Secret', '<ok>'));
    r = await call('fill_saved_login', { password_target: refOf(snap, '名字') });
    check('fill_saved_login 拒绝非密码框', !r.success && r.code === 'not_password_field', JSON.stringify(r));
    r = await call('fill_saved_login', { password_target: refOf(snap, '密码') }, { any_site: false, allowed_sites: [] });
    check('fill_saved_login 需要站点授权', !r.success && r.code === 'site_not_approved', JSON.stringify(r));
    r = await call('select_option', { target: refOf(snap, 'combobox'), values: ['绿'] });
    check('select_option', r.success, JSON.stringify(r));
    r = await call('fill_form', { fields: [{ target: refOf(snap, '同意'), type: 'checkbox', value: 'true', element: '同意' }] });
    check('fill_form checkbox', r.success && JSON.stringify(r.snapshot).includes('checked'), JSON.stringify(r));
    r = await call('click', { target: refOf(snap, '删除') });
    check('confirm 对话框被接管', r.success && r.dialog?.type === 'confirm', JSON.stringify(r));
    r = await call('snapshot');
    check('有对话框时其他操作被拒', !r.success && r.code === 'dialog_pending', JSON.stringify(r));
    r = await call('handle_dialog', { accept: true });
    check('handle_dialog 确认', r.success && JSON.stringify(r.snapshot).includes('确认'), JSON.stringify(r));
    r = await call('click', { target: refOf(snap, '提示') });
    check('alert 被接管', r.dialog?.type === 'alert', JSON.stringify(r));
    r = await call('handle_dialog', { accept: false });
    check('alert 关闭', r.success, JSON.stringify(r));
    r = await timed('click', { target: refOf(snap, '延迟') });
    check('定时器 700ms 的变化被捕获', r.success && JSON.stringify(r.snapshot).includes('延迟出现'), JSON.stringify(r));
    r = await timed('click', { target: refOf(snap, '请求') });
    check('fetch 返回后的变化被捕获', r.success && JSON.stringify(r.snapshot).includes('加载:ok'), JSON.stringify(r) + ` ms=${r._ms}`);
    const upload = path.join(temp, 'upload.txt'); fs.writeFileSync(upload, 'hi');
    r = await call('snapshot'); snap = r.snapshot.text;
    const fileRef = refOf(snap, 'button "Choose File"') || refOf(snap, 'Choose') || refOf(snap, '选择文件');
    r = await call('file_upload', { paths: [upload], target: fileRef }, { readable_paths: [upload] });
    check('file_upload 直接设置', r.success && JSON.stringify(r.snapshot || {}).includes('upload.txt'), JSON.stringify(r) + ' ref=' + fileRef);
    r = await call('file_upload', { paths: ['/etc/hosts'], target: fileRef }, { readable_paths: [upload] });
    check('file_upload 拒绝范围外路径', !r.success && r.code === 'path_not_allowed', JSON.stringify(r));
    r = await call('click', { target: fileRef });
    check('点击文件按钮后提示文件选择框', r.success && (r.notes || []).some(n => n.includes('文件选择框')), JSON.stringify(r));
    r = await call('file_upload', { paths: [upload] }, { readable_paths: [upload] });
    check('file_upload 响应文件选择框', r.success, JSON.stringify(r));
    r = await call('take_screenshot');
    check('隐藏窗口截图', r.success && (r.image_base64 || '').length > 2000, JSON.stringify({ ...r, image_base64: (r.image_base64 || '').length }));
    if (r.image_base64) { fs.mkdirSync(outDir, { recursive: true }); fs.writeFileSync(path.join(outDir, 'shot.png'), Buffer.from(r.image_base64, 'base64')); }
    r = await call('console_messages', { level: 'error' });
    check('console error 收集', r.success && r.messages.some(m => m.message.includes('fixture error message')), JSON.stringify(r));
    r = await call('network_requests');
    const req = (r.requests || []).find(x => x.url.includes('/api/delay'));
    check('network_requests', !!req, JSON.stringify(r).slice(0, 300));
    if (req) { r = await call('network_request', { id: req.id }); check('network_request 详情', r.success && r.request.body === 'ok', JSON.stringify({ body: r.request?.body, note: r.request?.bodyNote, done: r.request?.status })); }
    r = await call('evaluate', { function: '() => document.title' });
    check('evaluate', r.success && r.result === 'AI 浏览器测试页', JSON.stringify(r));
    r = await call('click', { target: refOf(snap, '点击计数') }, { any_site: false, allowed_sites: ['example.com'] });
    check('站点校验拒绝', !r.success && r.code === 'site_not_approved', JSON.stringify(r));
    r = await call('press_key', { key: 'Tab' });
    check('press_key', r.success, JSON.stringify(r).slice(0, 200));
    r = await call('hover', { target: refOf(snap, '已点击') });
    check('hover', r.success, JSON.stringify(r).slice(0, 200));
    r = await call('scroll', { direction: 'down' });
    check('scroll', r.success, JSON.stringify(r).slice(0, 200));
    r = await call('emulate_media', { color_scheme: 'dark' });
    check('emulate_media', r.success, JSON.stringify(r).slice(0, 200));
    r = await call('snapshot'); snap = r.snapshot.text;
    const counter = refOf(snap, '已点击');
    r = await call('click', { target: refOf(snap, '遮挡') });
    r = await call('click', { target: counter });
    check('遮挡检测', !r.success && r.code === 'element_obscured', JSON.stringify(r));
    r = await call('click', { target: 'e99999' });
    check('失效 ref 立即报错', !r.success && r.code === 'stale_ref', JSON.stringify(r));
    r = await call('navigate', { url: `${origin}/page2.html` });
    check('新文档返回完整快照', r.success && r.snapshot.kind === 'full' && r.snapshot.text.includes('第二页'), JSON.stringify(r).slice(0, 300));
    r = await call('click', { target: counter });
    check('跳转后旧 ref 失效', !r.success && r.code === 'stale_ref', JSON.stringify(r));
    r = await call('navigate', { action: 'back' });
    check('后退', r.success && r.snapshot.text.includes('点击计数'), JSON.stringify(r).slice(0, 200));
    r = await call('wait_for', { text: '测试页', timeout: 2 });
    check('wait_for text', r.success, JSON.stringify(r).slice(0, 200));
    r = await call('wait_for', { text: '不存在的文字', timeout: 1 });
    check('wait_for 超时', !r.success && r.code === 'wait_timeout', JSON.stringify(r));
    r = await call('tabs', { action: 'new', url: `${origin}/page2.html` });
    check('tabs new', r.success && r.tab?.id === 't2' && r.snapshot?.kind === 'full', JSON.stringify(r).slice(0, 300));
    r = await call('tabs', { action: 'list' });
    check('tabs list', r.success && r.tabs.length === 2 && r.tabs.find(t => t.id === 't2')?.current, JSON.stringify(r));
    r = await call('tabs', { action: 'select', tab_id: 't1' });
    check('tabs select', r.success && r.tab.id === 't1', JSON.stringify(r).slice(0, 200));
    r = await call('tabs', { action: 'close', tab_id: 't2' });
    check('tabs close', r.success, JSON.stringify(r).slice(0, 200));
    r = await nb.ai.call({ call_id: 'other', op: 'tabs', args: { action: 'list' }, identity: { ...identity, conversation_id: 'conv_other' }, epoch: 0 });
    check('其他对话看不到本对话标签', r.success && r.tabs.length === 0, JSON.stringify(r));
    r = await call('snapshot', {}, { epoch: 1 });
    r = await call('click', { target: refOf(r.snapshot.text, '点击计数') || refOf(r.snapshot.text, '已点击') }, { epoch: 2 });
    check('epoch 变化返回完整快照', r.success && r.snapshot.kind === 'full', JSON.stringify(r).slice(0, 200));
    {
      // 模型在后台打开的标签显示到面板后，视口必须立即变为面板尺寸（不再保留 1280 宽的模拟）。
      const tab = [...nb.tabs.values()].find(row => row.view && nb.ai.drivers.has(row.id));
      const driver = nb.ai.drivers.get(tab.id);
      const before = (await driver.cdp('Runtime.evaluate', { expression: 'innerWidth', returnByValue: true })).result.value;
      nb.main.geometry = { x: 0, y: 0, width: 600, height: 500 }; nb.main.visible = true; nb.main.owner = tab.owner; nb.selected.set(tab.owner, tab.id);
      nb.layout(); await sleep(400);
      const after = (await driver.cdp('Runtime.evaluate', { expression: 'innerWidth', returnByValue: true })).result.value;
      check('标签显示后立即使用面板宽度', after === 600 && !driver.metricsOverride, `before=${before} after=${after} override=${driver.metricsOverride}`);
      nb.main.visible = false; nb.layout();
    }
    nb.ai.call({ call_id: 'cancel-me', op: 'wait_for', args: { time: 10 }, identity, epoch: 2, any_site: true }).then(x => { check('取消等待', !x.success || x.code === 'cancelled', JSON.stringify(x)); }, e => check('取消等待', e.code === 'cancelled', e.message));
    await sleep(300); nb.ai.cancel('cancel-me'); await sleep(500);
  } catch (error) {
    check('harness 未抛异常', false, error.stack);
  }
  fs.mkdirSync(outDir, { recursive: true });
  const summary = { passed: results.filter(r => r.ok).length, failed: results.filter(r => !r.ok).length, results };
  fs.writeFileSync(path.join(outDir, 'results.json'), JSON.stringify(summary, null, 2));
  console.log(JSON.stringify({ passed: summary.passed, failed: summary.failed, out: path.relative(repo, outDir) }));
  nb.dispose(); server.close(); app.exit(0);
});
