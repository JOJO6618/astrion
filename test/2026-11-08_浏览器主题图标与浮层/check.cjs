exports.route = (request, response) => {
  if (request.url === '/site-icon.svg' || request.url === '/favicon.ico') {
    response.setHeader('Content-Type', 'image/svg+xml');
    response.end('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="#3178c6"/><path d="M3 8h10M8 3v10" stroke="white" stroke-width="2"/></svg>');
  } else if (request.url === '/page-with-icon') {
    response.setHeader('Content-Type', 'text/html');
    response.end('<title>Site icon fixture</title><link rel="icon" href="/site-icon.svg"><p>Searchable fixture content</p>');
  } else return false;
  return true;
};

exports.verify = async ({ run, inspect, manager, main, origin, check, delay, until }) => {
  if ((await inspect()).menu) await run('window.browserRegression.menu()');
  const tab = manager.main.active();
  for (const [theme, expected] of [['classic', '#faf9f5'], ['light', '#ffffff'], ['dark', '#1a1a1a']]) {
    await run(`window.browserRegression.theme(${JSON.stringify(theme)})`);
    await until(() => manager.surfaceColor === expected, `native ${theme} background follows CSS token`);
    check(`${theme} theme reaches native window background`, manager.window.getBackgroundColor().toLowerCase().endsWith(expected.slice(1)));
    await run(`window.browserRegression.navigate(${JSON.stringify(`${origin}/slow-page`)})`);
    await until(async () => (await inspect()).blank, `${theme} navigation waits`);
    check(`${theme} waiting region uses opaque theme backing`, !tab.view.getVisible() && !manager.main.stacking.transparent && manager.main.stacking.background === expected);
    await until(() => tab.view.getVisible() && !tab.pendingURL, `${theme} document loaded`);
  }

  const original = main.getBounds(), visibility = [];
  const nativeSetVisible = tab.view.setVisible.bind(tab.view);
  tab.view.setVisible = value => { visibility.push(value); nativeSetVisible(value); };
  try {
    for (const [width, height] of [[1300, 800], [1100, 720], [1250, 790]]) {
      main.setBounds({ ...original, width, height });
      manager.resize();
      check(`native resize ${width}x${height} updates immediately without hiding`, tab.view.getVisible() && tab.view.getBounds().height === height - Math.round(manager.main.geometry.y));
      await until(() => Math.abs((manager.main.geometryViewport?.width || 0) - width) <= 2, 'renderer acknowledges actual viewport');
    }
    check('continuous resizing never sends setVisible(false) to the website', visibility.length > 0 && visibility.every(Boolean));
  } finally {
    tab.view.setVisible = nativeSetVisible;
    main.setBounds(original); manager.resize();
  }
  await until(() => Math.abs((manager.main.geometryViewport?.width || 0) - original.width) <= 2, 'original viewport restored');

  const surface = () => run(`JSON.stringify((()=>{const r=document.querySelector('.nb-surface').getBoundingClientRect();return [r.x,r.y,r.width,r.height]})())`);
  const before = await surface();
  await run(`window.browserRegression.floating(true, 'Synthetic browser notification')`);
  await until(() => manager.main.overlayRaised && manager.main.focusFind, 'floating search and notice raised');
  check('find and notification do not change the website rectangle', await surface() === before);
  check('floating find receives native keyboard focus without hiding the page', main.webContents.isFocused() && tab.view.getVisible());
  check('floating find input is not forwarded into the website', await run(`(()=>{const input=document.querySelector('.nb-findbar input');return input.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,cancelable:true}))})()`));
  await run(`window.browserRegression.floating(false, '')`);
  await until(() => !manager.main.overlayRaised && !manager.main.focusFind, 'floating widgets closed');
  check('closing floating widgets preserves the same website rectangle', await surface() === before);

  await run(`window.browserRegression.navigate(${JSON.stringify(`${origin}/page-with-icon`)})`);
  await until(() => tab.favicon.endsWith('/site-icon.svg'), 'Chromium announces actual site favicon');
  await until(() => run(`(()=>{const i=document.querySelector('.nb-tab.active img.nb-site-icon');return !!i&&i.complete&&i.naturalWidth>0})()`), 'favicon decoded in actual tab component');
  check('tab displays the favicon announced by its actual document', await run(`document.querySelector('.nb-tab.active img').getAttribute('src').endsWith('/site-icon.svg')`));

  await run('window.browserRegression.closeTabs()');
  await until(() => manager.tabs.size === 0 && !(manager.main.active()), 'last tab actually destroyed');
  await until(async () => (await inspect()).newtab, 'empty browser presents new-tab content');
  await until(() => run(`Array.from(document.querySelectorAll('.nb-shortcut-icon img')).filter(i=>i.complete&&i.naturalWidth>0).length === 2`), 'new-tab site shortcut favicons decoded');
  check('new-tab website shortcuts use images rather than generic browser SVGs', await run(`document.querySelectorAll('.nb-shortcut-icon img').length === 2`));
  await run('window.browserRegression.history()');
  await until(() => run(`!!document.querySelector('.nb-row-main')`), 'history opens without a native tab');
  check('opening history with no tab produces no unknown-tab error', !(await inspect()).error);
  await run(`document.querySelector('.nb-row-main').click()`);
  await until(() => manager.main.active()?.view.getVisible(), 'history entry opens a new real tab');
  check('history navigation creates a tab when none exists', manager.tabs.size === 1 && !(await inspect()).error);
  await run('window.browserRegression.downloads()');
  await until(async () => (await inspect()).downloads, 'downloads reopened');
  check('completed download shows one size and no completed/total wording', await run(`Array.from(document.querySelectorAll('.nb-recent-downloads .nb-download-row small')).every(node=>/^\\s*[\\d.]+ (?:B|KB|MB)\\s*$/.test(node.textContent))`));
  await run('window.browserRegression.downloads()');
  await run('window.browserRegression.theme("classic")');
  await delay(30);
};
