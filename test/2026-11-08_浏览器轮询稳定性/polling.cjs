const fs = require('node:fs');
const path = require('node:path');

module.exports = async function pollingRegression({ run, inspect, manager, calls, check, delay, until, output }) {
  const observations = {};
  const page = manager.active().view.webContents;
  let navigations = 0;
  const onNavigate = (_event, _url, _sameDocument, isMainFrame) => { if (isMainFrame) navigations++; };
  page.on('did-start-navigation', onNavigate);
  const token = 'persistent-native-document';
  await page.executeJavaScript(`window.stabilityToken=${JSON.stringify(token)}`);
  await run("document.documentElement.style.setProperty('--text-primary', 'rgb(50, 50, 50)')");
  const originalLease = (await inspect()).lease;
  const identityRequests = () => calls.filter(url => url === '/api/session-status').length;
  const beforeRequests = identityRequests();
  const navigationSamples = [];
  // Match the production navigation poll interval, with fresh equivalent snapshots.
  for (let i = 0; i < 8; i++) {
    await run('window.browserRegression.pollNavigation()');
    await delay(500);
    navigationSamples.push(await inspect());
  }
  observations['same-owner polls do not reconnect or replace the lease'] =
    identityRequests() === beforeRequests && navigationSamples.every(state => state.lease === originalLease);
  observations['browser menu stays open through repeated desktop polls'] = navigationSamples.every(state => state.menu);

  await until(async () => !!(await inspect()).lease, 'connection settled for native state samples');
  if (!(await inspect()).menu) await run('window.browserRegression.menu()');
  await run("window.browserRegression.editAddress('unfinished-address-draft')");
  await run('window.browserRegression.findDraft()');
  const stateSamples = [];
  for (let i = 0; i < 12; i++) {
    manager.publish();
    await delay(45);
    stateSamples.push(await inspect());
  }
  observations['same-page state publications do not close the menu'] = stateSamples.every(state => state.menu);
  observations['same-page state publications preserve address editing'] = stateSamples.every(state => state.addressValue === 'unfinished-address-draft');
  observations['same-page state publications preserve find draft and visibility'] = stateSamples.every(state => state.findOpen && state.findText === 'steady-find-draft');
  if (!(await inspect()).menu) await run('window.browserRegression.menu()');
  observations['more menu has no box shadow'] = (await inspect()).menuShadow === 'none';
  observations['same native document survives all polling and menu changes'] = await page.executeJavaScript('window.stabilityToken') === token && navigations === 0;
  await page.executeJavaScript("history.pushState({}, '', '/fixture-page#real-navigation')");
  await until(async () => (await inspect()).activeUrl.endsWith('#real-navigation'), 'actual URL change projected');
  const navigated = await inspect();
  observations['real URL changes still close menus and reset page-specific drafts'] = !navigated.menu && !navigated.findOpen && navigated.findText === '' && navigated.addressValue.endsWith('#real-navigation');
  page.removeListener('did-start-navigation', onNavigate);
  fs.writeFileSync(path.join(output, 'polling-observations.json'), JSON.stringify({ observations, identityRequests: identityRequests() - beforeRequests, navigationSamples: navigationSamples.map(state => ({ lease: state.lease, menu: state.menu })) }, null, 2));
  for (const [name, passed] of Object.entries(observations)) check(name, passed);
};
