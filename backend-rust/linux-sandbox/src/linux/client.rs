use std::{io, path::Path, time::Duration};

use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde_json::Value;

use crate::protocol::{
    DEFAULT_SOCKET, MAX_PACKET, Network, PROTOCOL_VERSION, Request, denied, invalid,
};

use super::transport::PacketSocket;

fn connect() -> io::Result<PacketSocket> {
    let connection = PacketSocket::connect(Path::new(DEFAULT_SOCKET), Duration::from_secs(6))?;
    if connection.credentials()?.uid != 0 {
        return Err(denied(
            "The sandbox helper peer is not the administrator service",
        ));
    }
    Ok(connection)
}

pub fn probe() -> io::Result<()> {
    let connection = connect()?;
    connection.send(&Request::Status {}, &[])?;
    let (reply, _) = connection.receive::<Value>(0)?;
    if reply["protocol"].as_u64() != Some(u64::from(PROTOCOL_VERSION))
        || reply["ready"].as_bool() != Some(true)
        || reply["network"] != serde_json::json!(Network::ALL)
    {
        return Err(denied(reply["error"].as_str().unwrap_or(
            "Unsupported sandbox helper. Upgrade the administrator-installed helper.",
        )));
    }
    Ok(())
}

pub fn run(encoded: &str) -> io::Result<i32> {
    if encoded.len() > MAX_PACKET.div_ceil(3) * 4 {
        return Err(invalid("Sandbox request exceeds the packet limit"));
    }
    let decoded = STANDARD
        .decode(encoded)
        .map_err(|_| invalid("Invalid sandbox request encoding"))?;
    if decoded.len() > MAX_PACKET {
        return Err(invalid("Sandbox request exceeds the packet limit"));
    }
    let request: Request = serde_json::from_slice(&decoded)?;
    let Request::Run(plan) = &request else {
        return Err(invalid("Expected a sandbox run request"));
    };
    plan.validate()?;
    let connection = connect()?;
    // The OS closes the control socket on any termination signal. The broker
    // owns cancellation and observes the socket and the client's pidfd.
    connection.timeout(Duration::from_secs(25))?;
    connection.send(&request, &[0, 1, 2])?;
    loop {
        let (reply, _) = connection.receive::<Value>(0)?;
        if let Some(error) = reply["error"].as_str() {
            eprintln!("Linux sandbox: {error}");
        }
        if let Some(code) = reply["exitcode"].as_i64() {
            if !(-127..=255).contains(&code) {
                return Err(invalid("Invalid sandbox exit code"));
            }
            return Ok(if code >= 0 { code } else { 128 - code } as i32);
        }
        if reply["started"].as_bool() == Some(true) {
            connection.set_timeout(libc::SO_RCVTIMEO, Duration::ZERO)?;
        } else {
            return Err(invalid("Unexpected sandbox control reply"));
        }
    }
}
