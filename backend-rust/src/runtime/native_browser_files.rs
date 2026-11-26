use std::{
    io::{self, Read, Write},
    path::{Path, PathBuf},
};

use base64::Engine;
use cap_fs_ext::{DirExt, FollowSymlinks, OpenOptionsFollowExt};
use cap_std::{
    ambient_authority,
    fs::{Dir, OpenOptions},
};
use uuid::Uuid;

pub(super) fn screenshot(workspace: &Path, data: &str) -> Result<PathBuf, String> {
    if data.len() > 34 * 1024 * 1024 {
        return Err("浏览器截图过大。".into());
    }
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(data)
        .map_err(|_| "浏览器截图损坏。")?;
    if !bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Err("浏览器截图格式无效。".into());
    }
    let root =
        Dir::open_ambient_dir(workspace, ambient_authority()).map_err(|error| error.to_string())?;
    let folder = open_or_create(&root, ".astrion")
        .and_then(|root| open_or_create(&root, "browser_screenshots"))
        .map_err(|error| error.to_string())?;
    let name = format!("{}.png", Uuid::new_v4().simple());
    let mut flags = OpenOptions::new();
    flags
        .write(true)
        .create_new(true)
        .follow(FollowSymlinks::No);
    let mut file = folder
        .open_with(&name, &flags)
        .map_err(|error| error.to_string())?;
    file.write_all(&bytes).map_err(|error| error.to_string())?;
    Ok(PathBuf::from(".astrion/browser_screenshots").join(name))
}

fn open_or_create(root: &Dir, name: &str) -> io::Result<Dir> {
    match root.create_dir(name) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
        Err(error) => return Err(error),
    }
    root.open_dir_nofollow(name)
}

pub(super) fn screenshot_url(workspace: &Path, file: &Path) -> io::Result<String> {
    let invalid = || {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "Invalid browser screenshot path",
        )
    };
    let name = file
        .strip_prefix(".astrion/browser_screenshots")
        .map_err(|_| invalid())?;
    if name.components().count() != 1 || name.extension().is_none_or(|extension| extension != "png")
    {
        return Err(invalid());
    }
    let root = Dir::open_ambient_dir(workspace, ambient_authority())?;
    let directory = root
        .open_dir_nofollow(".astrion")?
        .open_dir_nofollow("browser_screenshots")?;
    let mut options = OpenOptions::new();
    options.read(true).follow(FollowSymlinks::No);
    let mut bytes = Vec::new();
    directory
        .open_with(name, &options)?
        .take(26 * 1024 * 1024)
        .read_to_end(&mut bytes)?;
    if !bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Err(invalid());
    }
    Ok(format!(
        "data:image/png;base64,{}",
        base64::engine::general_purpose::STANDARD.encode(bytes)
    ))
}
