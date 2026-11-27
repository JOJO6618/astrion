use fs2::FileExt;
use scrypt::{Params, scrypt};
use serde::{Deserialize, Serialize};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::Path;
use subtle::ConstantTimeEq;

const FILENAME: &str = "host_auth.json";
const MAX_FILE_BYTES: u64 = 65_536;
const HASH_METHOD: &str = "scrypt:32768:8:1";

#[derive(Default)]
pub(super) struct Credentials {
    pub enabled: bool,
    pub generation: String,
    hash: Option<PasswordHash>,
}

struct PasswordHash {
    salt: String,
    digest: [u8; 64],
}

#[derive(Deserialize, Serialize)]
struct StoredCredentials {
    version: u64,
    enabled: bool,
    password_hash: String,
    generation: String,
}

pub(super) enum DisableError {
    Changed,
    Incorrect,
    Unavailable,
}

impl Credentials {
    pub fn load(directory: &Path) -> io::Result<Self> {
        let mut options = OpenOptions::new();
        options.read(true);
        nofollow(&mut options);
        let mut file = match options.open(directory.join(FILENAME)) {
            Ok(file) => file,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(Self::default()),
            Err(e) => return Err(e),
        };
        let metadata = file.metadata()?;
        if !is_regular(&metadata) || metadata.len() > MAX_FILE_BYTES {
            return Err(invalid());
        }
        let mut payload = Vec::new();
        (&mut file)
            .take(MAX_FILE_BYTES + 1)
            .read_to_end(&mut payload)?;
        if payload.len() as u64 > MAX_FILE_BYTES {
            return Err(invalid());
        }
        let stored: StoredCredentials = serde_json::from_slice(&payload).map_err(|_| invalid())?;
        if stored.version != 1 || stored.generation.chars().count() < 16 {
            return Err(invalid());
        }
        let hash = stored
            .enabled
            .then(|| PasswordHash::parse(&stored.password_hash))
            .transpose()?;
        Ok(Self {
            enabled: stored.enabled,
            generation: stored.generation,
            hash,
        })
    }

    pub fn verify(&self, password: Option<&str>) -> io::Result<bool> {
        let Some((hash, password)) = self.hash.as_ref().zip(password) else {
            return Ok(false);
        };
        if !(8..=1024).contains(&password.chars().count()) {
            return Ok(false);
        }
        let parameters = Params::new(15, 8, 1, 64).map_err(|_| invalid())?;
        let mut derived = [0; 64];
        scrypt(
            password.as_bytes(),
            hash.salt.as_bytes(),
            &parameters,
            &mut derived,
        )
        .map_err(|_| invalid())?;
        Ok(bool::from(derived.ct_eq(&hash.digest)))
    }
}

impl PasswordHash {
    fn parse(hash: &str) -> io::Result<Self> {
        let mut fields = hash.split('$');
        let (Some(HASH_METHOD), Some(salt), Some(hex), None) =
            (fields.next(), fields.next(), fields.next(), fields.next())
        else {
            return Err(invalid());
        };
        if salt.is_empty() || hex.len() != 128 {
            return Err(invalid());
        }
        let mut digest = [0; 64];
        let (pairs, _) = hex.as_bytes().as_chunks::<2>();
        for (byte, pair) in digest.iter_mut().zip(pairs) {
            *byte = (hex_digit(pair[0])? << 4) | hex_digit(pair[1])?;
        }
        Ok(Self {
            salt: salt.to_owned(),
            digest,
        })
    }
}

fn hex_digit(digit: u8) -> io::Result<u8> {
    match digit {
        b'0'..=b'9' => Ok(digit - b'0'),
        b'a'..=b'f' => Ok(digit - b'a' + 10),
        _ => Err(invalid()),
    }
}

fn invalid() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidData,
        "Invalid Host password configuration",
    )
}

fn nofollow(options: &mut OpenOptions) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options
            .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
            .mode(0o600);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        options.custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    }
}

fn is_regular(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return false;
        }
    }
    metadata.is_file()
}

fn lock_credentials(directory: &Path) -> io::Result<File> {
    let mut options = OpenOptions::new();
    options.read(true).write(true).create(true).truncate(false);
    nofollow(&mut options);
    let file = options.open(directory.join(".host_auth.lock"))?;
    let metadata = file.metadata()?;
    if !is_regular(&metadata) {
        return Err(invalid());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.nlink() != 1 {
            return Err(invalid());
        }
    }
    // Python uses flock on Unix and locks the first byte with msvcrt on Windows.
    FileExt::lock_exclusive(&file)?;
    Ok(file)
}

pub(super) fn disable(
    directory: &Path,
    expected_generation: &str,
    new_generation: String,
    password: Option<&str>,
) -> Result<(), DisableError> {
    let _lock = lock_credentials(directory).map_err(|_| DisableError::Unavailable)?;
    let current = Credentials::load(directory).map_err(|_| DisableError::Unavailable)?;
    if !current.enabled || current.generation != expected_generation {
        return Err(DisableError::Changed);
    }
    if !current
        .verify(password)
        .map_err(|_| DisableError::Unavailable)?
    {
        return Err(DisableError::Incorrect);
    }
    let disabled = StoredCredentials {
        version: 1,
        enabled: false,
        password_hash: String::new(),
        generation: new_generation,
    };
    save(directory, &disabled).map_err(|_| DisableError::Unavailable)
}

/// Local administrator password reset; shares the web service's lock and generation scheme.
pub fn set_host_password(directory: &Path, password: &str) -> io::Result<()> {
    if !(8..=1024).contains(&password.chars().count()) {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "密码长度必须为 8 到 1024 个字符"));
    }
    let salt = uuid::Uuid::new_v4().simple().to_string();
    let parameters = Params::new(15, 8, 1, 64).map_err(|_| invalid())?;
    let mut digest = [0; 64];
    scrypt(password.as_bytes(), salt.as_bytes(), &parameters, &mut digest).map_err(|_| invalid())?;
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    fs::create_dir_all(directory)?;
    let _lock = lock_credentials(directory)?;
    save(directory, &StoredCredentials {
        version: 1, enabled: true, password_hash: format!("{HASH_METHOD}${salt}${hex}"),
        generation: uuid::Uuid::new_v4().simple().to_string(),
    })
}

fn save(directory: &Path, credentials: &StoredCredentials) -> io::Result<()> {
    let target = directory.join(FILENAME);
    match fs::symlink_metadata(&target) {
        Ok(metadata) if !is_regular(&metadata) => return Err(invalid()),
        Err(error) if error.kind() != io::ErrorKind::NotFound => return Err(error),
        _ => {}
    }
    let temporary = directory.join(format!(".host_auth.{}.tmp", credentials.generation));
    let mut options = OpenOptions::new();
    options.create_new(true).write(true);
    nofollow(&mut options);
    let mut file = options.open(&temporary)?;
    let result = (|| {
        serde_json::to_writer(&mut file, credentials)?;
        file.write_all(b"\n")?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, target)
    })();
    if result.is_err() {
        let _ = fs::remove_file(temporary);
    }
    result
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/auth_credentials.rs"]
mod tests;
