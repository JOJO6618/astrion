use std::io::{self, Read};
use tokio::sync::oneshot;

/// The Windows Electron guardian forwards its dedicated control pipe as stdin.
/// Any byte or EOF requests the same graceful stop as an operating system signal.
pub async fn desktop_stop() {
    if std::env::var_os("ASTRION_DESKTOP_STOP_STDIN").as_deref() != Some("1".as_ref()) {
        std::future::pending::<()>().await;
        return;
    }
    wait_for_input(io::stdin()).await;
}

async fn wait_for_input(mut reader: impl Read + Send + 'static) {
    let (tx, rx) = oneshot::channel();
    // A detached std thread cannot hold Tokio's runtime open after Ctrl-C.
    let _ = std::thread::Builder::new()
        .name("desktop-stop-pipe".into())
        .spawn(move || {
            let mut byte = [0];
            loop {
                match reader.read(&mut byte) {
                    Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                    _ => break,
                }
            }
            let _ = tx.send(());
        });
    let _ = rx.await;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn a_byte_or_eof_requests_stop() {
        for input in [&b"\n"[..], &b""[..]] {
            tokio::time::timeout(std::time::Duration::from_secs(1), wait_for_input(input))
                .await
                .unwrap();
        }
    }
}
