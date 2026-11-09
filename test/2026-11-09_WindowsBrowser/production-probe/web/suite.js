(async () => {
  const checks = {}, events = {};
  const check = (name, value) => { if (!value) throw new Error('assertion failed: '+name); checks[name] = true; };
  const sleep = ms => new Promise(resolve=>setTimeout(resolve,ms));
  async function wait(predicate, name, timeout=12000) {const start=Date.now();while(Date.now()-start<timeout){const result=await predicate();if(result)return result;await sleep(50);}throw new Error('timeout: '+name);}
  let state, bridge, sequence=0;
  const scope = conversation => ({ mode:'agent',workspace:'synthetic-workspace',conversation });
  async function command(action,value,id) {const result=await bridge.command({lease:state.lease,action,value,id});state=result;return result;}
  async function credentials(action,value,id) {return bridge.credentials({lease:state.lease,action,value,id});}
  const geometry = (cuts=[], seq=++sequence) => bridge.layout({lease:state.lease,sequence:seq,visible:true,rect:{x:500,y:40,width:480,height:560},surfaceColor:'#22c55e',occlusions:cuts});
  async function event(name) {Object.assign(events,await (await fetch('/events')).json());return events[name];}
  try {
    bridge=await wait(()=>window.astrionBrowser,'production bridge installed');
    check('regionCapability',bridge.surfaceMode==='regions');
    state=await bridge.connect(scope('probe-owner'),{});
    bridge.onState(value=>{if(value.lease===state.lease && value.revision>=state.revision)state=value;});
    check('connected',!!state.lease && state.tabs.length>0);
    const initial=await credentials('list');
    let row=initial.records.find(r=>r.origin===location.origin && r.username==='synthetic-user');
    if(!row){const saved=await credentials('save',{origin:location.origin,username:'synthetic-user',password:'synthetic-password'});row=saved.records.find(r=>r.username==='synthetic-user');}
    check('passwordMetadata',!!row && !Object.hasOwn(row,'password'));
    check('passwordReveal',(await credentials('reveal',undefined,row.id)).password==='synthetic-password');
    const copy=await credentials('copy',undefined,row.id);check('copyNoSecret',!Object.hasOwn(copy,'password'));
    await command('new',location.origin+'/page');const pageId=state.activeId;
    geometry([{x:600,y:100,width:140,height:100}]);
    await wait(()=>state.tabs.find(t=>t.id===pageId && !t.pending && t.title==='Synthetic browser fixture'),'page committed');
    const page=await wait(()=>event('page'),'page observation');
    check('noPageBridge',page.bridge==='undefined' && page.tauri==='undefined');
    check('isolatedWorldInvisible',page.isolatedBinding==='undefined' && page.isolatedFill==='undefined');
    check('mainCookieIsolation',!document.cookie.includes('browser_synthetic='));
    const firstFill=await wait(()=>event('filled'),'production isolated autofill');check('isolatedAutofill',true);
    await command('reload',undefined,pageId);
    await wait(async()=>{const value=await event('filled');return value?.document!==firstFill.document;},'autofill after second navigation');check('autofillAfterReload',true);
    await command('zoom',125,pageId);check('pageZoom',state.tabs.find(t=>t.id===pageId).zoom===125);
    await command('zoom',100,pageId);
    await command('find',{text:'needle'},pageId);
    await wait(()=>state.tabs.find(t=>t.id===pageId)?.find?.matches===3,'native find count');check('nativeFind',true);
    await command('find',{text:''},pageId);
    await command('device',{width:390,height:844},pageId);
    await wait(async()=>{const size=await event('size');return size?.width===390;},'device metrics');check('deviceMetrics',true);
    await command('device',null,pageId);
    await fetch('/instruction/popup');
    await wait(()=>state.tabs.some(t=>t.url===location.origin+'/popup' && !t.pending),'native popup');
    const popup=await wait(()=>event('popup'),'popup observation');
    await wait(()=>event('openerRoundtrip'),'popup opener roundtrip');
    check('nativePopupOpener',popup.opener===true && popup.bridge==='undefined');
    await command('close');await command('select',undefined,pageId);
    geometry([{x:600,y:100,width:140,height:100}]);
    await fetch('/instruction/permission');
    const permission=await wait(()=>state.permissions[0] || event('permissionResult'),'native permission or OS denial');
    if(permission.id){await command('permission',false,permission.id);check('permissionDeny',state.permissions.length===0);}else checks.permissionDeniedByOS=true;
    const oldLease=state.lease;
    const ownerTwo=await bridge.connect(scope('second-owner'),{});state=ownerTwo;
    check('ownerTabsIsolated',!state.tabs.some(t=>t.id===pageId));
    let stale=false;try{await bridge.command({lease:oldLease,action:'new',value:location.origin+'/forbidden'});}catch{stale=true;}
    check('oldLeaseRejected',stale);
    state=await bridge.connect(scope('probe-owner'),{});
    check('ownerTabsRestored',state.tabs.some(t=>t.id===pageId));
    await command('select',undefined,pageId);
    geometry([{x:600,y:100,width:140,height:100}]);
    // The native harness measures the real Win32 region and WGC marker pixels here.
    await fetch('/measure');await sleep(1000);
    check('passwordPersisted',(await credentials('list')).records.some(r=>r.id===row.id));
    const sources=await credentials('sources');check('syntheticImportSource',sources.sources.some(s=>s.id==='Default'));
    const selection={source:'Default',selected:{passwords:true,cookies:true,history:true}};
    const cancelled=await credentials('preview',selection);await credentials('cancel-import');
    let refused=false;try{await credentials('import',{id:cancelled.preview.id,choices:{},replaceCookies:false});}catch{refused=true;}check('cancelledPreviewRejected',refused);
    const preview=(await credentials('preview',selection)).preview;
    check('importPreviewNoSecrets',!JSON.stringify(preview).includes('imported-password'));
    check('unsupportedEncryptionCounted',preview.stats.passwords.reasons.unsupported_encryption===1);
    const choices=Object.fromEntries(preview.conflicts.map(group=>[group.id,group.candidates[0].id]));
    const imported=(await credentials('import',{id:preview.id,choices,replaceCookies:true})).report;
    check('nativeImportCommit',imported.stats.passwords.imported===1 && imported.stats.cookies.imported===1);
    check('importedPasswordAvailable',(await credentials('list')).records.some(r=>r.origin==='https://import.example.test' && r.username==='imported-user'));
    const phase=await (await fetch('/phase')).text();
    const { runMigration } = await import('/migration-suite.js');
    await runMigration(credentials, check, phase);
    const localConfig = await (await fetch('/local-config')).json();
    await command('new', localConfig.url);
    const localId = state.activeId;
    const localPage = await wait(() => event('localPage'), 'local relative JS and CSS');
    check('localFileLoadsRelativeResources', localPage.color === 'rgb(12, 34, 56)');
    check('localFileHasNoTrustedBridge', localPage.bridge === 'undefined' && localPage.tauri === 'undefined' && localPage.binding === 'undefined');
    await wait(() => state.tabs.some(tab => tab.id === localId && tab.title === 'Local second page'), 'relative local link navigation');
    check('relativeLocalFileNavigation', true);
    await command('navigate', location.origin + '/page', localId);
    await wait(() => state.tabs.some(tab => tab.id === localId && tab.title === 'Synthetic browser fixture' && !tab.pending), 'local to web navigation');
    await command('back', undefined, localId);
    await wait(() => state.tabs.some(tab => tab.id === localId && tab.title === 'Local second page' && !tab.pending), 'back to previously opened local file');
    check('historyBackToLocalFile', true);
    await command('forward', undefined, localId);
    await wait(() => state.tabs.some(tab => tab.id === localId && tab.url.startsWith(location.origin) && !tab.pending), 'forward from local to web');
    check('historyForwardToWeb', true);
    await command('close', undefined, localId);
    await command('new', location.origin + '/remote-file-attack');
    const attackId = state.activeId;
    await sleep(800);
    check('remoteCannotNavigateOrPopupLocalFiles', state.tabs.some(tab => tab.id === attackId && tab.url.startsWith(location.origin)) && !state.tabs.some(tab => tab.url.startsWith('file:')));
    await command('close', undefined, attackId);
    await command('select', undefined, pageId);
    if(phase==='read'){
      check('cookiePersisted',!!await wait(()=>event('persisted'),'persistent cookie'));check('storagePersisted',page.stored==='persistent');
      for(const tab of [...state.tabs])await command('close',undefined,tab.id);
      await command('internal','clear');check('internalWithoutWebTabs',state.tabs.every(t=>t.section));
      await command('clear',{history:true,cookies:false,cache:false});check('historyOnlyClear',state.history.length===0);
      await command('clear',{history:false,cookies:true,cache:true});
      await command('new',location.origin+'/page');
      await wait(async()=>{const p=await event('page');return p.cookie==='' && p.stored===null;},'clear storage without web tabs');
      check('clearWithoutWebTabs',true);
    }
    await fetch('/result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ok:true,checks})});
    document.getElementById('status').textContent='PASS '+Object.keys(checks).length+' production checks';
  } catch(error) {
    await fetch('/result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ok:false,checks,error:String(error),state})});
    document.getElementById('status').textContent=String(error);
  }
})();
