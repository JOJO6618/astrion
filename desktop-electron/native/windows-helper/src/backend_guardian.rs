use std::ffi::OsString;
use std::io::{self, Write};
use std::mem::size_of;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::os::windows::process::CommandExt;
use std::process::{Command, Stdio};
use windows::core::PCWSTR;
use windows::Win32::Foundation::{HANDLE, WAIT_TIMEOUT};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows::Win32::System::Threading::{
    GetCurrentProcess, OpenProcess, WaitForSingleObject, CREATE_NO_WINDOW, INFINITE,
    PROCESS_ACCESS_RIGHTS,
};

fn handle(owned: &OwnedHandle) -> HANDLE {
    HANDLE(owned.as_raw_handle())
}

pub fn run(args: &[OsString]) -> Result<(), Box<dyn std::error::Error>> {
    let [parent_pid, delimiter, executable, backend_args @ ..] = args else {
        return Err("Usage: guard-backend SHELL_PID -- EXECUTABLE [ARGS...]".into());
    };
    if delimiter != "--" {
        return Err("The backend command must follow --".into());
    }
    let parent_pid = parent_pid
        .to_str()
        .ok_or("Invalid shell PID")?
        .parse::<u32>()?;
    if parent_pid == 0 {
        return Err("Invalid shell PID".into());
    }
    // Both handles are non-inheritable. The guardian, rather than the backend,
    // owns the last job handle, so any guardian exit reaps the entire tree.
    let parent = unsafe { OpenProcess(PROCESS_ACCESS_RIGHTS(0x0010_0000), false, parent_pid)? };
    let parent = unsafe { OwnedHandle::from_raw_handle(parent.0) };
    let job = unsafe { CreateJobObjectW(None, PCWSTR::null())? };
    let job = unsafe { OwnedHandle::from_raw_handle(job.0) };
    let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    unsafe {
        SetInformationJobObject(
            handle(&job),
            JobObjectExtendedLimitInformation,
            (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        )?;
        AssignProcessToJobObject(handle(&job), GetCurrentProcess())?;
        if WaitForSingleObject(handle(&parent), 0) != WAIT_TIMEOUT {
            return Err("Electron has already exited".into());
        }
    }
    // The owner keeps the handle live until termination. Passing the value
    // instead of a clone ensures that an exiting guardian closes the last handle.
    let job_handle = job.as_raw_handle() as usize;
    std::thread::Builder::new()
        .name("electron-parent".into())
        .spawn(move || unsafe {
            WaitForSingleObject(handle(&parent), INFINITE);
            let _ = TerminateJobObject(HANDLE(job_handle as _), 1);
        })?;
    let mut child = Command::new(executable)
        .args(backend_args)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .creation_flags(CREATE_NO_WINDOW.0)
        .spawn()?;
    // The readiness probe must match the server subprocess, not this guardian.
    println!("ASTRION_BACKEND_PID={}", child.id());
    io::stdout().flush()?;
    let code = child.wait()?.code().unwrap_or(1);
    unsafe { TerminateJobObject(handle(&job), code as u32)? };
    Ok(())
}
