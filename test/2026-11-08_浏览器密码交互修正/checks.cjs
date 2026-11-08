const http = require('node:http');
exports.route = (request, response) => {
  const url = new URL(request.url, 'http://fixture.test');
  if (!['/spa-login', '/external-login'].includes(url.pathname)) return false;
  response.setHeader('Content-Type', 'text/html');
  const fields = '<input name="username" autocomplete="username"><input name="password" type="password" autocomplete="current-password">';
  if (url.pathname === '/spa-login') response.end(`<form>${fields}<button type="button" id="login">Sign in</button><div role="button" tabindex="0" id="custom">Sign in</div></form><script>for(const node of document.querySelectorAll('button,[role=button]'))node.onclick=()=>fetch('/api/login').then(()=>history.pushState({},'','/spa-home'))</script>`);
  else response.end(`<form method="post" action="${url.searchParams.get('target')}">${fields}<button>Sign in</button></form>`);
  return true;
};
exports.verify = async ({ manager, command, origin, check, until }) => {
  const tab = manager.registry.create(`${origin}/spa-login`), wc = tab.view.webContents;
  await until(() => !tab.pendingURL && !wc.isLoading(), 'SPA fixture loaded'); wc.focus();
  async function fill(username) {
    await wc.executeJavaScript(`document.querySelector('[name=username]').value=${JSON.stringify(username)};document.querySelector('[name=password]').value='synthetic-new-login';`);
  }
  async function click(selector) {
    const point = await wc.executeJavaScript(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}})()`);
    wc.sendInputEvent({type:'mouseDown',button:'left',clickCount:1,...point}); wc.sendInputEvent({type:'mouseUp',button:'left',clickCount:1,...point});
  }
  for (const [selector, username] of [['#login','new-spa-button'],['#custom','new-spa-role']]) {
    await fill(username); await click(selector);
    await until(()=>manager.credentials.snapshot().prompt?.username===username, 'SPA save prompt');
    const prompt=manager.credentials.snapshot().prompt;
    await command({action:'accept',id:prompt.id});
    check(`new login saves from ${selector}`,manager.credentials.vault(manager.profile).find(origin,username)?.password==='synthetic-new-login');
  }
  const auth = http.createServer((request,response)=>{
    request.resume();
    if(request.url==='/session'){response.writeHead(302,{Location:'/welcome'});response.end();}
    else {response.setHeader('Content-Type','text/html');response.end('<h1>Signed in</h1>');}
  });
  await new Promise(resolve=>auth.listen(0,'127.0.0.1',resolve));
  const target=`http://127.0.0.1:${auth.address().port}`;
  try {
    await wc.loadURL(`${origin}/external-login?target=${encodeURIComponent(target+'/session')}`);
    await until(()=>!tab.pendingURL && !wc.isLoading(),'cross-origin login form loaded'); wc.focus();
    await fill('new-redirect-login'); await click('button');
    await until(()=>wc.getURL()===target+'/welcome' && !wc.isLoading(),'login redirect completed');
    await until(()=>manager.credentials.snapshot().prompt?.username==='new-redirect-login','save prompt survives redirect');
    const prompt=manager.credentials.snapshot().prompt;
    check('redirected login retains the original password site',prompt.origin===origin);
    await command({action:'accept',id:prompt.id});
    check('new login can be saved after cross-origin navigation',manager.credentials.vault(manager.profile).find(origin,'new-redirect-login')?.password==='synthetic-new-login');
  } finally { auth.closeAllConnections();auth.close(); }
};
