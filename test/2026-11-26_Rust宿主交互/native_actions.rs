use super::{platform::{Application, Platform}, save::{open_regular, save_with}};
use std::{fs, io::{self, Seek, SeekFrom}, path::{Path, PathBuf}, sync::Mutex};

struct Dialog {
    target: Option<PathBuf>,
    suggested_name: Mutex<String>,
}

impl Dialog {
    fn new(target: Option<PathBuf>) -> Self {
        Self { target, suggested_name: Mutex::default() }
    }
}

impl Platform for Dialog {
    fn has_dialogs(&self) -> bool { true }
    fn save_dialog(&self, _: &str, filename: &str) -> io::Result<Option<PathBuf>> {
        filename.clone_into(&mut *self.suggested_name.lock().unwrap());
        Ok(self.target.clone())
    }
    fn pick_folder(&self, _: &str) -> io::Result<Option<PathBuf>> { unreachable!() }
    fn open_path(&self, _: &Path) -> io::Result<()> { unreachable!() }
    fn open_url(&self, _: &str) -> io::Result<()> { unreachable!() }
    fn open_with(&self, _: &Path, _: &Path) -> io::Result<()> { unreachable!() }
    fn applications(&self, _: &Path) -> Vec<Application> { unreachable!() }
    fn app_icon(&self, _: &Path) -> io::Result<Vec<u8>> { unreachable!() }
}

struct Fixture(PathBuf);

impl Fixture {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("astrion-native-save-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&path).unwrap();
        Self(path.canonicalize().unwrap())
    }
}

impl Drop for Fixture {
    fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); }
}

#[test]
fn native_save_uses_open_source_and_dialog_leaf() {
    let fixture = Fixture::new();
    let source = fixture.0.join("source.txt");
    let destination = fixture.0.join("destination.txt");
    fs::write(&source, "selected source").unwrap();
    fs::write(&destination, "old destination").unwrap();
    let mut reader = open_regular(&source).unwrap();
    reader.seek(SeekFrom::End(0)).unwrap();
    fs::rename(&source, fixture.0.join("moved.txt")).unwrap();
    fs::write(&source, "unrelated replacement").unwrap();

    let dialog = Dialog::new(Some(destination.clone()));
    assert_eq!(save_with(&dialog, reader, &source, "../nested/final:name.txt", true).unwrap(), Some(destination.clone()));
    assert_eq!(&*dialog.suggested_name.lock().unwrap(), "final_name.txt");
    assert_eq!(fs::read_to_string(&destination).unwrap(), "selected source");
    assert_eq!(fs::read_to_string(&source).unwrap(), "unrelated replacement");
    let cancel = Dialog::new(None);
    assert!(save_with(&cancel, open_regular(&source).unwrap(), &source, "..", true).unwrap().is_none());
    assert_eq!(&*cancel.suggested_name.lock().unwrap(), "source.txt");
    assert_eq!(fs::read_to_string(&destination).unwrap(), "selected source");
}

#[cfg(unix)]
#[test]
fn native_save_replaces_the_selected_link_without_writing_its_target() {
    let fixture = Fixture::new();
    let source = fixture.0.join("source.txt");
    let private = fixture.0.join("private.txt");
    let selected = fixture.0.join("selected.txt");
    fs::write(&source, "exported fixture").unwrap();
    fs::write(&private, "private fixture").unwrap();
    std::os::unix::fs::symlink(&private, &selected).unwrap();
    assert!(open_regular(&selected).is_err());
    let dialog = Dialog::new(Some(selected.clone()));

    save_with(&dialog, open_regular(&source).unwrap(), &source, "copy.txt", false).unwrap();
    assert!(!fs::symlink_metadata(&selected).unwrap().is_symlink());
    assert_eq!(fs::read_to_string(selected).unwrap(), "exported fixture");
    assert_eq!(fs::read_to_string(private).unwrap(), "private fixture");
}
