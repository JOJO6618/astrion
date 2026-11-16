const { chromium, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const base = 'http://127.0.0.1:8192';
const out = path.join(__dirname, 'browser-evidence');
const checks = [];
const pass = name => { checks.push(name); console.log('PASS', name); };
(async () => {
  const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/schedules`);
    if (page.url().includes('/login')) { await page.getByRole('button', { name: '宿主机模式（免登录）' }).click(); await page.waitForURL(url => !url.pathname.includes('/login')); await page.goto(`${base}/schedules`); }
    async function api(route, body) {
      const csrf = await (await context.request.get(`${base}/api/csrf-token`)).json();
      const response = body === undefined ? await context.request.get(`${base}/api/schedules${route}`) : await context.request.post(`${base}/api/schedules${route}`, { data: body, headers: { 'X-CSRF-Token': csrf.token } });
      const json = await response.json();
      if (!response.ok() || !json.success) throw new Error(`${route}: ${JSON.stringify(json)}`);
      return json.data;
    }
    async function select(label, option, scope = page) {
      await scope.getByRole('button', { name: label, exact: true }).first().click();
      await page.getByRole('option', { name: option, exact: true }).click();
    }
    let last;
    for (const [kind,label] of [['once','单次'],['interval','间隔'],['daily','每天'],['weekly','每周'],['monthly','每月'],['cron','Cron']]) {
      await page.getByRole('button', { name: '新建计划', exact: true }).first().click();
      const name = `表单-${kind}-${Date.now()}`;
      await page.getByRole('textbox', { name: '名称', exact: true }).fill(name);
      await page.getByRole('textbox', { name: '任务指令', exact: true }).fill('检查表单配置。');
      await select('执行频率', label);
      if (kind === 'weekly') await page.locator('.schedule-weekdays').getByRole('button', { name: '日', exact: true }).click();
      if (kind === 'monthly') await page.getByRole('textbox', { name: '日期序号', exact: true }).fill('last');
      if (kind === 'cron') await page.getByRole('textbox', { name: 'Cron 表达式', exact: true }).fill('0 9 * * 1-5');
      await page.getByRole('button', { name: '保存', exact: true }).click();
      await expect.poll(async () => { last = (await api('')).plans.find(item => item.definition.name === name); return last?.definition.schedule.type; }).toBe(kind);
      pass(`页面保存${label}时间规则`);
    }
    await page.getByRole('button', { name: /默认配置/ }).click();
    const dialog = page.getByRole('dialog', { name: '执行配置', exact: true });
    await select('执行配置','自定义',dialog);
    await select('工作模式','执行',dialog);
    await select('权限','自动审核',dialog);
    await dialog.getByRole('switch', { name: '思考', exact: true }).click();
    await select('推理强度','高',dialog);
    await page.screenshot({ path: path.join(out, 'configuration.png'), fullPage: true });
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.locator('.schedule-editor-footer').getByRole('button', { name: '保存', exact: true }).click();
    await expect.poll(async () => (await api(`/${last.definition.id}`)).definition.configuration.mode).toBe('custom');
    pass('配置弹窗、共享下拉和开关保存');
    await page.getByRole('textbox', { name: '时区', exact: true }).fill('Invalid/Zone');
    await expect(page.locator('.schedule-error')).toContainText('时区不存在');
    await page.getByRole('textbox', { name: '时区', exact: true }).fill('Asia/Shanghai');
    await page.locator('.schedule-editor-footer').getByRole('button', { name: '保存', exact: true }).click();
    await expect(page.locator('.schedule-error')).toHaveCount(0);
    pass('非法时区显示校验错误且页面不崩溃');
    const stable = await api(`/${last.definition.id}`);
    const importValue = { ...stable.definition, name: `导入-${Date.now()}` };
    delete importValue.id; delete importValue.revision;
    await page.getByRole('button', { name: '导入 YAML', exact: true }).click();
    let importer = page.getByRole('dialog', { name: '导入计划' });
    await importer.getByRole('textbox', { name: 'YAML 配置' }).fill(JSON.stringify(importValue));
    await importer.getByRole('button', { name: '导入 YAML', exact: true }).click();
    await expect(page.getByRole('textbox', { name: '名称', exact: true })).toHaveValue(importValue.name);
    pass('导入新计划并选中');
    let imported = (await api('')).plans.find(item => item.definition.name === importValue.name);
    const overwrite = { ...imported.definition, name: `${importValue.name}-覆盖` };
    await page.getByRole('button', { name: '导入 YAML', exact: true }).click();
    importer = page.getByRole('dialog', { name: '导入计划' });
    await importer.getByRole('textbox', { name: 'YAML 配置' }).fill(JSON.stringify(overwrite));
    await importer.getByRole('switch', { name: '按 ID 和版本覆盖', exact: true }).click();
    await importer.getByRole('button', { name: '导入 YAML', exact: true }).click();
    await expect(page.getByRole('textbox', { name: '名称', exact: true })).toHaveValue(overwrite.name);
    pass('同ID覆盖后编辑器显示新版本');
    await page.getByRole('textbox', { name: '搜索计划', exact: true }).fill(overwrite.name);
    await expect(page.locator('.schedule-list-item')).toHaveCount(1);
    pass('搜索过滤真实计划');
    const toggle = page.locator('.schedule-detail-heading').getByRole('switch', { name: '开启计划' });
    await toggle.click();
    await expect.poll(async () => (await api(`/${imported.definition.id}`)).definition.enabled).toBe(true);
    await toggle.click();
    await expect.poll(async () => (await api(`/${imported.definition.id}`)).definition.enabled).toBe(false);
    pass('页面开启与暂停');
    await page.getByRole('button', { name: '更多', exact: true }).click();
    await page.getByRole('button', { name: '删除计划', exact: true }).click();
    await page.getByRole('dialog', { name: '删除这个计划？' }).getByRole('button', { name: '确认', exact: true }).click();
    await expect(page.locator('.schedule-editor')).toHaveCount(0);
    await expect(page.locator('.schedule-list-item')).toHaveCount(0);
    pass('删除后清空旧草稿');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: '定时任务', exact: true }).click();
    await page.getByRole('button', { name: '新建计划', exact: true }).first().click();
    await page.getByRole('textbox', { name: '名称', exact: true }).fill('窄屏配置');
    await page.getByRole('textbox', { name: '任务指令', exact: true }).fill('检查窄屏。');
    await page.getByRole('button', { name: /默认配置/ }).click();
    await select('执行配置','自定义',page.getByRole('dialog', { name: '执行配置', exact: true }));
    await page.screenshot({ path: path.join(out, 'mobile-dialog.png'), fullPage: true });
    await page.keyboard.press('Escape');
    pass('窄屏弹窗可打开、下拉可操作并关闭');
    if (errors.length) throw new Error(errors.join('\n'));
    fs.writeFileSync(path.join(out, 'forms-results.json'), JSON.stringify({ checks, errors }, null, 2));
    console.log('BROWSER_FORMS_OK', checks.length);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
