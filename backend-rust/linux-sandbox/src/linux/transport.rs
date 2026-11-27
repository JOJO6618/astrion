//! The control and runner channels each transfer complete bounded datagrams.

use std::{
    io,
    mem::{size_of, zeroed},
    os::{
        fd::{AsRawFd, FromRawFd, OwnedFd, RawFd},
        unix::ffi::OsStrExt,
    },
    path::Path,
    ptr,
    time::Duration,
};

use serde::{Serialize, de::DeserializeOwned};

use crate::protocol::{MAX_PACKET, MAX_SOURCE_FDS, invalid};

pub struct PacketSocket(OwnedFd);

impl AsRawFd for PacketSocket {
    fn as_raw_fd(&self) -> RawFd {
        self.0.as_raw_fd()
    }
}

pub fn result(code: libc::c_int) -> io::Result<libc::c_int> {
    if code < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(code)
    }
}

pub fn owned(fd: RawFd) -> io::Result<OwnedFd> {
    result(fd).map(|fd| unsafe { OwnedFd::from_raw_fd(fd) })
}

fn address(path: &Path) -> io::Result<(libc::sockaddr_un, libc::socklen_t)> {
    let bytes = path.as_os_str().as_bytes();
    let mut address: libc::sockaddr_un = unsafe { zeroed() };
    if bytes.is_empty() || bytes.len() >= address.sun_path.len() || bytes.contains(&0) {
        return Err(invalid("Invalid sandbox socket path"));
    }
    address.sun_family = libc::AF_UNIX as _;
    for (target, byte) in address.sun_path.iter_mut().zip(bytes) {
        *target = *byte as _;
    }
    let length = std::mem::offset_of!(libc::sockaddr_un, sun_path) + bytes.len() + 1;
    Ok((address, length as _))
}

impl PacketSocket {
    pub fn new() -> io::Result<Self> {
        owned(unsafe { libc::socket(libc::AF_UNIX, libc::SOCK_SEQPACKET | libc::SOCK_CLOEXEC, 0) })
            .map(Self)
    }

    pub fn pair() -> io::Result<(Self, Self)> {
        let mut descriptors = [-1; 2];
        result(unsafe {
            libc::socketpair(
                libc::AF_UNIX,
                libc::SOCK_SEQPACKET | libc::SOCK_CLOEXEC,
                0,
                descriptors.as_mut_ptr(),
            )
        })?;
        Ok((Self(owned(descriptors[0])?), Self(owned(descriptors[1])?)))
    }

    pub fn connect(path: &Path, timeout: Duration) -> io::Result<Self> {
        let socket = Self::new()?;
        socket.timeout(timeout)?;
        let (address, length) = address(path)?;
        result(unsafe {
            libc::connect(socket.as_raw_fd(), ptr::from_ref(&address).cast(), length)
        })?;
        Ok(socket)
    }

    pub fn bind(&self, path: &Path) -> io::Result<()> {
        let (address, length) = address(path)?;
        result(unsafe { libc::bind(self.as_raw_fd(), ptr::from_ref(&address).cast(), length) })?;
        Ok(())
    }

    pub fn listen(&self, backlog: usize) -> io::Result<()> {
        result(unsafe { libc::listen(self.as_raw_fd(), backlog as _) })?;
        Ok(())
    }

    pub fn accept(&self) -> io::Result<Self> {
        let fd = unsafe {
            libc::accept4(
                self.as_raw_fd(),
                ptr::null_mut(),
                ptr::null_mut(),
                libc::SOCK_CLOEXEC,
            )
        };
        owned(fd).map(Self)
    }

    pub fn timeout(&self, duration: Duration) -> io::Result<()> {
        self.set_timeout(libc::SO_RCVTIMEO, duration)?;
        self.set_timeout(libc::SO_SNDTIMEO, duration)
    }

    pub fn set_timeout(&self, option: libc::c_int, duration: Duration) -> io::Result<()> {
        let timeout = libc::timeval {
            tv_sec: duration.as_secs() as _,
            tv_usec: duration.subsec_micros() as _,
        };
        result(unsafe {
            libc::setsockopt(
                self.as_raw_fd(),
                libc::SOL_SOCKET,
                option,
                ptr::from_ref(&timeout).cast(),
                size_of::<libc::timeval>() as _,
            )
        })?;
        Ok(())
    }

