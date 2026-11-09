(() => {
  const kind = location.pathname === '/popup' ? 'popup' : 'page';
  const send = (name, value) => fetch('/event', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ kind:name, value }) });
  send(kind, { cookie:document.cookie, stored:localStorage.getItem('synthetic'), bridge:typeof window.astrionBrowser, tauri:typeof window.__TAURI_INTERNALS__, isolatedBinding:typeof window.__astrionPasswordSignal, isolatedFill:typeof window.__astrionFill, opener:!!window.opener });
  if (kind === 'popup') {
    window.opener?.postMessage('synthetic-popup-ready', location.origin);
    return;
  }
  if (document.cookie.includes('browser_synthetic=synthetic')) send('persisted', {ok:true});
  document.cookie = 'browser_synthetic=synthetic; Max-Age=3600; SameSite=Lax; path=/';
  localStorage.setItem('synthetic', 'persistent');
  addEventListener('message', event => {
    if (event.origin === location.origin && event.data === 'synthetic-popup-ready') send('openerRoundtrip', {ok:true});
    if (event.origin === location.origin && event.data === 'open-popup') window.open('/popup','synthetic-popup');
    if (event.origin === location.origin && event.data === 'permission') navigator.geolocation.getCurrentPosition(()=>send('permissionResult',{ok:true}),()=>send('permissionResult',{ok:true}));
  });
  let filled = false;
  const inspect = () => {
    if (!filled && document.getElementById('user').value === 'synthetic-user' && document.getElementById('secret').value === 'synthetic-password') {filled=true;send('filled',{ok:true,document:performance.timeOrigin});}
  };
  document.addEventListener('input', inspect);
  setInterval(inspect,100);
  let instruction = '';
  setInterval(async()=>{
    const value = await (await fetch('/instruction')).text();
    if (value === instruction) return;
    instruction = value;
    if (value === 'popup') window.open('/popup','synthetic-popup');
    if (value === 'permission') navigator.geolocation.getCurrentPosition(()=>send('permissionResult',{ok:true}),()=>send('permissionResult',{ok:true}));
  },100);
  addEventListener('resize',()=>send('size',{width:innerWidth,height:innerHeight}));
})();
