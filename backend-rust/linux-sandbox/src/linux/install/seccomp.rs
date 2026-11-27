//! Generate architecture-specific libseccomp BPF during trusted installation.

use std::{
    ffi::{CStr, c_char, c_int, c_uint, c_void},
    fs::{self, OpenOptions},
    io,
    os::{
        fd::AsRawFd,
        unix::fs::{OpenOptionsExt, PermissionsExt},
    },
    path::Path,
    ptr::NonNull,
};

use crate::protocol::{Network, invalid};

use super::trusted_path;

const ALLOW: u32 = 0x7fff_0000;
const DENY: u32 = 0x0005_0000 | libc::EPERM as u32;
const CLONE3_UNAVAILABLE: u32 = 0x0005_0000 | libc::ENOSYS as u32;
const COMPARE_EQ: c_int = 4;
const COMPARE_MASKED_EQ: c_int = 7;

#[repr(C)]
struct Comparison {
    argument: c_uint,
    operation: c_int,
    value: u64,
    mask_value: u64,
}

impl Comparison {
    fn equal(argument: c_uint, value: u64) -> Self {
        Self {
            argument,
            operation: COMPARE_EQ,
            value,
            mask_value: 0,
        }
    }

    fn masked(argument: c_uint, mask: u64, value: u64) -> Self {
        Self {
            argument,
            operation: COMPARE_MASKED_EQ,
            value: mask,
            mask_value: value,
        }
    }
}

type Init = unsafe extern "C" fn(u32) -> *mut c_void;
type Resolve = unsafe extern "C" fn(*const c_char) -> c_int;
type AddRule = unsafe extern "C" fn(*mut c_void, u32, c_int, c_uint, *const Comparison) -> c_int;
type Export = unsafe extern "C" fn(*mut c_void, c_int) -> c_int;
type Release = unsafe extern "C" fn(*mut c_void);

struct Handle(NonNull<c_void>);

impl Handle {
    fn symbol(&self, name: &CStr) -> io::Result<NonNull<c_void>> {
        NonNull::new(unsafe { libc::dlsym(self.0.as_ptr(), name.as_ptr()) }).ok_or_else(|| {
            io::Error::other(format!(
                "Missing libseccomp function {}",
                name.to_string_lossy()
            ))
        })
    }
}

impl Drop for Handle {
    fn drop(&mut self) {
        unsafe {
            libc::dlclose(self.0.as_ptr());
        }
    }
}

pub(super) struct Library {
    _handle: Handle,
    init: Init,
    resolve: Resolve,
    add_rule: AddRule,
    export: Export,
    release: Release,
}

struct Filter<'a> {
    library: &'a Library,
    context: NonNull<c_void>,
}

impl Drop for Filter<'_> {
    fn drop(&mut self) {
        unsafe {
            (self.library.release)(self.context.as_ptr());
        }
    }
}

impl Filter<'_> {
    fn add(&self, name: &CStr, action: u32, comparison: Option<&Comparison>) -> io::Result<()> {
        let number = unsafe { (self.library.resolve)(name.as_ptr()) };
        if number < 0 {
            return Ok(()); // The syscall does not exist on this architecture.
        }
        let count = u32::from(comparison.is_some());
        let comparison = comparison.map_or(std::ptr::null(), std::ptr::from_ref);
        let rc = unsafe {
            (self.library.add_rule)(self.context.as_ptr(), action, number, count, comparison)
        };
        if rc < 0 {
            return Err(io::Error::other(format!(
                "seccomp rule {}: {}",
                name.to_string_lossy(),
                io::Error::from_raw_os_error(-rc)
            )));
        }
        Ok(())
    }

    fn populate(&self, network: Network) -> io::Result<()> {
        for name in [
            c"mount",
            c"umount2",
            c"pivot_root",
            c"move_mount",
            c"open_tree",
            c"fsopen",
            c"fsconfig",
            c"fsmount",
            c"mount_setattr",
            c"setns",
            c"unshare",
            c"ptrace",
            c"process_vm_readv",
            c"process_vm_writev",
            c"pidfd_getfd",
            c"bpf",
            c"keyctl",
            c"add_key",
            c"request_key",
            c"open_by_handle_at",
            c"init_module",
            c"finit_module",
            c"delete_module",
            c"reboot",
            c"kexec_load",
            c"kexec_file_load",
            c"swapon",
            c"swapoff",
            c"iopl",
            c"ioperm",
            c"userfaultfd",
            c"perf_event_open",
            c"io_uring_setup",
            c"io_uring_enter",
            c"io_uring_register",
        ] {
            self.add(name, DENY, None)?;
        }
        self.add(c"clone3", CLONE3_UNAVAILABLE, None)?;
        // The s390x clone ABI puts flags after the stack pointer.
        let clone_flags = u32::from(cfg!(target_arch = "s390x"));
        for flag in [
            0x0002_0000,
            0x0200_0000,
            0x0400_0000,
            0x0800_0000,
            0x1000_0000,
            0x2000_0000,
            0x4000_0000,
            0x80,
        ] {
            self.add(
                c"clone",
                DENY,
                Some(&Comparison::masked(clone_flags, flag, flag)),
            )?;
        }
        for family in [16, 17, 40, 44] {
            self.add(c"socket", DENY, Some(&Comparison::equal(0, family)))?;
            self.add(c"socketpair", DENY, Some(&Comparison::equal(0, family)))?;
        }
        for operation in [0x5412, 0x541d] {
            self.add(c"ioctl", DENY, Some(&Comparison::equal(1, operation)))?;
        }
        if network == Network::None {
            self.add(
                c"socket",
                DENY,
                Some(&Comparison::equal(0, libc::AF_UNIX as u64)),
            )?;
            // Keep stream and seqpacket socketpairs for internal IPC.
            self.add(
                c"socketpair",
                DENY,
                Some(&Comparison::masked(1, 0xf, libc::SOCK_DGRAM as u64)),
            )?;
        }
        Ok(())
    }
}

