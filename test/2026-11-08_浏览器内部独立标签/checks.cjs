exports.verify = async ({ manager, browserCommand, check, until, origin }) => {
  const original = manager.main.active(), contents = original.view.webContents;
  const sections = ['passwords', 'downloads', 'history', 'settings', 'import', 'clear'];
  const created = [];
  for (const section of sections) {
    await browserCommand({ action: 'internal', value: section });
    const tab = manager.main.active(); created.push(tab.id);
    check(`opens ${section} as an independent internal tab`, tab.section === section && !tab.view && manager.tabs.has(original.id));
  }
  const count = manager.tabs.size;
  await browserCommand({ action: 'internal', value: 'passwords' });
  const passwordTab = manager.main.active();
  check('reopening the same internal page selects its existing tab', manager.tabs.size === count && passwordTab.id === created[0]);
  check('internal snapshot carries a logical address and no webpage capabilities', manager.snapshot(manager.main).tabs.find(row => row.id === passwordTab.id).url === 'astrion://browser/passwords' && !manager.snapshot(manager.main).tabs.find(row => row.id === passwordTab.id).canGoBack);
  const saved = manager.main.profile.data.owners[manager.main.owner];
  check('internal pages participate in saved tab order', sections.every(section => saved.urls.includes(`astrion://browser/${section}`)));
  await browserCommand({ action: 'close', id: passwordTab.id });
  check('closing an internal tab leaves the original document alive', manager.tabs.has(original.id) && !contents.isDestroyed());
  await browserCommand({ action: 'internal', value: 'history' });
  const historyId = manager.main.active().id;
  await browserCommand({ action: 'navigate', value: `${origin}/internal-to-web` });
  await until(() => manager.main.active()?.id === historyId && !!manager.main.active()?.view && !manager.main.active().pendingURL, 'internal tab navigates to a webpage');
  check('typing a website replaces only the active internal tab', manager.main.active().id === historyId && !manager.main.active().section && manager.tabs.has(original.id));
  await until(() => manager.main.profile.data.history.find(row=>row.url===`${origin}/internal-to-web`)?.favicon?.endsWith('/site-icon.png'), 'website favicon saved with history');
  check('history retains the icon actually announced by the website', true);
  await browserCommand({ action: 'select', id: original.id });
  check('switching back reuses the exact original web contents', manager.main.active().view.webContents === contents && !contents.isDestroyed());
  // Keep the existing password/import fixture focused on its original page.
  for (const tab of [...manager.tabs.values()]) if (tab.id !== original.id) await browserCommand({ action: 'close', id: tab.id });
  await until(() => manager.tabs.size === 1, 'extra test tabs closed');
};
