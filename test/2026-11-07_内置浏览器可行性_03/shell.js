(() => {
  const $ = id => document.getElementById(id);
  let current, downloadsOpen = false, permissionId = null, layoutFrame = 0;
  const states = { progressing: '下载中', completed: '已完成', cancelled: '已取消', interrupted: '已中断' };
  async function send(request) {
    try {
      $('error').textContent = '';
      const state = await window.browserLab.command(request);
      if (state) render(state);
      return state;
    } catch (error) { $('error').textContent = String(error.message || error); }
  }
  function button(label, className, action) {
    const node = document.createElement('button'); node.type = 'button'; node.className = className;
    node.textContent = label; node.addEventListener('click', action); return node;
  }
  function geometry() {
    cancelAnimationFrame(layoutFrame);
    layoutFrame = requestAnimationFrame(() => {
      const { x, y, width, height } = $('viewport').getBoundingClientRect();
      if (width > 0 && height > 0) void send({ action: 'geometry', rect: { x, y, width, height } });
    });
  }
  function render(state) {
    const before = current;
    current = state;
    $('owner').textContent = `对话 ${state.owner.conversation} · ${state.owner.actor}`;
    $('conversation-a').setAttribute('aria-pressed', String(state.owner.conversation === 'A'));
    $('conversation-b').setAttribute('aria-pressed', String(state.owner.conversation === 'B'));
    $('workspace').classList.toggle('collapsed', state.collapsed);
    $('collapse').textContent = state.collapsed ? '展开浏览器' : '收起浏览器';
    $('collapse').setAttribute('aria-expanded', String(!state.collapsed));
    $('tabs').replaceChildren(...state.tabs.map(tab => {
      const node = document.createElement('div'); node.className = 'tab'; node.setAttribute('role', 'tab');
      node.setAttribute('aria-selected', String(tab.id === state.active));
      const select = button(tab.loading ? '正在加载…' : tab.title, 'tab-select', () => send({ action: 'select', id: tab.id }));
      select.setAttribute('aria-label', `选择标签 ${tab.title}`);
      const close = button('×', 'tab-close', () => send({ action: 'close', id: tab.id }));
      close.setAttribute('aria-label', `关闭标签 ${tab.title}`); node.append(select, close); return node;
    }));
    const active = state.tabs.find(tab => tab.id === state.active);
    if (document.activeElement !== $('address')) $('address').value = active?.url || '';
    $('back').disabled = !active?.canGoBack; $('forward').disabled = !active?.canGoForward;
    $('page-status').textContent = active?.error ? '页面加载失败' : active?.loading ? '正在加载' : active ? '已就绪' : '没有打开的网页';
    $('native-status').textContent = `${state.tabs.length} 个标签`;
    $('events').replaceChildren(...state.events.map(event => {
      const li = document.createElement('li'); const time = document.createElement('time');
      time.textContent = event.time; const text = document.createElement('span'); text.textContent = event.message;
      li.append(time, text); return li;
    }));
    $('downloads-panel').hidden = !downloadsOpen;
    $('download-list').replaceChildren(...state.downloads.map(item => {
      const row = document.createElement('div'); row.className = 'download-row';
      const name = document.createElement('span'); name.textContent = item.filename;
      const bytes = item.total > 0 ? `${item.received} / ${item.total} B` : `${item.received} B`;
      const status = document.createElement('small'); status.textContent = `${states[item.state] || item.state} · ${bytes}`;
      row.append(name, status);
      if (item.state === 'progressing') row.append(button('取消下载', '', () => send({ action: 'cancel-download', id: item.id })));
      return row;
    }));
    if (!state.downloads.length) $('download-list').textContent = '暂无下载';
    const permission = state.permissions[0];
    $('permission-panel').hidden = !permission;
    $('permission-origin').textContent = permission?.origin || '';
    if (permission && permissionId !== permission.id) $('permission-deny').focus();
    permissionId = permission?.id || null;
    if (before && before.owner.conversation !== state.owner.conversation) downloadsOpen = false;
    if (!before || before.collapsed !== state.collapsed) geometry();
  }
  for (const action of ['back', 'forward', 'reload']) $(action).onclick = () => send({ action });
  $('new-tab').onclick = () => send({ action: 'new' });
  $('collapse').onclick = () => send({ action: 'collapse' });
  $('navigation').onsubmit = event => { event.preventDefault(); $('address').blur(); void send({ action: 'navigate', url: $('address').value }); };
  for (const node of document.querySelectorAll('[data-conversation]')) node.onclick = () => {
    downloadsOpen = false; return send({ action: 'conversation', conversation: node.dataset.conversation });
  };
  for (const node of document.querySelectorAll('button[data-theme]')) node.onclick = () => {
    document.documentElement.dataset.theme = node.dataset.theme; geometry();
  };
  function downloads(open) { downloadsOpen = open; return send({ action: 'downloads', open }); }
  $('downloads-toggle').onclick = () => downloads(!downloadsOpen);
  $('downloads-close').onclick = () => downloads(false);
  $('permission-deny').onclick = () => send({ action: 'permission', id: permissionId, allowed: false });
  $('permission-allow').onclick = () => send({ action: 'permission', id: permissionId, allowed: true });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && downloadsOpen) void downloads(false);
    if (event.key === 'Escape' && permissionId) void send({ action: 'permission', id: permissionId, allowed: false });
  });
  const divider = $('divider'); let dragging = false;
  const setWidth = width => {
    const value = Math.max(260, Math.min(window.innerWidth - 360, width));
    $('workspace').style.setProperty('--left-width', `${value}px`); geometry();
  };
  divider.addEventListener('pointerdown', event => {
    dragging = true; divider.setPointerCapture(event.pointerId); void send({ action: 'dragging', open: true });
  });
  divider.addEventListener('pointermove', event => { if (dragging) setWidth(event.clientX); });
  const stopDrag = () => { if (dragging) { dragging = false; void send({ action: 'dragging', open: false }); } };
  divider.addEventListener('pointerup', stopDrag); divider.addEventListener('pointercancel', stopDrag);
  divider.addEventListener('keydown', event => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault(); setWidth(divider.getBoundingClientRect().x + (event.key === 'ArrowLeft' ? -24 : 24));
    }
  });
  new ResizeObserver(geometry).observe($('viewport'));
  window.browserLab.onState(render);
  void send({ action: 'state' }).then(geometry);
})();
