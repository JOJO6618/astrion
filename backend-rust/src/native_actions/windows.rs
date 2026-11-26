use super::{Application, launch};
use crate::project_git::canonicalize;
use std::{
    collections::HashSet,
    ffi::{OsStr, OsString, c_void},
    io,
    os::windows::{ffi::{OsStrExt, OsStringExt}, process::CommandExt},
    path::{Path, PathBuf},
    process::Command,
    ptr,
};

const CANCELLED: i32 = 0x800704c7_u32 as i32;
const FORCE_FILESYSTEM: u32 = 0x40;
const PICK_FOLDERS: u32 = 0x20;
const OVERWRITE_PROMPT: u32 = 0x02;
const PATH_MUST_EXIST: u32 = 0x800;
const FILESYSTEM_PATH: u32 = 0x80058000;
const CREATE_NO_WINDOW: u32 = 0x08000000;

type Raw = *mut c_void;
type Hr = i32;

#[repr(C)]
struct Guid(u32, u16, u16, [u8; 8]);

const FILE_OPEN_DIALOG: Guid = Guid(0xdc1c5a9c, 0xe88a, 0x4dde, [0xa5, 0xa1, 0x60, 0xf8, 0x2a, 0x20, 0xae, 0xf7]);
const FILE_SAVE_DIALOG: Guid = Guid(0xc0b4e2f3, 0xba21, 0x4773, [0x8d, 0xba, 0x33, 0x5e, 0xc9, 0x46, 0xeb, 0x8b]);
const IFILE_DIALOG: Guid = Guid(0x42f85136, 0xdb7e, 0x439c, [0x85, 0xf1, 0xe4, 0x07, 0x5d, 0x13, 0x56, 0xfc]);

#[link(name = "ole32")]
unsafe extern "system" {
    fn CoInitializeEx(reserved: Raw, flags: u32) -> Hr;
    fn CoUninitialize();
    fn CoCreateInstance(class: *const Guid, outer: Raw, context: u32, interface: *const Guid, out: *mut Raw) -> Hr;
    fn CoTaskMemFree(memory: Raw);
}

#[link(name = "shell32")]
unsafe extern "system" {
    fn ShellExecuteW(window: Raw, verb: *const u16, file: *const u16, parameters: *const u16, directory: *const u16, show: i32) -> isize;
    fn SHAssocEnumHandlers(extension: *const u16, filter: u32, handlers: *mut Raw) -> Hr;
}

#[link(name = "shlwapi")]
unsafe extern "system" {
    fn AssocQueryStringW(flags: u32, kind: u32, association: *const u16, extra: *const u16, value: *mut u16, len: *mut u32) -> Hr;
}

// Each interface embeds the IUnknown prefix, then its SDK-declared methods in order.
#[repr(C)]
struct Unknown {
    query: usize,
    add_ref: usize,
    release: unsafe extern "system" fn(Raw) -> u32,
}

#[repr(C)]
struct FileDialog {
    unknown: Unknown,
    show: unsafe extern "system" fn(Raw, Raw) -> Hr,
    file_types_and_events: [usize; 5],
    set_options: unsafe extern "system" fn(Raw, u32) -> Hr,
    get_options: unsafe extern "system" fn(Raw, *mut u32) -> Hr,
    folders_and_selection: [usize; 4],
    set_file_name: unsafe extern "system" fn(Raw, *const u16) -> Hr,
    get_file_name: usize,
    set_title: unsafe extern "system" fn(Raw, *const u16) -> Hr,
    labels: [usize; 2],
    get_result: unsafe extern "system" fn(Raw, *mut Raw) -> Hr,
}

#[repr(C)]
struct ShellItem {
    unknown: Unknown,
    bind_to_handler: usize,
    get_parent: usize,
    get_display_name: unsafe extern "system" fn(Raw, u32, *mut *mut u16) -> Hr,
}

