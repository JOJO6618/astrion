//! A process cannot run before admission to its own kill-on-close job.

use super::*;
use std::{
    io::{Read, Write}, mem::{size_of, size_of_val},
    os::windows::{io::{AsRawHandle, FromRawHandle, OwnedHandle}, process::CommandExt},
    process::{Child, Stdio},
};
use windows_sys::Win32::{
    Foundation::{HANDLE, INVALID_HANDLE_VALUE},
    System::{
        Diagnostics::ToolHelp::{CreateToolhelp32Snapshot, TH32CS_SNAPTHREAD, THREADENTRY32, Thread32First, Thread32Next},
        JobObjects::{AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
            SetInformationJobObject, TerminateJobObject},
        Pipes::PeekNamedPipe,
        Threading::{CREATE_NO_WINDOW, CREATE_SUSPENDED, OpenThread, ResumeThread, THREAD_SUSPEND_RESUME},
    },
};

const POLL: Duration = Duration::from_millis(15);

pub(super) fn spawn(command: Command, timeout: Option<Duration>, shared: Arc<Shared>) -> io::Result<()> {
    let (ready, wait) = std::sync::mpsc::sync_channel(0);
    std::thread::Builder::new().name("astrion-host-process".into()).spawn(move || {
        let (mut child, job) = match start(command, timeout.is_none()) {
            Ok(result) => result,
            Err(error) => { let _ = ready.send(Err(error)); return; }
        };
        if ready.send(Ok(())).is_err() { let _ = terminate(&job, &mut child); return; }
        let launched = Instant::now();
        let mut stdout = child.stdout.take();
        let mut stderr = child.stderr.take();
        if let Some(stdin) = child.stdin.take() {
            let state = shared.clone();
            std::thread::spawn(move || send_input(stdin, state));
        }
        let finish = loop {
            let out = read(&mut stdout);
            let err = read(&mut stderr);
            let mut state = shared.state();
            if !(out.is_empty() && err.is_empty()) {
                state.read_output(&out, &err);
                shared.changed();
            }
            if state.cancelled { break Ok(Status::Cancelled); }
            if timeout.is_some_and(|timeout| launched.elapsed() >= timeout) { break Ok(Status::Timeout); }
            match child.try_wait() {
                Ok(Some(_)) => break Ok(Status::Completed),
                Ok(None) => (),
                Err(error) => break Err(error),
            }
            let _state = shared.wake.wait_timeout(state, POLL).unwrap_or_else(|error| error.into_inner());
        };
        let code = terminate(&job, &mut child);
        let (status, message) = match (&finish, &code) {
            (_, Err(error)) | (Err(error), _) => (Status::Failed, Some(format!("Process supervision failed: {error}"))),
            (Ok(Status::Completed), Ok(0)) => (Status::Completed, None),
            (Ok(Status::Completed), Ok(code)) => (Status::Failed, Some(format!("Command exited with return code {code}"))),
            (Ok(Status::Timeout), _) => (Status::Timeout, Some("Command timed out".into())),
            (Ok(Status::Cancelled), _) => (Status::Cancelled, Some("Command cancelled".into())),
            (Ok(status), _) => (*status, None),
        };
        let (out, err) = (read(&mut stdout), read(&mut stderr));
        let mut state = shared.state();
        if !(out.is_empty() && err.is_empty()) { state.read_output(&out, &err); }
        let unfinished = std::mem::take(&mut state.marker_tail);
        state.output.push(&unfinished);
        state.marker = None;
        state.input.clear();
        state.status = status;
        state.message = message;
        state.return_code = code.ok();
        state.updated_at = now();
        state.finished_at = Some(state.updated_at);
        shared.changed();
    })?;
    wait.recv().map_err(io::Error::other)?
}

fn start(mut command: Command, interactive: bool) -> io::Result<(Child, OwnedHandle)> {
    let job = owned(unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) })?;
    let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    let result = unsafe { SetInformationJobObject(job.as_raw_handle(), JobObjectExtendedLimitInformation,
        (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(), size_of_val(&limits) as u32) };
    if result == 0 { return Err(io::Error::last_os_error()); }
    let mut child = command.creation_flags(CREATE_SUSPENDED | CREATE_NO_WINDOW)
        .stdin(if interactive { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped()).stderr(Stdio::piped()).spawn()?;
    let admitted = if unsafe { AssignProcessToJobObject(job.as_raw_handle(), child.as_raw_handle()) } == 0 {
        Err(io::Error::last_os_error())
    } else {
        resume_child(child.id())
    };
    if let Err(error) = admitted {
        let _ = child.kill();
        let _ = child.wait();
        return Err(error);
    }
    Ok((child, job))
}

fn owned(handle: HANDLE) -> io::Result<OwnedHandle> {
    if handle.is_null() || handle == INVALID_HANDLE_VALUE {
        return Err(io::Error::last_os_error());
    }
    // Each creator returns an owned, noninheritable Win32 handle.
    Ok(unsafe { OwnedHandle::from_raw_handle(handle) })
}

fn resume_child(pid: u32) -> io::Result<()> {
    let snapshot = owned(unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) })?;
    let mut row = THREADENTRY32 { dwSize: size_of::<THREADENTRY32>() as u32, ..Default::default() };
    let mut found = unsafe { Thread32First(snapshot.as_raw_handle(), &mut row) };
    while found != 0 {
        if row.th32OwnerProcessID == pid {
            let thread = owned(unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, row.th32ThreadID) })?;
            if unsafe { ResumeThread(thread.as_raw_handle()) } == u32::MAX {
                return Err(io::Error::last_os_error());
            }
            return Ok(());
        }
        row.dwSize = size_of::<THREADENTRY32>() as u32;
        found = unsafe { Thread32Next(snapshot.as_raw_handle(), &mut row) };
    }
    Err(io::Error::other("Suspended Windows child thread disappeared"))
}

fn terminate(job: &OwnedHandle, child: &mut Child) -> io::Result<i32> {
    if unsafe { TerminateJobObject(job.as_raw_handle(), 1) } == 0 {
        let error = io::Error::last_os_error();
        let _ = child.kill();
        let _ = child.wait();
        return Err(error);
    }
    child.wait()?.code().ok_or_else(|| io::Error::other("The Windows process returned no exit code"))
}

fn read<R: Read + AsRawHandle>(pipe: &mut Option<R>) -> Vec<u8> {
    let mut output = Vec::new();
    let Some(reader) = pipe.as_mut() else { return output; };
    let mut block = [0u8; 8192];
    for _ in 0..16 {
        let mut available = 0;
        if unsafe { PeekNamedPipe(reader.as_raw_handle(), std::ptr::null_mut(), 0,
            std::ptr::null_mut(), &mut available, std::ptr::null_mut()) } == 0
        {
            *pipe = None;
            break;
        }
        if available == 0 { break; }
        let size = (available as usize).min(block.len());
        match reader.read(&mut block[..size]) {
            Ok(count) if count > 0 => output.extend_from_slice(&block[..count]),
            _ => { *pipe = None; break; }
        }
    }
    output
}

fn send_input(mut stdin: impl Write, shared: Arc<Shared>) {
    loop {
        let mut state = shared.wake.wait_while(shared.state(), |state|
            state.input.is_empty() && !state.cancelled && state.status == Status::Running,
        ).unwrap_or_else(|error| error.into_inner());
        if state.cancelled || state.status != Status::Running { break; }
        let input = std::mem::take(&mut state.input);
        drop(state);
        if stdin.write_all(&Vec::from(input)).is_err() { break; }
    }
}
