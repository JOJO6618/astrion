//! Loopback-only authenticated channel. The secret reaches Python through a one-shot pipe.
use super::{enqueue, policy, Event};
use serde_json::{json, Value};
use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    sync::mpsc,
    time::Duration,
};
use tauri::AppHandle;

pub fn start(app: &AppHandle) -> Result<(u16, String), String> {
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|e| e.to_string())?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    let token = format!("{}{}", policy::id(), policy::id());
    let secret = token.clone();
    let app = app.clone();
    std::thread::spawn(move || {
        for stream in listener.incoming().flatten() {
            let app = app.clone();
            let secret = secret.clone();
            std::thread::spawn(move || {
                let _ = handle(stream, &app, &secret);
            });
        }
    });
    Ok((port, token))
}
fn response(stream: &mut TcpStream, status: u16, value: Value) -> std::io::Result<()> {
    let body = value.to_string();
    write!(stream,"HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",body.len())
}
fn handle(mut stream: TcpStream, app: &AppHandle, token: &str) -> std::io::Result<()> {
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;
    stream.set_write_timeout(Some(Duration::from_secs(10)))?;
    let mut bytes = Vec::new();
    let mut chunk = [0; 4096];
    let end = loop {
        if let Some(end) = bytes.windows(4).position(|p| p == b"\r\n\r\n") {
            break end + 4;
        }
        if bytes.len() > 16384 {
            return response(&mut stream, 431, json!({"success":false}));
        }
        let n = stream.read(&mut chunk)?;
        if n == 0 {
            return Ok(());
        }
        bytes.extend_from_slice(&chunk[..n]);
    };
    let head = String::from_utf8_lossy(&bytes[..end]);
    let first = head.lines().next().unwrap_or("");
    let mut words = first.split_whitespace();
    let method = words.next().unwrap_or("");
    let path = words.next().unwrap_or("");
    let headers: Vec<_> = head
        .lines()
        .skip(1)
        .filter_map(|line| line.split_once(':'))
        .collect();
    let auth: Vec<_> = headers
        .iter()
        .filter(|(key, _)| key.eq_ignore_ascii_case("X-Astrion-Browser-Token"))
        .collect();
    let valid = auth.len() == 1 && {
        let raw = auth[0].1.trim().as_bytes();
        raw.len() == token.len()
            && raw
                .iter()
                .zip(token.as_bytes())
                .fold(0u8, |out, (a, b)| out | (a ^ b))
                == 0
    };
    if !valid
        || headers
            .iter()
            .any(|(k, _)| k.eq_ignore_ascii_case("origin"))
    {
        return response(
            &mut stream,
            403,
            json!({"success":false,"code":"forbidden","error":"forbidden"}),
        );
    }
    if method != "POST" || !["/browser/call", "/browser/cancel"].contains(&path) {
        return response(
            &mut stream,
            404,
            json!({"success":false,"error":"unknown_endpoint"}),
        );
    }
    let lengths: Vec<_> = headers
        .iter()
        .filter(|(k, _)| k.eq_ignore_ascii_case("content-length"))
        .collect();
    let length = if lengths.len() == 1 {
        lengths[0].1.trim().parse::<usize>().ok()
    } else {
        None
    };
    let Some(length) = length.filter(|n| *n <= 1024 * 1024) else {
        return response(&mut stream, 413, json!({"success":false}));
    };
    let cancel = path.ends_with("cancel");
    while bytes.len() - end < length {
        let n = stream.read(&mut chunk)?;
        if n == 0 {
            return Ok(());
        }
        bytes.extend_from_slice(&chunk[..n]);
    }
    let Ok(payload) = serde_json::from_slice::<Value>(&bytes[end..end + length]) else {
        return response(&mut stream, 400, json!({"success":false}));
    };
    let (tx, rx) = mpsc::sync_channel(1);
    let handle = app.clone();
    if app
        .run_on_main_thread(move || enqueue(&handle, Event::AiCall(payload, cancel, tx)))
        .is_err()
    {
        return response(
            &mut stream,
            503,
            json!({"success":false,"error":"browser_closed"}),
        );
    }
    let value = rx
        .recv_timeout(Duration::from_secs(if cancel { 5 } else { 145 }))
        .unwrap_or_else(
            |_| json!({"success":false,"code":"browser_timeout","error":"浏览器调用超时。"}),
        );
    response(&mut stream, 200, value)
}