#[repr(C)]
struct EnumHandlers {
    unknown: Unknown,
    next: unsafe extern "system" fn(Raw, u32, *mut Raw, *mut u32) -> Hr,
}

#[repr(C)]
struct AssocHandler {
    unknown: Unknown,
    get_name: unsafe extern "system" fn(Raw, *mut *mut u16) -> Hr,
    get_ui_name: unsafe extern "system" fn(Raw, *mut *mut u16) -> Hr,
}

struct Apartment;
impl Apartment {
    fn enter() -> io::Result<Self> {
        check(unsafe { CoInitializeEx(ptr::null_mut(), 0x02 | 0x04) })?;
        Ok(Self)
    }
}
impl Drop for Apartment {
    fn drop(&mut self) {
        unsafe { CoUninitialize() };
    }
}

struct Com(Raw);
impl Com {
    fn receive(call: impl FnOnce(*mut Raw) -> Hr) -> io::Result<Self> {
        let mut pointer = ptr::null_mut();
        check(call(&mut pointer))?;
        if pointer.is_null() {
            return Err(io::Error::other("The Windows Shell did not return an interface"));
        }
        Ok(Self(pointer))
    }

    // The caller chooses the vtable type corresponding to the requested COM interface.
    unsafe fn vtable<T>(&self) -> &T {
        unsafe { &**self.0.cast::<*const T>() }
    }
}
impl Drop for Com {
    fn drop(&mut self) {
        unsafe { (self.vtable::<Unknown>().release)(self.0) };
    }
}

fn check(result: Hr) -> io::Result<()> {
    if result < 0 {
        Err(io::Error::other(format!("Windows Shell error 0x{:08x}", result as u32)))
    } else {
        Ok(())
    }
}

fn wide(text: &OsStr) -> Vec<u16> {
    text.encode_wide().chain([0]).collect()
}

fn shell_string(call: impl FnOnce(*mut *mut u16) -> Hr) -> io::Result<OsString> {
    let mut pointer = ptr::null_mut();
    check(call(&mut pointer))?;
    if pointer.is_null() {
        return Err(io::Error::other("The Windows Shell returned no path"));
    }
    // Shell methods return NUL-terminated strings owned by the COM allocator.
    let value = unsafe {
        let mut len = 0;
        while *pointer.add(len) != 0 {
            len += 1;
        }
        let value = OsString::from_wide(std::slice::from_raw_parts(pointer, len));
        CoTaskMemFree(pointer.cast());
        value
    };
    Ok(value)
}

pub(super) fn dialog(prompt: &str, name: Option<&str>) -> io::Result<Option<PathBuf>> {
    let _apartment = Apartment::enter()?;
    let (class, flags) = if name.is_some() {
        (&FILE_SAVE_DIALOG, OVERWRITE_PROMPT | PATH_MUST_EXIST)
    } else {
        (&FILE_OPEN_DIALOG, PICK_FOLDERS)
    };
    let dialog = Com::receive(|out| unsafe {
        CoCreateInstance(class, ptr::null_mut(), 1, &IFILE_DIALOG, out)
    })?;
    let table = unsafe { dialog.vtable::<FileDialog>() };
    let mut current = 0;
    unsafe {
        check((table.get_options)(dialog.0, &mut current))?;
        check((table.set_options)(dialog.0, current | flags | FORCE_FILESYSTEM))?;
        check((table.set_title)(dialog.0, wide(prompt.as_ref()).as_ptr()))?;
        if let Some(name) = name {
            check((table.set_file_name)(dialog.0, wide(name.as_ref()).as_ptr()))?;
        }
        let result = (table.show)(dialog.0, ptr::null_mut());
        if result == CANCELLED {
            return Ok(None);
        }
        check(result)?;
        let item = Com::receive(|out| (table.get_result)(dialog.0, out))?;
        let path = shell_string(|out| (item.vtable::<ShellItem>().get_display_name)(item.0, FILESYSTEM_PATH, out))?;
        Ok(Some(path.into()))
    }
}