    pub fn credentials(&self) -> io::Result<libc::ucred> {
        let mut value: libc::ucred = unsafe { zeroed() };
        let length = self.get_option(libc::SO_PEERCRED, std::slice::from_mut(&mut value))?;
        if length != size_of::<libc::ucred>() {
            return Err(invalid("Incomplete sandbox peer credentials"));
        }
        Ok(value)
    }

    pub fn label(&self) -> io::Result<String> {
        let mut bytes = [0u8; 1024];
        let length = self.get_option(libc::SO_PEERSEC, &mut bytes)?;
        let label = std::str::from_utf8(&bytes[..length])
            .map_err(|_| invalid("Invalid sandbox peer security label"))?;
        Ok(label.trim_end_matches('\0').to_owned())
    }

    pub fn peer_pidfd(&self) -> io::Result<OwnedFd> {
        let mut descriptor = -1;
        // SO_PEERPIDFD is available on the supported Ubuntu kernel (6.8).
        // The descriptor identifies the socket peer even if its PID is reused.
        let length = self.get_option(libc::SO_PEERPIDFD, std::slice::from_mut(&mut descriptor))?;
        let fd = owned(descriptor)?;
        if length != size_of::<RawFd>() {
            return Err(invalid("Incomplete sandbox peer PID descriptor"));
        }
        result(unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_SETFD, libc::FD_CLOEXEC) })?;
        Ok(fd)
    }

    fn get_option<T>(&self, option: libc::c_int, buffer: &mut [T]) -> io::Result<usize> {
        let capacity = std::mem::size_of_val(buffer);
        let mut length = capacity as libc::socklen_t;
        result(unsafe {
            libc::getsockopt(
                self.as_raw_fd(),
                libc::SOL_SOCKET,
                option,
                buffer.as_mut_ptr().cast(),
                &mut length,
            )
        })?;
        if length as usize > capacity {
            return Err(invalid("Oversized sandbox socket option"));
        }
        Ok(length as usize)
    }

    pub fn send<T: Serialize + ?Sized>(
        &self,
        payload: &T,
        descriptors: &[RawFd],
    ) -> io::Result<()> {
        let mut data = serde_json::to_vec(payload)?;
        if data.len() > MAX_PACKET || descriptors.len() > MAX_SOURCE_FDS {
            return Err(invalid("Sandbox message exceeds the protocol limit"));
        }
        let mut vectors = libc::iovec {
            iov_base: data.as_mut_ptr().cast(),
            iov_len: data.len(),
        };
        let mut message: libc::msghdr = unsafe { zeroed() };
        message.msg_iov = &mut vectors;
        message.msg_iovlen = 1;
        let mut control = ancillary(descriptors.len());
        if !descriptors.is_empty() {
            message.msg_control = control.as_mut_ptr().cast();
            message.msg_controllen = std::mem::size_of_val(control.as_slice());
            let bytes = std::mem::size_of_val(descriptors);
            unsafe {
                let header = libc::CMSG_FIRSTHDR(&message);
                (*header).cmsg_level = libc::SOL_SOCKET;
                (*header).cmsg_type = libc::SCM_RIGHTS;
                (*header).cmsg_len = libc::CMSG_LEN(bytes as _) as _;
                ptr::copy_nonoverlapping(
                    descriptors.as_ptr(),
                    libc::CMSG_DATA(header).cast(),
                    descriptors.len(),
                );
            }
        }
        let sent = unsafe { libc::sendmsg(self.as_raw_fd(), &message, libc::MSG_NOSIGNAL) };
        if sent < 0 {
            return Err(io::Error::last_os_error());
        }
        if sent as usize != data.len() {
            return Err(invalid("Incomplete sandbox message"));
        }
        Ok(())
    }

    pub fn receive<T: DeserializeOwned>(&self, max_fds: usize) -> io::Result<(T, Vec<OwnedFd>)> {
        let mut data = vec![0u8; MAX_PACKET + 1];
        let mut vectors = libc::iovec {
            iov_base: data.as_mut_ptr().cast(),
            iov_len: data.len(),
        };
        let mut control = ancillary(max_fds);
        let mut message: libc::msghdr = unsafe { zeroed() };
        message.msg_iov = &mut vectors;
        message.msg_iovlen = 1;
        if max_fds != 0 {
            message.msg_control = control.as_mut_ptr().cast();
            message.msg_controllen = std::mem::size_of_val(control.as_slice());
        }
        let received =
            unsafe { libc::recvmsg(self.as_raw_fd(), &mut message, libc::MSG_CMSG_CLOEXEC) };
        if received < 0 {
            return Err(io::Error::last_os_error());
        }
        // Take ownership of all descriptors delivered by the kernel even when
        // later checks reject the payload, including MSG_CTRUNC.
        let mut descriptors = Vec::new();
        let mut unexpected = false;
        unsafe {
            let mut header = libc::CMSG_FIRSTHDR(&message);
            while !header.is_null() {
                if (*header).cmsg_level == libc::SOL_SOCKET
                    && (*header).cmsg_type == libc::SCM_RIGHTS
                {
                    let size = (*header).cmsg_len - libc::CMSG_LEN(0) as usize;
                    let fds = std::slice::from_raw_parts(
                        libc::CMSG_DATA(header).cast::<RawFd>(),
                        size / size_of::<RawFd>(),
                    );
                    for &fd in fds {
                        descriptors.push(OwnedFd::from_raw_fd(fd));
                    }
                } else {
                    unexpected = true;
                }
                header = libc::CMSG_NXTHDR(&message, header);
            }
        }
        if received == 0 {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "Sandbox controller disconnected",
            ));
        }
        if unexpected
            || message.msg_flags & (libc::MSG_TRUNC | libc::MSG_CTRUNC) != 0
            || received as usize > MAX_PACKET
            || descriptors.len() > max_fds
        {
            return Err(invalid(
                "Invalid ancillary data or oversized sandbox request",
            ));
        }
        let payload = serde_json::from_slice(&data[..received as usize])?;
        Ok((payload, descriptors))
    }
}

