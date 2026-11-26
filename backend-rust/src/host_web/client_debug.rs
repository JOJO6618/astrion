use axum::{
    Extension, Json, Router,
    extract::{ConnectInfo, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::post,
};
use cap_fs_ext::{FollowSymlinks, OpenOptionsFollowExt};
use cap_std::{ambient_authority,fs::{Dir,OpenOptions}};
use serde_json::{Value,json};
use std::{
    collections::{HashMap, VecDeque},
    io::{self, Write},
    net::{IpAddr, SocketAddr},
    path::{Path,PathBuf},
    sync::{Arc,Mutex},
    time::{Duration,Instant,SystemTime,UNIX_EPOCH}
};

struct Log {
    directory:PathBuf,
    clients:HashMap<Option<IpAddr>,VecDeque<Instant>>,
}

pub(super) fn router(directory:PathBuf) -> Router {
    Router::new().route("/api/client_debug_log",post(receive))
        .with_state(Arc::new(Mutex::new(Log{directory,clients:HashMap::new()})))
}

async fn receive(State(log):State<Arc<Mutex<Log>>>, peer:Option<Extension<ConnectInfo<SocketAddr>>>, Json(value):Json<Value>) -> Response {
    let ip = peer.map(|Extension(ConnectInfo(addr))|addr.ip());
    let result = tokio::task::spawn_blocking(move || -> io::Result<Response> {
        let mut log = log.lock().map_err(|_|io::Error::other("Debug log unavailable"))?;
        let now = Instant::now();
        let window = Duration::from_secs(60);
        log.clients.retain(|_,hits|hits.back().is_some_and(|at|now.duration_since(*at)<window));
        let hits = log.clients.entry(ip).or_default();
        while hits.front().is_some_and(|at|now.duration_since(*at)>=window) {hits.pop_front();}
        if hits.len()>=10 {
            let wait=60-now.duration_since(hits[0]).as_secs();
            return Ok((StatusCode::TOO_MANY_REQUESTS,Json(json!({"ok":false,"error":"rate_limited","retry_after":wait}))).into_response());
        }
        hits.push_back(now);
        append(&log.directory,value)?;
        Ok(Json(json!({"ok":true})).into_response())
    }).await;
    result.ok().and_then(Result::ok).unwrap_or_else(||(StatusCode::SERVICE_UNAVAILABLE,Json(json!({"ok":false,"error":"Debug log unavailable"}))).into_response())
}

fn append(directory:&Path, value:Value) -> io::Result<()> {
    let serialized = serde_json::to_string(&value)?;
    let mut entry = if serialized.chars().count()>4000 {
        json!({"truncated":true,"raw_preview":serialized.chars().take(4000).collect::<String>()})
    } else if value.is_object() {value} else {json!({"data":value})};
    entry.as_object_mut().unwrap().entry("server_ts").or_insert_with(||json!(SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs_f64()));
    std::fs::create_dir_all(directory)?;
    let dir=Dir::open_ambient_dir(directory,ambient_authority())?;
    let filename="goal_mode_debug.log";
    let mut options=OpenOptions::new();
    options.create(true).append(true).follow(FollowSymlinks::No);
    #[cfg(unix)] {
        use cap_std::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NONBLOCK);
    }
    let mut output=dir.open_with(filename,&options)?;
    if !output.metadata()?.is_file(){return Err(io::Error::other("Invalid log file"));}
    let line=entry.to_string();
    if output.metadata()?.len()+line.len() as u64+1>20*1024*1024 {
        drop(output);
        for index in (0..3).rev() {
            let source=if index==0 {filename.to_owned()} else {format!("{filename}.{index}")};
            match dir.rename(&source,&dir,format!("{filename}.{}",index+1)) {
                Err(err) if err.kind()==io::ErrorKind::NotFound=>(),
                other=>other?,
            }
        }
        output=dir.open_with(filename,&options)?;
    }
    writeln!(output,"{line}")
}