pub(super) fn open(item: &OsStr) -> io::Result<()> {
    let item = wide(item);
    let result = unsafe {
        ShellExecuteW(ptr::null_mut(), ptr::null(), item.as_ptr(), ptr::null(), ptr::null(), 1)
    };
    if result > 32 { Ok(()) } else { Err(io::Error::other(format!("ShellExecuteW failed ({result})"))) }
}

fn executable(path: &Path) -> Option<PathBuf> {
    if !path.is_absolute() {
        return None;
    }
    let path = canonicalize(path).ok()?;
    (path.is_file() && path.extension()?.to_str()?.eq_ignore_ascii_case("exe")).then_some(path)
}

pub(super) fn open_with(file: &Path, app: &Path) -> io::Result<()> {
    if executable(app).as_deref() != Some(app) {
        return Err(io::Error::other("Application path changed"));
    }
    launch(Command::new(app).arg(file).creation_flags(CREATE_NO_WINDOW))
}

fn default_handler(extension: &[u16]) -> Option<PathBuf> {
    const EXECUTABLE: u32 = 2;
    let mut len = 0;
    unsafe { AssocQueryStringW(0, EXECUTABLE, extension.as_ptr(), ptr::null(), ptr::null_mut(), &mut len) };
    if !(2..=32768).contains(&len) {
        return None;
    }
    let mut buffer = vec![0u16; len as usize];
    let result = unsafe { AssocQueryStringW(0, EXECUTABLE, extension.as_ptr(), ptr::null(), buffer.as_mut_ptr(), &mut len) };
    if result != 0 {
        return None;
    }
    let end = buffer.iter().position(|&unit| unit == 0)?;
    executable(Path::new(&OsString::from_wide(&buffer[..end])))
}

pub(super) fn applications(file: &Path) -> Vec<Application> {
    let mut result = Vec::new();
    let Some(extension) = file.extension() else { return result; };
    let mut name = OsString::from(".");
    name.push(extension);
    let extension = wide(&name);
    let mut seen = HashSet::new();
    let mut add = |path: PathBuf, name: Option<OsString>, is_default| {
        if !seen.insert(path.to_string_lossy().to_lowercase()) {
            return;
        }
        let stem = path.file_stem().unwrap_or_default().to_string_lossy();
        let label = match stem.to_ascii_lowercase().as_str() {
            "notepad" => "记事本".into(),
            "code" => "Visual Studio Code".into(),
            "cursor" => "Cursor".into(),
            "wordpad" => "写字板".into(),
            _ => name.filter(|name| !name.is_empty()).map_or_else(|| stem.into_owned(), |name| name.to_string_lossy().into_owned()),
        };
        result.push(Application { path, label, bundle_id: None, is_default });
    };
    if let Some(path) = default_handler(&extension) {
        add(path, None, true);
    }
    let Ok(_apartment) = Apartment::enter() else { return result; };
    // ASSOC_FILTER_NONE asks Windows for its complete Open With list.
    if let Ok(handlers) = Com::receive(|out| unsafe { SHAssocEnumHandlers(extension.as_ptr(), 0, out) }) {
        let table = unsafe { handlers.vtable::<EnumHandlers>() };
        for _ in 0..128 {
            let Ok(handler) = Com::receive(|out| unsafe { (table.next)(handlers.0, 1, out, ptr::null_mut()) }) else { break; };
            let table = unsafe { handler.vtable::<AssocHandler>() };
            let name = unsafe { shell_string(|out| (table.get_name)(handler.0, out)) };
            if let Some(path) = name.ok().and_then(|name| executable(Path::new(&name))) {
                let label = unsafe { shell_string(|out| (table.get_ui_name)(handler.0, out)) }.ok();
                add(path, label, false);
            }
        }
    }
    result.truncate(40);
    result
}
