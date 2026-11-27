//! Native protocol checks that require Linux but do not change host policy.

use std::{
    fs::{File, OpenOptions},
    os::fd::AsRawFd,
    time::Duration,
};

use serde_json::{Value, json};

use super::transport::{PacketSocket, active, result, validate_stdio};

#[test]
fn seqpacket_transfers_stdio_as_cloexec() {
    let (send, receive) = PacketSocket::pair().unwrap();
    send.timeout(Duration::from_secs(1)).unwrap();
    receive.timeout(Duration::from_secs(1)).unwrap();

    let input = File::open("/dev/null").unwrap();
    let output = OpenOptions::new().write(true).open("/dev/null").unwrap();
    let fds = [input.as_raw_fd(), output.as_raw_fd(), output.as_raw_fd()];
    send.send(&json!({"operation": "run"}), &fds).unwrap();
    let (payload, descriptors) = receive.receive::<Value>(3).unwrap();
    assert_eq!(payload, json!({"operation": "run"}));
    validate_stdio(&descriptors).unwrap();
    assert!(descriptors.iter().all(|fd| {
        let flags = result(unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_GETFD) }).unwrap();
        flags & libc::FD_CLOEXEC != 0
    }));

    send.send(&json!({"operation": "run"}), &[output.as_raw_fd(); 3])
        .unwrap();
    let (_, descriptors) = receive.receive::<Value>(3).unwrap();
    assert!(validate_stdio(&descriptors).is_err());

    send.send(&json!({"operation":"status"}), &[input.as_raw_fd(); 4])
        .unwrap();
    assert!(receive.receive::<Value>(3).is_err());
    drop(send);
    assert!(active(receive.as_raw_fd()).unwrap());
}

#[test]
fn kernel_peer_pidfd_identifies_the_connected_process() {
    let (socket, _peer) = PacketSocket::pair().unwrap();
    let credentials = socket.credentials().unwrap();
    assert_eq!(credentials.pid, unsafe { libc::getpid() });
    assert_eq!(credentials.uid, unsafe { libc::getuid() });
    let pidfd = socket.peer_pidfd().unwrap();
    assert!(!active(pidfd.as_raw_fd()).unwrap());
}
