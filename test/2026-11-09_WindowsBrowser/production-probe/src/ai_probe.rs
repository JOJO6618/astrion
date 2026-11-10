use crate::{browser,fixture};
use serde_json::{json,Value};
use std::{path::PathBuf,sync::{Arc,Mutex,atomic::Ordering},time::{Duration,Instant}};
use tauri::{Manager,WebviewWindowBuilder,WebviewUrl};
struct Harness {client:reqwest::blocking::Client,url:String,token:String,origin:String,root:PathBuf,seq:u64,checks:Vec<String>}
impl Harness {
    fn payload(&mut self,op:&str,args:Value)->Value {self.seq+=1;json!({"call_id":format!("ai-test-{}",self.seq),"op":op,"args":args,"identity":{"username":"synthetic-probe-user","workspace_id":"synthetic-workspace","conversation_id":"conv_ai"},"any_site":true,"epoch":0,"reveal":false,"download_dir":self.root.join("downloads")})}
    fn send(&self,payload:&Value)->Result<Value,String> {self.client.post(format!("{}/browser/call",self.url)).header("X-Astrion-Browser-Token",&self.token).json(payload).send().and_then(|r|r.error_for_status()).and_then(|r|r.json()).map_err(|e|e.to_string())}
    fn raw(&mut self,op:&str,args:Value)->Result<Value,String> {let payload=self.payload(op,args);self.send(&payload)}
    fn call(&mut self,op:&str,args:Value)->Result<Value,String> {let result=self.raw(op,args)?;if result["success"]!=true {return Err(format!("{op}: {} {}",result["code"],result["error"]));}Ok(result)}
    fn check(&mut self,name:&str,condition:bool)->Result<(),String> {if !condition {return Err(name.into());}self.checks.push(name.into());println!("PASS {name}");Ok(())}
    fn snapshot(&mut self)->Result<Value,String> {self.call("snapshot",json!({}))}
    fn target(&mut self,label:&str)->Result<String,String> {let snap=self.snapshot()?;let text=snap["snapshot"]["text"].as_str().unwrap_or("");for line in text.lines() {if line.contains(&format!("\"{label}\"")) {if let Some(after)=line.split("[ref=").nth(1) {return Ok(after.split(']').next().unwrap().to_owned());}}}Err(format!("missing ref for {label}: {}",text.chars().take(2000).collect::<String>()))}
    fn tests(&mut self)->Result<(),String> {
        let status=self.client.post(format!("{}/browser/call",self.url)).json(&json!({})).send().map_err(|e|e.to_string())?.status().as_u16();self.check("missingTokenRejected",status==403)?;
        let url=format!("{}/ai",self.origin);let nav=self.call("navigate",json!({"url":url}))?;
        self.check("navigateAndSnapshot",nav["snapshot"]["text"].as_str().unwrap_or("").contains("AI fixture"))?;
        let found=self.call("find",json!({"text":"Increment"}))?;self.check("find",found["total"].as_u64().unwrap_or(0)>0)?;
        let target=self.target("Increment")?;self.call("click",json!({"target":target}))?;
        let value=self.call("evaluate",json!({"function":"()=>document.getElementById('status').textContent"}))?;self.check("realClick",value["result"]=="Count: 1")?;
        let name=self.target("Name")?;self.call("type",json!({"target":name,"text":"Native input"}))?;
        let value=self.call("evaluate",json!({"function":"()=>document.getElementById('name').value"}))?;self.check("realInput",value["result"]=="Native input")?;
        let name=self.target("Name")?;let checkbox=self.target("Subscribe")?;let color=self.target("Color")?;let level=self.target("Level")?;
        self.call("fill_form",json!({"fields":[{"target":name,"type":"textbox","value":"Form input"},{"target":checkbox,"type":"checkbox","value":true},{"target":color,"type":"combobox","value":"red"},{"target":level,"type":"slider","value":"7"}]}))?;
        let value=self.call("evaluate",json!({"function":"()=>[document.getElementById('name').value,document.getElementById('subscribe').checked,document.getElementById('level').value].join('|')"}))?;self.check("fillForm",value["result"]=="Form input|true|7")?;
        let color=self.target("Color")?;self.call("select_option",json!({"target":color,"values":["blue"]}))?;self.check("selectOption",true)?;
        let target=self.target("Increment")?;self.call("hover",json!({"target":target}))?;self.check("hover",true)?;
        let name=self.target("Name")?;self.call("click",json!({"target":name}))?;self.call("press_key",json!({"key":"ControlOrMeta+A"}))?;self.call("press_key",json!({"key":"Z"}))?;
        let value=self.call("evaluate",json!({"function":"()=>document.getElementById('name').value"}))?;self.check("windowsControlOrMeta",value["result"]=="Z")?;
        let source=self.target("Drag source")?;let end=self.target("Drop target")?;self.call("drag",json!({"start_target":source,"end_target":end}))?;self.check("drag",true)?;
        self.call("evaluate",json!({"function":"()=>{const e=document.getElementById('drop');e.textContent='Drop target';return true;}"}))?;
        let target=self.target("Drop target")?;self.call("drop",json!({"target":target,"data":{"text/plain":"synthetic-drop"}}))?;
        let value=self.call("evaluate",json!({"function":"()=>document.getElementById('drop').textContent"}))?;self.check("dataDrop",value["result"]=="Dropped: synthetic-drop")?;
        self.call("scroll",json!({"direction":"down","amount":650}))?;let value=self.call("evaluate",json!({"function":"()=>scrollY>0"}))?;self.check("scroll",value["result"]=="true")?;
        self.call("wait_for",json!({"text":"Bottom marker","timeout":2}))?;self.check("waitFor",true)?;
        let shot=self.call("take_screenshot",json!({}))?;self.check("hiddenScreenshot",shot["image_base64"].as_str().is_some_and(|s|s.starts_with("iVBOR") && s.len()>1000))?;
        self.call("emulate_media",json!({"color_scheme":"dark","reduced_motion":"reduce"}))?;
        let value=self.call("evaluate",json!({"function":"()=>matchMedia('(prefers-color-scheme:dark)').matches"}))?;self.check("emulateMedia",value["result"]=="true")?;
        self.call("evaluate",json!({"function":"()=>{console.warn('synthetic-console');return fetch('/ai/data').then(r=>r.json());}"}))?;
        let messages=self.call("console_messages",json!({"level":"debug"}))?;self.check("consoleMessages",messages["messages"].as_array().is_some_and(|rows|rows.iter().any(|r|r["message"].as_str().unwrap_or("").contains("synthetic-console"))))?;
        let requests=self.call("network_requests",json!({"include_static":true}))?;let request=requests["requests"].as_array().and_then(|rows|rows.iter().find(|r|r["url"].as_str().unwrap_or("").ends_with("/ai/data"))).ok_or("network request not recorded")?;
        self.call("network_request",json!({"id":request["id"]}))?;self.check("networkRequestsAndDetail",true)?;
        self.call("evaluate",json!({"function":"()=>{setTimeout(()=>confirm('synthetic-confirm'),100);return 'scheduled';}"}))?;
        let dialog=self.call("wait_for",json!({"time":0.3}))?;self.check("dialogCaptured",dialog["dialog"]["type"]=="confirm")?;
        self.call("handle_dialog",json!({"accept":false}))?;let value=self.call("evaluate",json!({"function":"()=>document.getElementById('status').textContent"}))?;self.check("dialogDismissed",value["result"].as_str().is_some_and(|s|s.starts_with("Count: ")))?;
        let file=self.root.join("synthetic-upload.txt");std::fs::write(&file,"synthetic upload").map_err(|e|e.to_string())?;
        let upload=self.target("Upload fixture")?;self.call("click",json!({"target":upload}))?;
        let mut payload=self.payload("file_upload",json!({"paths":[file]}));payload["readable_paths"]=json!([file]);let upload=self.send(&payload)?;self.check("fileUpload",upload["success"]==true)?;
        let password=self.target("Password")?;let name=self.target("Name")?;let filled=self.call("fill_saved_login",json!({"password_target":password,"username_target":name}))?;
        self.check("savedLoginSecretNotReturned",!filled.to_string().contains("synthetic-ai-password"))?;
        let value=self.call("evaluate",json!({"function":"()=>document.getElementById('name').value==='synthetic-ai-user'&&document.getElementById('password').value.length>0"}))?;self.check("savedLoginFilled",value["result"]=="true")?;
        let snap=self.snapshot()?;self.check("snapshotMasksPassword",!snap.to_string().contains("synthetic-ai-password"))?;
        let value=self.call("evaluate",json!({"function":"()=>[typeof window.__TAURI_INTERNALS__,typeof window.astrionBrowser,typeof globalThis.__astrionAI].join('|')"}))?;self.check("untrustedPageHasNoBridge",value["result"]=="undefined|undefined|undefined")?;
        let target=self.target("Increment")?;let mut denied=self.payload("click",json!({"target":target}));denied["any_site"]=json!(false);denied["allowed_sites"]=json!(["other.example"]);let denied=self.send(&denied)?;self.check("wrongSiteRejected",denied["code"]=="site_not_approved")?;
        let stale=self.raw("click",json!({"target":"e999999"}))?;self.check("staleRefRejected",stale["code"]=="stale_ref")?;
        let mut other=self.payload("tabs",json!({"action":"list"}));other["identity"]["conversation_id"]=json!("conv_other");let other=self.send(&other)?;self.check("conversationIsolation",other["tabs"].as_array().is_some_and(Vec::is_empty))?;
        let tabs=self.call("tabs",json!({"action":"list"}))?;let first=tabs["tabs"][0]["id"].clone();let added=self.call("tabs",json!({"action":"new","url":url}))?;let second=added["tab"]["id"].clone();self.call("tabs",json!({"action":"select","tab_id":first}))?;self.call("tabs",json!({"action":"close","tab_id":second}))?;self.check("tabsNewSelectClose",true)?;
        let payload=self.payload("wait_for",json!({"time":60}));let call_id=payload["call_id"].clone();let client=self.client.clone();let url=self.url.clone();let token=self.token.clone();
        let pending=std::thread::spawn(move||client.post(format!("{url}/browser/call")).header("X-Astrion-Browser-Token",token).json(&payload).send().unwrap().json::<Value>().unwrap());
        std::thread::sleep(Duration::from_millis(500));let cancel:Value=self.client.post(format!("{}/browser/cancel",self.url)).header("X-Astrion-Browser-Token",&self.token).json(&json!({"call_id":call_id})).send().map_err(|e|e.to_string())?.json().map_err(|e|e.to_string())?;let stopped=pending.join().map_err(|_|"cancel thread failed")?;self.check("cancelStopsWait",cancel["cancelled"]==true && stopped["code"]=="cancelled")?;
        Ok(())
    }
}
pub fn run() {
    let root=PathBuf::from(std::env::var_os("ASTRION_BROWSER_PRODUCTION_PROBE_ROOT").expect("isolated root"));std::fs::create_dir_all(&root).unwrap();
    let fixture=fixture::start("ai",0);let origin=fixture.origin.clone();let python=PathBuf::from(std::env::var_os("ASTRION_BROWSER_PROBE_PYTHON").unwrap());
    let report=Arc::new(Mutex::new(json!({"ok":false})));
    tauri::Builder::default().setup(move|app|{
        WebviewWindowBuilder::new(app,"main",WebviewUrl::External(format!("{origin}/new").parse()?)).data_directory(root.join("main-webview2")).initialization_script(browser::BRIDGE_SCRIPT).title("Astrion AI tool native regression").inner_size(1000.,700.).build()?;
        browser::initialize(app.handle(),origin.clone(),root.clone(),python,PathBuf::new()).map_err(std::io::Error::other)?;
        let (port,token)=browser::start_ai_service(app.handle()).map_err(std::io::Error::other)?;
        let handle=app.handle().clone();let state=fixture.state.clone();let done=fixture.done.clone();let report=report.clone();
        std::thread::spawn(move||{
            let start=Instant::now();while start.elapsed()<Duration::from_secs(20) && state.lock().unwrap()["events"]["aiReady"]!=true {if state.lock().unwrap()["events"]["aiError"].is_string(){break;}std::thread::sleep(Duration::from_millis(100));}
            let mut harness=Harness{client:reqwest::blocking::Client::builder().no_proxy().timeout(Duration::from_secs(150)).build().unwrap(),url:format!("http://127.0.0.1:{port}"),token,origin,root:root.clone(),seq:0,checks:vec![]};
            let result=if state.lock().unwrap()["events"]["aiReady"]==true {harness.tests()}else{Err(format!("host fixture not ready: {}",state.lock().unwrap()["events"]))};
            *report.lock().unwrap()=json!({"ok":result.is_ok(),"checks":harness.checks,"error":result.err(),"root":root});
            std::fs::write(root.join("ai.json"),serde_json::to_vec_pretty(&*report.lock().unwrap()).unwrap()).unwrap();done.store(true,Ordering::Relaxed);
            let exit=handle.clone();let _=handle.run_on_main_thread(move||{browser::dispose();exit.exit(0);});
        });Ok(())
    }).run(tauri::generate_context!()).expect("native AI probe");
}
