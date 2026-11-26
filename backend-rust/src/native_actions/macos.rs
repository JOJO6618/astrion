use super::{Application, launch, output};
use crate::project_git::canonicalize;
use base64::{Engine, engine::general_purpose::STANDARD};
use serde::Deserialize;
use std::{collections::HashSet, ffi::OsStr, io, path::{Path, PathBuf}, process::Command, time::Duration};

const DIALOG: &str = r#"
on run argv
    set promptText to item 1 of argv
    try
        if (count of argv) is 1 then
            return POSIX path of (choose folder with prompt promptText)
        end if
        return POSIX path of (choose file name with prompt promptText default name (item 2 of argv))
    on error number -128
        return ""
    end try
end run
"#;

const WORKSPACE: &str = include_str!("macos_workspace.js");

pub(super) fn dialog(prompt: &str, name: Option<&str>) -> io::Result<Option<PathBuf>> {
    let mut cmd = Command::new("/usr/bin/osascript");
    cmd.args(["-e", DIALOG, "--", prompt]);
    if let Some(name) = name {
        cmd.arg(name);
    }
    let (status, bytes) = output(&mut cmd, Duration::from_secs(600))?;
    if !status.success() {
        return Err(io::Error::other("The macOS file dialog failed"));
    }
    let bytes = bytes.strip_suffix(b"\n").unwrap_or(&bytes);
    use std::os::unix::ffi::OsStrExt;
    Ok((!bytes.is_empty()).then(|| PathBuf::from(OsStr::from_bytes(bytes))))
}

pub(super) fn open(item: &OsStr) -> io::Result<()> {
    launch(Command::new("/usr/bin/open").arg("--").arg(item))
}

pub(super) fn open_with(file: &Path, app: &Path) -> io::Result<()> {
    if bundle(app).as_deref() != Some(app) {
        return Err(io::Error::other("Application path changed"));
    }
    launch(Command::new("/usr/bin/open").arg("-a").arg(app).arg("--").arg(file))
}

fn workspace(action: &str, path: &Path) -> io::Result<Vec<u8>> {
    let (status, bytes) = output(
        Command::new("/usr/bin/osascript")
            .args(["-l", "JavaScript", "-e", WORKSPACE, "--", action])
            .arg(path),
        Duration::from_secs(10),
    )?;
    if !status.success() {
        return Err(io::Error::other("The macOS workspace service failed"));
    }
    Ok(bytes)
}

#[derive(Deserialize)]
struct Candidate {
    path: PathBuf,
    label: String,
    bundle_id: String,
    is_default: bool,
}

fn bundle(path: &Path) -> Option<PathBuf> {
    if !path.is_absolute() {
        return None;
    }
    let path = canonicalize(path).ok()?;
    let text = path.to_str()?;
    (path.extension().and_then(OsStr::to_str) == Some("app")
        && path.is_dir()
        && path.join("Contents/Info.plist").is_file()
        && ![
            "/__pycache__/", "/Library/Application Support/", "/System/Library/PrivateFrameworks/",
            "/System/Library/CoreServices/", "/System/Library/Services/",
        ].iter().any(|fragment| text.contains(fragment)))
        .then_some(path)
}

pub(super) fn applications(file: &Path) -> Vec<Application> {
    let mut rows: Vec<Candidate> = workspace("apps", file)
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default();
    if rows.is_empty() {
        for (path, label, id) in [
            ("/System/Applications/TextEdit.app", "TextEdit", "com.apple.TextEdit"),
            ("/Applications/Xcode.app", "Xcode", "com.apple.dt.Xcode"),
            ("/Applications/Cursor.app", "Cursor", "com.todesktop.230313mzl4w4u92"),
        ] {
            rows.push(Candidate { path: path.into(), label: label.into(), bundle_id: id.into(), is_default: false });
        }
    }
    rows.sort_by_cached_key(|row| (!row.is_default, row.label.to_lowercase()));
    let mut seen = HashSet::new();
    rows.into_iter().filter_map(|row| {
        let path = bundle(&row.path)?;
        let label = if row.label.is_empty() || row.bundle_id == "com.microsoft.VSCode" || row.label == "Code" {
            row.path.file_stem()?.to_str()?.to_owned()
        } else {
            row.label
        };
        let lower = label.to_lowercase();
        if lower.ends_with("agent") || matches!(lower.as_str(), "finder" | "system settings") || !seen.insert(path.clone()) {
            return None;
        }
        Some(Application { path, label, bundle_id: Some(row.bundle_id), is_default: row.is_default })
    }).take(40).collect()
}

pub(super) fn app_icon(path: &Path) -> io::Result<Vec<u8>> {
    if bundle(path).as_deref() != Some(path) {
        return Err(io::Error::other("Application path changed"));
    }
    let bytes = workspace("icon", path)?;
    STANDARD.decode(bytes.strip_suffix(b"\n").unwrap_or(&bytes)).map_err(io::Error::other)
}
