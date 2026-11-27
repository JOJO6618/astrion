//! Derive authority from the kernel peer, never from a command request.

use std::{
    fs, io,
    os::fd::{AsRawFd, OwnedFd},
};

use serde::{Deserialize, Serialize};

use crate::protocol::{PROFILE_PREFIX, denied, invalid};

use super::transport::{PacketSocket, active, result};

pub struct Principal {
    pub identity: Identity,
    pub pidfd: OwnedFd,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Identity {
    pub uid: u32,
    pub gid: u32,
    pub groups: Vec<u32>,
}

impl Principal {
    pub fn authenticate(connection: &PacketSocket, allowed_uids: &[u32]) -> io::Result<Self> {
        let peer = connection.credentials()?;
        if peer.uid == 0 || peer.pid <= 0 || !allowed_uids.contains(&peer.uid) {
            return Err(denied(
                "This OS user is not authorized for the sandbox helper",
            ));
        }
        if connection.label()?.contains(PROFILE_PREFIX) {
            return Err(denied(
                "Sandboxed processes cannot invoke the privileged helper",
            ));
        }
        let pidfd = connection.peer_pidfd()?;
        if active(pidfd.as_raw_fd())? {
            return Err(denied("Sandbox caller has already exited"));
        }
        let status = fs::read_to_string(format!("/proc/{}/status", peer.pid))?;
        let values = |key: &str| -> io::Result<Vec<u32>> {
            status
                .lines()
                .find_map(|line| line.strip_prefix(key))
                .ok_or_else(|| denied("Missing caller credentials"))?
                .split_whitespace()
                .map(|word| {
                    word.parse()
                        .map_err(|_| invalid("Invalid caller credentials"))
                })
                .collect()
        };
        if values("Uid:")? != [peer.uid; 4]
            || values("Gid:")? != [peer.gid; 4]
            || active(pidfd.as_raw_fd())?
        {
            return Err(denied(
                "Mixed or changed caller credentials are not accepted",
            ));
        }
        let identity = Identity {
            uid: peer.uid,
            gid: peer.gid,
            groups: values("Groups:")?,
        };
        Ok(Self { identity, pidfd })
    }
}

impl Identity {
    /// Called only in a fork or inside the privileged mount namespace.
    pub fn drop_privilege(&self) -> io::Result<()> {
        if self.uid == 0
            || self.uid == u32::MAX
            || self.gid == u32::MAX
            || self.groups.contains(&u32::MAX)
            || self.groups.len() > 65_536
        {
            return Err(denied("Invalid sandbox identity"));
        }
        result(unsafe { libc::setgroups(self.groups.len(), self.groups.as_ptr()) })?;
        result(unsafe { libc::setresgid(self.gid, self.gid, self.gid) })?;
        result(unsafe { libc::setresuid(self.uid, self.uid, self.uid) })?;
        Ok(())
    }
}
