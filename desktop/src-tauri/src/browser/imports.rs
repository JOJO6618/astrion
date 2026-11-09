//! Chrome reads run outside the UI thread; previews stay in memory and expire.
use super::{manager::Browser, policy, *};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};
#[derive(Default)]
pub struct Imports {
    pub operation: Option<(String, Arc<AtomicBool>)>,
    pub preview: Option<Preview>,
    pub commit: Option<super::import_commit::Commit>,
}
pub struct Preview {
    pub id: String,
    pub profile: String,
    pub lease: String,
    pub until: u64,
    pub value: Value,
    pub vault: Value,
}
impl Imports {
    pub fn cancel(&mut self) {
        if let Some((_, cancel)) = self.operation.take() {
            cancel.store(true, Ordering::SeqCst);
        }
        self.preview = None;
        self.commit = None;
    }
    pub fn running(&self) -> bool {
        self.operation.is_some() || self.commit.is_some()
    }
}
impl Browser {
    pub fn import_request(&mut self, request: &Value) {
        let action = request["data"]["action"].as_str().unwrap_or("");
        if action == "cancel-import" {
            self.imports.cancel();
            self.reply(request, Ok(json!({})));
            return;
        }
        if self.imports.running() {
            self.reply(request, Err("browser_import_busy".into()));
            return;
        }
        if action == "import" {
            if let Err(error) = self.begin_import(request) {
                self.reply(request, Err(error));
            }
            return;
        }
        self.imports.preview = None;
        let request = request.clone();
        let lease = self.lease.clone();
        let profile = self.profile.clone();
        let app = self.app.clone();
        let python = self.python.clone();
        let cancel = Arc::new(AtomicBool::new(false));
        self.imports.operation =
            Some((request["id"].as_str().unwrap_or("").into(), cancel.clone()));
        self.send(
            "credentials-progress",
            json!({"lease":self.lease,"stage":if action=="sources"{"profiles"}else{"decrypt"}}),
        );
        std::thread::spawn(move || {
            let result = read_worker(python, &request["data"], cancel);
            let handle = app.clone();
            let _ = app.run_on_main_thread(move || {
                enqueue(
                    &handle,
                    Event::Worker {
                        request,
                        lease,
                        profile,
                        result,
                    },
                )
            });
        });
    }
    pub fn worker_done(
        &mut self,
        request: Value,
        lease: String,
        profile: String,
        result: Result<Value, String>,
    ) {
        let current = self
            .imports
            .operation
            .as_ref()
            .is_some_and(|(id, _)| request["id"] == *id);
        if current {
            self.imports.operation = None;
        }
        if !current || self.lease != lease || self.profile != profile {
            self.reply(&request, Err("stale_browser_import".into()));
            return;
        }
        let result=result.and_then(|mut value|{
            if request["data"]["action"]=="sources"{return Ok(value);}
            let vault=self.profiles[&self.profile].vault.list();let mut groups:HashMap<String,Value>=HashMap::new();
            let candidates=value["passwords"].take().as_array().cloned().ok_or("invalid_import_data")?;
            for candidate in candidates{
                let identity=json!([candidate["origin"],candidate["username"]]).to_string();
                let existing=self.profiles[&self.profile].vault.matches(candidate["origin"].as_str().unwrap_or("")).into_iter().find(|row|row["username"]==candidate["username"]);
                let group=groups.entry(identity).or_insert_with(||json!({"id":policy::id(),"origin":candidate["origin"],"username":candidate["username"],"existingId":existing.as_ref().map(|r|r["id"].clone()),"candidates":[]}));
                if existing.as_ref().is_some_and(|r|r["password"]==candidate["password"]) || group["candidates"].as_array().unwrap().iter().any(|r|r["password"]==candidate["password"]){adjust(&mut value["stats"]["passwords"],"duplicate");continue;}
                group["candidates"].as_array_mut().unwrap().push(candidate);
            }
            let groups:Vec<_>=groups.into_values().filter(|g|!g["candidates"].as_array().unwrap().is_empty()).collect();
            let conflicts:Vec<_>=groups.iter().filter(|g|g["existingId"].is_string() || g["candidates"].as_array().unwrap().len()>1).map(|g|json!({"id":g["id"],"origin":g["origin"],"username":g["username"],"hasExisting":g["existingId"].is_string(),"candidates":g["candidates"].as_array().unwrap().iter().map(|r|json!({"id":r["id"],"sourceURL":r["sourceURL"],"modified":r["modified"]})).collect::<Vec<_>>()})).collect();
            value["passwords"]=json!(groups);value["stats"]["passwords"]["conflicts"]=json!(conflicts.len());
            let id=policy::id();let response=json!({"preview":{"id":id,"stats":value["stats"],"conflicts":conflicts}});
            self.imports.preview=Some(Preview{id,lease,profile,until:policy::now()+300000,value,vault});Ok(response)
        });
        self.reply(&request, result);
    }
}
fn adjust(stat: &mut Value, reason: &str) {
    stat["ready"] = json!(stat["ready"].as_u64().unwrap_or(0).saturating_sub(1));
    stat["skipped"] = json!(stat["skipped"].as_u64().unwrap_or(0) + 1);
    let count = stat["reasons"][reason].as_u64().unwrap_or(0) + 1;
    stat["reasons"][reason] = json!(count);
}
fn read_worker(
    python: std::path::PathBuf,
    input: &Value,
    cancel: Arc<AtomicBool>,
) -> Result<Value, String> {
    use std::{
        io::{Read, Write},
        os::windows::process::CommandExt,
        process::{Command, Stdio},
        time::{Duration, Instant},
    };
    let mut child = Command::new(python)
        .args(["-I", "-c", include_str!("chrome_worker.py")])
        .creation_flags(0x08000000)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "chrome_worker_start_failed")?;
    let result = (|| {
        let mut stdin = child.stdin.take().ok_or("chrome_worker_pipe_failed")?;
        stdin
            .write_all(input.to_string().as_bytes())
            .map_err(|_| "chrome_worker_pipe_failed")?;
        drop(stdin);
        let stdout = child.stdout.take().ok_or("chrome_worker_pipe_failed")?;
        let reader = std::thread::spawn(move || {
            let mut bytes = Vec::new();
            stdout
                .take(128 * 1024 * 1024 + 1)
                .read_to_end(&mut bytes)
                .map_err(|_| "chrome_worker_read_failed")?;
            if bytes.len() > 128 * 1024 * 1024 {
                return Err("chrome_import_too_large");
            }
            let value =
                serde_json::from_slice::<Value>(&bytes).map_err(|_| "chrome_worker_invalid_result");
            bytes.fill(0);
            value
        });
        let start = Instant::now();
        loop {
            if cancel.load(Ordering::SeqCst) || start.elapsed() > Duration::from_secs(150) {
                let _ = child.kill();
                let _ = child.wait();
                let _ = reader.join();
                return Err("chrome_import_cancelled".into());
            }
            if child
                .try_wait()
                .map_err(|_| "chrome_worker_wait_failed")?
                .is_some()
            {
                break;
            }
            std::thread::sleep(Duration::from_millis(40));
        }
        let value = reader
            .join()
            .map_err(|_| "chrome_worker_read_failed")?
            .map_err(str::to_owned)?;
        if value["ok"] != true {
            return Err(value["error"]
                .as_str()
                .unwrap_or("chrome_read_failed")
                .into());
        }
        Ok(value["value"].clone())
    })();
    if result.is_err() {
        let _ = child.kill();
        let _ = child.wait();
    }
    result
}
