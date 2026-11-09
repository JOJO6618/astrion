use serde_json::{json, Value};
use std::{io::{Read, Write}, net::{TcpListener,TcpStream}, sync::{Arc,Mutex,atomic::{AtomicBool,Ordering}}, time::Duration};
pub struct Fixture {pub origin:String,pub state:Arc<Mutex<Value>>,pub done:Arc<AtomicBool>,pub measure:Arc<AtomicBool>}
pub fn start(phase:&str,port:u16)->Fixture {
    let listener=TcpListener::bind(("127.0.0.1",port)).unwrap();listener.set_nonblocking(true).unwrap();
    let origin=format!("http://127.0.0.1:{}",listener.local_addr().unwrap().port());
    let root=std::path::PathBuf::from(std::env::var_os("ASTRION_BROWSER_PRODUCTION_PROBE_ROOT").unwrap()).join("local 网页");
    std::fs::create_dir_all(&root).unwrap();
    std::fs::write(root.join("index.html"),format!("<!doctype html><meta charset=utf-8><title>Local waiting</title><link rel=stylesheet href=local.css><h1>Local fixture</h1><a id=next href=second.html>Next</a><script>globalThis.fixtureOrigin={};</script><script src=local.js></script>",serde_json::to_string(&origin).unwrap())).unwrap();
    std::fs::write(root.join("local.css"),"h1{color:rgb(12,34,56)}").unwrap();
    std::fs::write(root.join("local.js"),"document.title='Local relative JS';fetch(fixtureOrigin+'/event',{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify({kind:'localPage',value:{bridge:typeof astrionBrowser,tauri:typeof __TAURI__,binding:typeof __astrionPasswordSignal,color:getComputedStyle(document.querySelector('h1')).color}})});setTimeout(()=>document.getElementById('next').click(),700);").unwrap();
    std::fs::write(root.join("second.html"),"<!doctype html><title>Local second page</title><p>Relative navigation</p>").unwrap();
    let local_url=tauri::Url::from_file_path(root.join("index.html")).unwrap().to_string();
    let state=Arc::new(Mutex::new(json!({"events":{},"result":null,"instruction":"","localURL":local_url})));
    let done=Arc::new(AtomicBool::new(false));let measure=Arc::new(AtomicBool::new(false));
    let shared=state.clone();let finished=done.clone();let measured=measure.clone();let phase=phase.to_owned();
    std::thread::spawn(move||{
        while !finished.load(Ordering::Relaxed){
            let Ok((stream,_))=listener.accept()else{std::thread::sleep(Duration::from_millis(5));continue};
            let state=shared.clone();let done=finished.clone();let measure=measured.clone();let phase=phase.clone();
            // Chromium can preconnect idle sockets. One connection must not block other views.
            std::thread::spawn(move||handle(stream,state,done,measure,&phase));
        }
    });Fixture{origin,state,done,measure}
}
fn handle(mut stream:TcpStream,shared:Arc<Mutex<Value>>,finished:Arc<AtomicBool>,measured:Arc<AtomicBool>,phase:&str){
    let _=stream.set_read_timeout(Some(Duration::from_secs(10)));let mut bytes=vec![];let mut chunk=[0;8192];let mut body_start=0;let mut length=0;
    while bytes.len()<2*1024*1024{
        let n=stream.read(&mut chunk).unwrap_or(0);if n==0{return;}bytes.extend_from_slice(&chunk[..n]);
        if body_start==0{if let Some(pos)=bytes.windows(4).position(|b|b==b"\r\n\r\n"){body_start=pos+4;let head=String::from_utf8_lossy(&bytes[..pos]);length=head.lines().find_map(|line|line.to_lowercase().strip_prefix("content-length:").and_then(|s|s.trim().parse::<usize>().ok())).unwrap_or(0);}}
        if body_start>0 && bytes.len()>=body_start+length{break;}
    }
    if body_start==0{return;}
    let raw=String::from_utf8_lossy(&bytes);let path=raw.split_whitespace().nth(1).unwrap_or("/");
    let body=bytes.get(body_start..body_start+length).unwrap_or(&[]);
    let (mime,body)=match path{
        "/api/session-status"=>("application/json",json!({"success":true,"session":{"username":"synthetic-probe-user","logged_in":true}}).to_string()),
        "/new" if phase=="ai"=>("text/html",include_str!("../web/ai-main.html").into()),
        "/new"=>("text/html",include_str!("../web/index.html").into()),
        "/ai"=>("text/html",include_str!("../web/ai.html").into()),
        "/ai/data"=>("application/json",json!({"synthetic":true}).to_string()),
        "/suite.js"=>("text/javascript",include_str!("../web/suite.js").into()),
        "/migration-suite.js"=>("text/javascript",include_str!("../web/migration-suite.js").into()),
        "/page.js"=>("text/javascript",include_str!("../web/page.js").into()),
        "/fixture.css"=>("text/css",include_str!("../web/fixture.css").into()),
        "/page"|"/popup"=>("text/html",include_str!("../web/page.html").into()),
        "/events"=>("application/json",shared.lock().unwrap()["events"].to_string()),
        "/event"=>{if let Ok(value)=serde_json::from_slice::<Value>(body){let key=value["kind"].as_str().unwrap_or("unknown").to_owned();shared.lock().unwrap()["events"][key]=value["value"].clone();}("text/plain","ok".into())},
        "/result"=>{shared.lock().unwrap()["result"]=serde_json::from_slice(body).unwrap_or(json!({"ok":false}));finished.store(true,Ordering::Relaxed);("text/plain","ok".into())},
        "/phase"=>("text/plain",phase.to_owned()),
        "/local-config"=>("application/json",json!({"url":shared.lock().unwrap()["localURL"]}).to_string()),
        "/remote-file-attack"=>{let url=shared.lock().unwrap()["localURL"].to_string();("text/html",format!("<!doctype html><title>Remote attack fixture</title><script>setTimeout(()=>{{window.open({url});location.href={url};}},100);</script>"))},
        "/instruction"=>("text/plain",shared.lock().unwrap()["instruction"].as_str().unwrap_or("").into()),
        "/instruction/popup"=>{shared.lock().unwrap()["instruction"]=json!("popup");("text/plain","ok".into())},
        "/instruction/permission"=>{shared.lock().unwrap()["instruction"]=json!("permission");("text/plain","ok".into())},
        "/measure"=>{measured.store(true,Ordering::Relaxed);("text/plain","ok".into())},
        _=>("text/plain","not found".into()),
    };
    let response=format!("HTTP/1.1 200 OK\r\nContent-Type: {mime}; charset=utf-8\r\nContent-Length: {}\r\nCache-Control: no-store\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n{body}",body.len());let _=stream.write_all(response.as_bytes());
}
