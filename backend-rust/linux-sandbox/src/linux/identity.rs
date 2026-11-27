//! Final trusted entrypoint. The lease, cwd and privilege drop precede exec.

use std::{
    ffi::CString,
    fs::{self, OpenOptions},
    io,
    os::{
        fd::AsRawFd,
        unix::fs::{MetadataExt, OpenOptionsExt},
    },
    path::Path,
    ptr,
};

use crate::protocol::{RUNTIME_ROOT, denied, invalid, mount_path};

use super::{
    principal::Identity,
    sources::open_path,
    transport::{PacketSocket, result, stat},
};

#[repr(C)]
struct CapHeader {
    version: u32,
    pid: i32,
}

#[repr(C)]
struct CapData {
    effective: u32,
    permitted: u32,
    inheritable: u32,
}

fn check_lease(fd: i32) -> io::Result<()> {
    let lease = stat(fd)?;
    if lease.st_mode & libc::S_IFMT != libc::S_IFREG
        || lease.st_uid != 0
        || lease.st_nlink != 1
        || lease.st_mode & 0o7777 != 0o600
    {
        return Err(denied("Sandbox lease expired or invalid"));
    }
    Ok(())
}

pub fn execute(args: &[String]) -> io::Result<i32> {
    let [uid, gid, mask, groups, cwd, device, inode, target @ ..] = args else {
        return Err(invalid("Invalid sandbox identity entrypoint arguments"));
    };
    if target.is_empty() || unsafe { libc::geteuid() } != 0 {
        return Err(denied(
            "Sandbox identity entrypoint requires trusted root setup",
        ));
    }
    let parse = |number: &str| {
        number
            .parse::<u32>()
            .map_err(|_| invalid("Invalid sandbox UID/GID/mode"))
    };
    let supplement = if groups.is_empty() {
        Vec::new()
    } else {
        groups.split(',').map(parse).collect::<io::Result<_>>()?
    };
    let identity = Identity {
        uid: parse(uid)?,
        gid: parse(gid)?,
        groups: supplement,
    };
    let mask = parse(mask)?;
    if identity.uid == 0 || mask > 0o777 {
        return Err(denied("Invalid sandbox UID or mode"));
    }
    mount_path(cwd)?;
    let root = Path::new(RUNTIME_ROOT);
    let lease = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(root.join("lease"))?;
    check_lease(lease.as_raw_fd())?;
    let home = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(root.join("home"))?;
    let mut home_fs: libc::statfs = unsafe { std::mem::zeroed() };
    result(unsafe { libc::fstatfs(home.as_raw_fd(), &mut home_fs) })?;
    if home_fs.f_type != 0x01021994 {
        return Err(denied("Sandbox home must be anonymous tmpfs"));
    }
    result(unsafe { libc::fchmod(home.as_raw_fd(), 0o700) })?;
    result(unsafe { libc::fchown(home.as_raw_fd(), identity.uid, identity.gid) })?;
    drop(home);
    result(unsafe { libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) })?;
    result(unsafe {
        libc::prctl(
            libc::PR_CAP_AMBIENT,
            libc::PR_CAP_AMBIENT_CLEAR_ALL,
            0,
            0,
            0,
        )
    })?;
    for cap in 0..64 {
        let drop = result(unsafe { libc::prctl(libc::PR_CAPBSET_DROP, cap, 0, 0, 0) });
        if let Err(error) = drop
            && error.raw_os_error() != Some(libc::EINVAL)
        {
            return Err(error);
        }
    }
    identity.drop_privilege()?;
    let directory = open_path(Path::new(cwd), true)?;
    let info = stat(directory.as_raw_fd())?;
    if device.parse::<u64>().ok() != Some(info.st_dev as u64)
        || inode.parse::<u64>().ok() != Some(info.st_ino as u64)
    {
        return Err(denied("Sandbox working directory changed during setup"));
    }
    result(unsafe { libc::fchdir(directory.as_raw_fd()) })?;
    drop(directory);
    let header = CapHeader {
        version: 0x20080522,
        pid: 0,
    };
    let caps = [
        CapData {
            effective: 0,
            permitted: 0,
            inheritable: 0,
        },
        CapData {
            effective: 0,
            permitted: 0,
            inheritable: 0,
        },
    ];
    if unsafe { libc::syscall(libc::SYS_capset, &header, caps.as_ptr()) } < 0 {
        return Err(io::Error::last_os_error());
    }
    let command: Vec<_> = target
        .iter()
        .map(|argument| CString::new(argument.as_str()))
        .collect::<Result<_, _>>()
        .map_err(|_| invalid("Invalid command argument"))?;
    let arguments: Vec<_> = command
        .iter()
        .map(|argument| argument.as_ptr())
        .chain([ptr::null()])
        .collect();
    check_lease(lease.as_raw_fd())?;
    drop(lease);
    unsafe {
        libc::umask(mask);
        if libc::syscall(libc::SYS_close_range, 3u32, u32::MAX, 0u32) < 0 {
            return Err(io::Error::last_os_error());
        }
        libc::signal(libc::SIGPIPE, libc::SIG_DFL);
        libc::execvp(arguments[0], arguments.as_ptr());
    }
    eprintln!("sandbox exec: {}", io::Error::last_os_error());
    Ok(126)
}

pub fn check(args: &[String]) -> io::Result<i32> {
    let [uid, mode] = args else {
        return Err(invalid("Expected identity check UID and mode"));
    };
    let uid = uid
        .parse::<u32>()
        .map_err(|_| invalid("Invalid check UID"))?;
    if uid == 0 || unsafe { libc::getuid() } != uid {
        return Err(denied(
            "Sandbox identity check did not drop to the selected UID",
        ));
    }
    let status = fs::read_to_string("/proc/self/status")?;
    for (key, expected) in [
        ("CapInh:", "0000000000000000"),
        ("CapPrm:", "0000000000000000"),
        ("CapEff:", "0000000000000000"),
        ("CapBnd:", "0000000000000000"),
        ("CapAmb:", "0000000000000000"),
        ("NoNewPrivs:", "1"),
        ("Seccomp:", "2"),
    ] {
        if !status
            .lines()
            .filter_map(|line| line.strip_prefix(key))
            .any(|value| value.trim() == expected)
        {
            return Err(denied(format!("Sandbox identity check failed for {key}")));
        }
    }
    let (left, right) = PacketSocket::pair()?;
    left.send(&true, &[])?;
    if !right.receive::<bool>(0)?.0 {
        return Err(denied("Sandbox anonymous IPC check failed"));
    }
    let token = super::random_token()?;
    let home_check = Path::new(RUNTIME_ROOT).join("home").join(&token);
    let home = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&home_check)?;
    if home.metadata()?.uid() != uid {
        return Err(denied("Sandbox temporary home ownership check failed"));
    }
    fs::remove_file(home_check)?;
    let check = OpenOptions::new().write(true).create_new(true).open(&token);
    match (mode.as_str(), check) {
        ("writable", Ok(_)) => fs::remove_file(token)?,
        ("readonly", Err(error))
            if matches!(
                error.raw_os_error(),
                Some(libc::EROFS | libc::EACCES | libc::EPERM)
            ) =>
        {
            ()
        }
        (_, Ok(_)) => {
            fs::remove_file(token)?;
            return Err(denied("Sandbox workspace was unexpectedly writable"));
        }
        (_, Err(error)) => return Err(error),
    }
    println!("{}", serde_json::json!({"ok": true, "uid": uid}));
    Ok(0)
}
