const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

module.exports = async function themeSwitch(main, manager, output, until, sleep, nativeColors) {
  const errors = [], samples = [], tabs = [];
  const onError = error => errors.push(error.stack || String(error));
  process.on('uncaughtException', onError);
  try {
    const settings = manager.registry.createInternal('settings');
    tabs.push(settings);
    assert.equal(settings.view, undefined);
    const verify = async scenario => {
      for (const [theme, color] of [['light', '#ffffff'], ['dark', '#1a1a1a'], ['classic', '#faf9f5'], ['dark', '#1a1a1a']]) {
        await main.webContents.executeJavaScript(`window.surfaceDiag.theme(${JSON.stringify(theme)})`);
        await until(() => manager.surfaceColor === color, `${scenario}: ${theme} reaches native manager`);
        await sleep(100);
        assert.deepEqual(errors, [], `${scenario}: main-process exception`);
        const persisted = JSON.parse(fs.readFileSync(path.join(output, 'electron/window-surface.json'), 'utf8'));
        assert.equal(persisted.color, color);
        assert.equal(nativeColors.get(manager.window.contentView), color);
        const active = manager.active();
        // WebContentsView overrides View.setBackgroundColor; the base-View probe
        // tracks the window root only. Actual webpage pixels are sampled separately.
        if (active?.view) assert.equal(active.view.webContents.isDestroyed(), false);
        const renderer = await main.webContents.executeJavaScript('window.surfaceDiag.inspect()');
        samples.push({ scenario, theme, color, persisted: persisted.color,
          activeSection: active?.section || '', activeView: !!active?.view,
          suspended: [...manager.tabs.values()].filter(tab => tab.suspended).length,
          html: renderer.html });
      }
    };
    await verify('browser-settings-without-view');
    const retentionAvailable = !!manager.retention;
    if (retentionAvailable) {
      const suspended = manager.registry.create('about:blank', { select: false });
      tabs.push(suspended);
      manager.retention.suspend(suspended);
      assert.equal(suspended.view, null);
      await verify('browser-settings-and-suspended-webpage');
    }
    const live = manager.registry.create('about:blank');
    tabs.push(live);
    await verify('live-webpage-with-internal-tabs');
    const scenarios = retentionAvailable ? 3 : 2;
    assert.equal(samples.length, scenarios * 4);
    fs.writeFileSync(path.join(output, 'theme-switch.json'), JSON.stringify({ passed: true, retentionAvailable, samples, errors }, null, 2));
    console.log('THEME_SWITCH ' + JSON.stringify({ passed: true, scenarios, samples: samples.length, errors: errors.length }));
  } catch (error) {
    fs.writeFileSync(path.join(output, 'theme-switch.json'), JSON.stringify({ passed: false, error: error.stack, samples, errors }, null, 2));
    throw error;
  } finally {
    process.removeListener('uncaughtException', onError);
    for (const tab of tabs) if (manager.tabs.has(tab.id)) manager.registry.close(tab);
  }
};
