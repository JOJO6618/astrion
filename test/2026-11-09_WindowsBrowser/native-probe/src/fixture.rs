use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::{Arc, atomic::{AtomicBool, Ordering}};
use std::time::Duration;

pub fn start(write: bool, port: u16) -> (String, Arc<AtomicBool>) {
    let listener = TcpListener::bind(("127.0.0.1", port)).unwrap();
    listener.set_nonblocking(true).unwrap();
    let origin = format!("http://127.0.0.1:{}", listener.local_addr().unwrap().port());
    let stopping = Arc::new(AtomicBool::new(false));
    let stopped = stopping.clone();
    std::thread::spawn(move || {
        while !stopped.load(Ordering::Relaxed) {
            let Ok((mut stream, _)) = listener.accept() else {
                std::thread::sleep(Duration::from_millis(10));
                continue;
            };
            let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
            let mut buffer = [0; 4096];
            let length = stream.read(&mut buffer).unwrap_or(0);
            let request = String::from_utf8_lossy(&buffer[..length]);
            let route = request.split_whitespace().nth(1).unwrap_or("/");
            let (mime, body) = if route == "/fixture.js" {
                ("text/javascript", include_str!("../web/fixture.js").replace("__WRITE__", if write { "true" } else { "false" }))
            } else {
                ("text/html", include_str!("../web/fixture.html").to_string())
            };
            let response = format!("HTTP/1.1 200 OK\r\nContent-Type: {mime}; charset=utf-8\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n{body}", body.len());
            let _ = stream.write_all(response.as_bytes());
        }
    });
    (origin, stopping)
}
