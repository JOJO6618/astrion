const { chromium, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const base = 'http://127.0.0.1:8192';
const out = path.join(__dirname, 'browser-evidence');
fs.mkdirSync(out, { recursive: true });
const checks = [];
const record = message => { checks.push(message); console.log('PASS', message); };
(async () => {
  const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/schedules`);
    if (page.url().includes('/login')) {
      await page.getByRole('button', { name: '宿主机模式（免登录）' }).click();
      await page.waitForURL(url => !url.pathname.includes('/login'));
      await page.goto(`${base}/schedules`);
    }
    await page.getByRole('button', { name: '新建计划', exact: true }).first().click();
    await page.getByRole('textbox', { name: '名称', exact: true }).fill('浏览器全流程验证');
    await page.getByRole('textbox', { name: '任务指令', exact: true }).fill('SCHEDULE_WRITE_PROBE 请写入隔离验证文件并汇报。');
    await page.getByRole('button', { name: '保存', exact: true }).click();
    await expect(page.locator('.schedule-list-item').filter({ hasText: '浏览器全流程验证' }).first()).toBeVisible();
    record('页面创建并保存计划');
    const request = context.request;
    async function api(route, body) {
      const csrf = await (await request.get(`${base}/api/csrf-token`)).json();
      const response = body === undefined ? await request.get(`${base}/api/schedules${route}`) : await request.post(`${base}/api/schedules${route}`, { data: body, headers: { 'X-CSRF-Token': csrf.token } });
      const json = await response.json();
      if (!response.ok() || !json.success) throw new Error(`API ${route}: ${JSON.stringify(json)}`);
      return json.data;
    }
    const plan = (await api('')).plans.find(item => item.definition.name === '浏览器全流程验证');
    if (!plan) throw new Error('Saved plan absent');
    await page.getByRole('button', { name: '立即运行', exact: true }).click();
    let run;
    await expect.poll(async () => { run = (await api(`/${plan.definition.id}/runs`))[0]; return run?.state; }, { timeout: 60000, intervals: [500,1000,2000] }).toBe('success');
    record('立即运行经真实RuntimeService完成');
    const project = path.resolve(__dirname, '../../_experiments/schedule-runtime/project/schedule-probe.txt');
    if (!fs.existsSync(project)) throw new Error(`Agent tool did not write ${project}; run=${JSON.stringify(run)}`);
    if (!fs.readFileSync(project, 'utf8').includes('Synthetic scheduled Agent tool execution verified.')) throw new Error('Wrong tool output');
    record('真实Agent文件工具写入隔离工作区');
    await page.getByRole('button', { name: '更多', exact: true }).click();
    await page.getByRole('button', { name: '运行记录', exact: true }).click();
    await expect(page.locator('.schedule-run').first()).toContainText('成功');
    await expect(page.locator('.schedule-run').first()).toContainText('隔离测试模型');
    await page.screenshot({ path: path.join(out, 'history.png'), fullPage: true });
    record('真实运行结果与摘要在页面显示');
    await page.locator('.schedule-detail-heading').getByRole('button', { name: /返回/ }).click();
    await page.getByRole('button', { name: '更多', exact: true }).click();
    await page.getByRole('button', { name: '导出 YAML', exact: true }).click();
    await expect(page.getByRole('textbox', { name: 'YAML 配置' })).toHaveValue(/schema_version: 1/);
    record('导出已保存的YAML配置');
    await page.locator('.schedule-detail-heading').getByRole('button', { name: /返回/ }).click();
    await page.getByRole('textbox', { name: '名称', exact: true }).fill('浏览器全流程验证（已编辑）');
    await page.getByRole('button', { name: '保存', exact: true }).click();
    await expect.poll(async () => (await api(`/${plan.definition.id}`)).definition.revision).toBe(2);
    record('修改与版本更新');
    const edited = await api(`/${plan.definition.id}`);
    const second = new Date(Date.now() + 61000);
    const scheduled = { ...edited.definition, name: '定时触发验证', enabled: true,
      schedule: { type: 'once', timezone: 'UTC', date: second.toISOString().slice(0,10), times: [second.toISOString().slice(11,16)] },
      message: '请回复本次单次定时任务已执行。' };
    delete scheduled.id; delete scheduled.revision;
    const timedPlan = await api('', { definition: scheduled });
    await expect.poll(async () => (await api(`/${timedPlan.definition.id}/runs`))[0]?.state, { timeout: 90000, intervals: [1000,2000] }).toBe('success');
    record('实际时钟触发单次任务，非手动受理');
    await expect.poll(async () => (await api(`/${timedPlan.definition.id}`)).definition.enabled).toBe(false);
    record('单次触发后自动停止');
    const goalPlan = await api('', { definition: { ...scheduled, enabled: false, name: '目标模式验证', goal: { mode: 'start', max_turns: 2 } } });
    await api(`/${goalPlan.definition.id}/actions`, { action: 'run', request_id: 'browser-goal-probe' });
    await expect.poll(async () => (await api(`/${goalPlan.definition.id}/runs`))[0]?.state, { timeout: 45000, intervals: [500,1000] }).toBe('success');
    record('目标启动与审核沿现有Runtime完成');
    await page.reload();
    await page.getByRole('button', { name: '新建计划', exact: true }).first().click();
    await page.getByRole('textbox', { name: '名称', exact: true }).fill('尚未保存的草稿');
    await page.waitForTimeout(3500);
    await expect(page.getByRole('textbox', { name: '名称', exact: true })).toHaveValue('尚未保存的草稿');
    await expect(page.locator('.schedule-detail-actions')).toHaveCount(0);
    record('轮询不抢占新建草稿');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: '定时任务', exact: true }).click();
    await page.screenshot({ path: path.join(out, 'mobile-list.png'), fullPage: true });
    await page.locator('.schedule-library-scrim').click({ position: { x: 385, y: 420 } });
    await expect(page.getByRole('textbox', { name: '名称', exact: true })).toBeVisible();
    await page.screenshot({ path: path.join(out, 'mobile.png'), fullPage: true });
    record('窄屏布局可编辑');
    if (errors.length) throw new Error(`Page errors: ${errors.join('\n')}`);
    fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify({ checks, errors }, null, 2));
    console.log('BROWSER_E2E_OK', checks.length);
  } finally { await browser.close(); }
})().catch(error => { fs.writeFileSync(path.join(out, 'failure.txt'), error.stack || String(error)); console.error(error); process.exitCode = 1; });