fn ancillary(count: usize) -> Vec<usize> {
    let bytes = unsafe { libc::CMSG_SPACE((count * size_of::<RawFd>()) as _) } as usize;
    vec![0; bytes.div_ceil(size_of::<usize>())]
}

pub fn poll(descriptors: &mut [libc::pollfd], timeout_ms: i32) -> io::Result<()> {
    match result(unsafe {
        libc::poll(descriptors.as_mut_ptr(), descriptors.len() as _, timeout_ms)
    }) {
        Ok(_) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::Interrupted => Ok(()),
        Err(error) => Err(error),
    }
}

pub fn event(fd: RawFd) -> libc::pollfd {
    libc::pollfd {
        fd,
        events: libc::POLLIN,
        revents: 0,
    }
}

pub fn active(fd: RawFd) -> io::Result<bool> {
    let mut descriptors = [event(fd)];
    poll(&mut descriptors, 0)?;
    Ok(descriptors[0].revents != 0)
}

pub fn stat(fd: RawFd) -> io::Result<libc::stat> {
    let mut value = unsafe { zeroed() };
    result(unsafe { libc::fstat(fd, &mut value) })?;
    Ok(value)
}

pub fn validate_stdio(descriptors: &[OwnedFd]) -> io::Result<()> {
    if descriptors.len() != 3 {
        return Err(invalid("Exactly three stdio descriptors are required"));
    }
    for (index, fd) in descriptors.iter().enumerate() {
        let fd = fd.as_raw_fd();
        let mode = stat(fd)?.st_mode & libc::S_IFMT;
        if !matches!(
            mode,
            libc::S_IFREG | libc::S_IFIFO | libc::S_IFSOCK | libc::S_IFCHR
        ) {
            return Err(invalid("Unsupported stdio descriptor"));
        }
        let flags = result(unsafe { libc::fcntl(fd, libc::F_GETFL) })?;
        let access = flags & libc::O_ACCMODE;
        if flags & libc::O_PATH != 0
            || (index == 0 && access == libc::O_WRONLY)
            || (index != 0 && access == libc::O_RDONLY)
        {
            return Err(invalid("Wrong stdio access mode"));
        }
    }
    Ok(())
}
