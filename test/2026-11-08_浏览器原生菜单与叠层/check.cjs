const fs = require('node:fs');
const payload = 'Astrion isolated download fixture\n';

exports.route = (request, response) => {
  if (request.url === '/slow-page') {
    setTimeout(() => {
      response.setHeader('Content-Type', 'text/html');
      response.end('<title>Delayed document</title><p>Loaded</p>');
    }, 600);
  } else if (request.url === '/download-target') {
    response.setHeader('Content-Type', 'application/octet-stream');
    response.setHeader('Content-Disposition', 'attachment; filename="fixture-download.txt"');
    response.setHeader('Content-Length', Buffer.byteLength(payload));
    response.write(payload.slice(0, 5));
    setTimeout(() => response.end(payload.slice(5)), 250);
  } else return false;
  return true;
};

exports.verify = async ({ run, inspect, manager, window, main, origin, check, delay, until }) => {
  const tab = manager.active(), page = tab.view.webContents;
  await page.executeJavaScript('window.liveDocumentToken="same-live-document";window.liveTicks=0;window.liveTimer=setInterval(()=>window.liveTicks++,20)');
  await delay(80);
  const ticks = await page.executeJavaScript('window.liveTicks');
  check('main renderer backing is transparent while page is visible', manager.stacking.transparent);
  check('DOM roots and page surface expose the live native page', await run(`['html','body','#app','.native-browser-panel','.nb-surface'].every(selector => getComputedStyle(document.querySelector(selector)).backgroundColor === 'rgba(0, 0, 0, 0)')`));
  check('outside-page underlay uses a real opaque surface token', await run(`getComputedStyle(document.querySelector('.nb-native-underlay span')).backgroundColor !== 'rgba(0, 0, 0, 0)'`));
  await delay(80);
  check('website continues running behind the Vue menu', await page.executeJavaScript('window.liveTicks') > ticks);
  await run(`document.querySelector('.nb-surface').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))`);
  await until(() => window.contentView.children.at(-1) === tab.view, 'clicking outside menu returns page to top');
  check('closing menu retains transparent backing and the same native document', manager.stacking.transparent && await page.executeJavaScript('window.liveDocumentToken') === 'same-live-document');

  const base = { frame: page.mainFrame, x: 5, y: 5, editFlags: { canCopy: true } };
  const build = params => manager.contextMenu.build(tab, { ...base, ...params });
  const link = build({ linkURL: `${origin}/fixture-page` });
  check('native link menu offers open, copy and download', ['openLinkNewTab','copyLink','saveLink'].every(id => !!link.menu.getMenuItemById(id)));
  check('native image menu offers image and address actions', !!build({ mediaType: 'image', hasImageContents: true, srcURL: `${origin}/image.png` }).menu.getMenuItemById('saveImage'));
  const edit = build({ isEditable: true, editFlags: { canCopy: true, canPaste: false, canUndo: true } });
  check('native editable menu honors Chromium edit capabilities', edit.menu.getMenuItemById('undo').enabled && !edit.menu.getMenuItemById('paste').enabled);
  check('native selection menu offers copy and search', !!build({ selectionText: 'fixture text' }).menu.getMenuItemById('searchSelection'));
  const lease = manager.lease;
  manager.lease = 'retired-fixture-lease';
  check('old native menu rejects a changed lease', !link.valid());
  manager.lease = lease;
  const selected = manager.selected.get(manager.owner);
  manager.selected.set(manager.owner, 'different-fixture-tab');
  check('old native menu rejects another active tab', !link.valid());
  manager.selected.set(manager.owner, selected);
  const gone = build({ frame: { get url() { throw new Error('frame disposed'); } } });
  check('destroyed frame invalidates menu without throwing', !gone.valid());

  check('HTTPS root address omits protocol and trailing slash', await run(`window.browserRegression.address('https://github.com/') === 'github.com'`));
  check('address omits trailing path slash while preserving query and HTTP identity', await run(`window.browserRegression.address('https://github.com/a/?q=1#b') === 'github.com/a?q=1#b' && window.browserRegression.address('http://localhost:8000/') === 'http://localhost:8000'`));
  await run(`window.browserRegression.navigate(${JSON.stringify(`${origin}/slow-page`)})`);
  await until(async () => (await inspect()).blank, 'blank while navigation waits');
  const pending = await inspect();
  check('navigation waiting shows blank instead of the new-tab content or old page', !pending.newtab && !tab.view.getVisible());
  await until(() => page.getTitle() === 'Delayed document' && tab.view.getVisible(), 'delayed document becomes visible');
  await page.executeJavaScript('clearInterval(window.liveTimer)');

  // A real target=_blank attachment must not leave an empty tab or cancel its download.
  const before = manager.tabs.size;
  await page.executeJavaScript(`{ const a=document.createElement('a');a.href=${JSON.stringify(`${origin}/download-target`)};a.target='_blank';document.body.append(a);a.click(); }`);
  await until(() => manager.profile.data.downloads.length > 0, 'download starts');
  await until(() => manager.profile.data.downloads[0].state === 'completed', 'download completes');
  await until(() => manager.tabs.size === before && manager.active() === tab && manager.downloadViews.size === 0, 'download placeholder retired');
  const row = manager.profile.data.downloads[0];
  check('download placeholder closes and restores its originating tab', manager.active() === tab && !tab.retiredDownload);
  check('download survives placeholder cleanup with exact file content', fs.readFileSync(row.path, 'utf8') === payload);
  const downloadsBefore = manager.profile.data.downloads.length;
  await page.executeJavaScript(`{ const a=document.createElement('a');a.href=${JSON.stringify(`${origin}/download-target`)};document.body.append(a);a.click(); }`);
  await until(() => manager.profile.data.downloads.length > downloadsBefore && manager.profile.data.downloads[0].state === 'completed', 'same-tab download completes');
  await until(() => !tab.pendingURL && tab.view.getVisible(), 'same-tab download restores its page');
  check('same-tab attachment restores the current document instead of leaving blank pending content', manager.tabs.size === before && manager.active() === tab && page.getTitle() === 'Delayed document');
  check('download button is immediately left of the more button', await run(`document.querySelector('.nb-download-button').nextElementSibling.matches('.nb-more')`));
  await run('window.browserRegression.downloads()');
  await until(async () => (await inspect()).downloads && manager.overlayRaised, 'recent downloads popup');
  check('recent downloads popup renders the actual completed record over the live page', tab.view.getVisible() && await run(`document.querySelector('.nb-recent-downloads').textContent.includes('fixture-download.txt')`));
  check('recent downloads popup has no shadow', await run(`getComputedStyle(document.querySelector('.nb-recent-downloads')).boxShadow === 'none'`));
  await run('window.browserRegression.downloads()');
  await until(() => !manager.overlayRaised, 'download popup closes');
  await run(`window.browserRegression.navigate(${JSON.stringify(`${origin}/fixture-page`)})`);
  await until(() => page.getTitle() === 'Manual browser without a model' && tab.view.getVisible(), 'original fixture restored');
  await run('window.browserRegression.menu()');
  await until(() => manager.overlayRaised, 'menu restored for polling test');
};
