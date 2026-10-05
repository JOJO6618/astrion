const kind=location.pathname.includes('quick')?'quick':'target';
document.body.className=kind;
document.addEventListener('pointerdown',event=>{
  const record={kind,x:event.clientX,y:event.clientY,dpr:devicePixelRatio};
  document.querySelector('output').textContent=JSON.stringify(record);
  fetch('/event',{method:'POST',body:JSON.stringify(record)}).catch(()=>{});
});
fetch('/event',{method:'POST',body:JSON.stringify({ready:kind,dpr:devicePixelRatio,origin:location.origin})}).catch(()=>{});