impl Library {
    pub(super) fn open() -> io::Result<Self> {
        let arch = match std::env::consts::ARCH {
            "x86_64" => "x86_64-linux-gnu",
            "aarch64" => "aarch64-linux-gnu",
            "arm" => "arm-linux-gnueabihf",
            "riscv64" => "riscv64-linux-gnu",
            "powerpc64" if cfg!(target_endian = "little") => "powerpc64le-linux-gnu",
            "s390x" => "s390x-linux-gnu",
            arch => {
                return Err(invalid(format!(
                    "Unsupported Ubuntu seccomp architecture: {arch}"
                )));
            }
        };
        let path = fs::canonicalize(format!("/usr/lib/{arch}/libseccomp.so.2"))?;
        trusted_path(&path, false)?;
        let path = std::ffi::CString::new(path.as_os_str().as_encoded_bytes())
            .map_err(|_| invalid("Invalid libseccomp library path"))?;
        let handle = Handle(
            NonNull::new(unsafe { libc::dlopen(path.as_ptr(), libc::RTLD_NOW | libc::RTLD_LOCAL) })
                .ok_or_else(|| io::Error::other("Unable to load system libseccomp"))?,
        );
        // libseccomp exports these stable C ABI functions. The retained handle
        // keeps them mapped for the lifetime of the library and every filter.
        let library = unsafe {
            Self {
                init: std::mem::transmute::<*mut c_void, Init>(
                    handle.symbol(c"seccomp_init")?.as_ptr(),
                ),
                resolve: std::mem::transmute::<*mut c_void, Resolve>(
                    handle.symbol(c"seccomp_syscall_resolve_name")?.as_ptr(),
                ),
                add_rule: std::mem::transmute::<*mut c_void, AddRule>(
                    handle.symbol(c"seccomp_rule_add_array")?.as_ptr(),
                ),
                export: std::mem::transmute::<*mut c_void, Export>(
                    handle.symbol(c"seccomp_export_bpf")?.as_ptr(),
                ),
                release: std::mem::transmute::<*mut c_void, Release>(
                    handle.symbol(c"seccomp_release")?.as_ptr(),
                ),
                _handle: handle,
            }
        };
        Ok(library)
    }

    pub(super) fn export(&self, path: &Path, network: Network) -> io::Result<()> {
        let context = NonNull::new(unsafe { (self.init)(ALLOW) })
            .ok_or_else(|| io::Error::other("seccomp_init failed"))?;
        let filter = Filter {
            library: self,
            context,
        };
        filter.populate(network)?;
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(path)?;
        let rc = unsafe { (self.export)(filter.context.as_ptr(), file.as_raw_fd()) };
        if rc < 0 {
            return Err(io::Error::other(format!(
                "seccomp_export_bpf: {}",
                io::Error::from_raw_os_error(-rc)
            )));
        }
        file.set_permissions(fs::Permissions::from_mode(0o600))?;
        file.sync_all()?;
        if file.metadata()?.len() == 0 {
            return Err(invalid("libseccomp exported an empty policy"));
        }
        Ok(())
    }
}
