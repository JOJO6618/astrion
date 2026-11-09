//! Per-Astrion-user vault, encrypted with the current Windows user's DPAPI key.
use super::{model, policy};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use windows::Win32::{
    Foundation::{LocalFree, HLOCAL},
    Security::Cryptography::*,
};

pub struct Vault {
    file: PathBuf,
    pub data: Value,
    error: Option<String>,
}
fn regular(path: &Path) -> Result<Option<std::fs::File>, String> {
    use std::os::windows::{
        fs::{MetadataExt, OpenOptionsExt},
        io::AsRawHandle,
    };
    use windows::Win32::{Foundation::HANDLE, Storage::FileSystem::*};
    let file = match std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT.0)
        .open(path)
    {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.to_string()),
    };
    let meta = file.metadata().map_err(|e| e.to_string())?;
    let mut info = BY_HANDLE_FILE_INFORMATION::default();
    unsafe {
        GetFileInformationByHandle(HANDLE(file.as_raw_handle()), &mut info)
            .map_err(|e| e.to_string())?;
    }
    if !meta.is_file()
        || meta.file_attributes() & 0x400 != 0
        || info.nNumberOfLinks != 1
        || meta.len() > 64 * 1024 * 1024
    {
        return Err("browser_vault_invalid_file".into());
    }
    Ok(Some(file))
}
fn protect(bytes: &[u8], encrypt: bool) -> Result<Vec<u8>, String> {
    let input = CRYPT_INTEGER_BLOB {
        cbData: bytes.len() as u32,
        pbData: bytes.as_ptr() as *mut u8,
    };
    let mut output = CRYPT_INTEGER_BLOB::default();
    unsafe {
        let result = if encrypt {
            CryptProtectData(
                &input,
                None,
                None,
                None,
                None,
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        } else {
            CryptUnprotectData(
                &input,
                None,
                None,
                None,
                None,
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        };
        result.map_err(|_| "browser_vault_windows_decryption_failed")?;
        let value = std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec();
        std::slice::from_raw_parts_mut(output.pbData, output.cbData as usize).fill(0);
        let _ = LocalFree(Some(HLOCAL(output.pbData as _)));
        Ok(value)
    }
}
impl Vault {
    pub fn open(directory: &Path) -> Self {
        Self::load(directory).unwrap_or_else(|error| Self {
            file: directory.join("passwords.dpapi"),
            data: json!({"version":1,"records":[],"settings":{"save":false,"autofill":false}}),
            error: Some(error),
        })
    }
    pub fn ready(&self) -> Result<(), String> {
        self.error.clone().map_or(Ok(()), Err)
    }
    pub fn load(directory: &Path) -> Result<Self, String> {
        let file = directory.join("passwords.dpapi");
        let data = if let Some(mut handle) = regular(&file)? {
            use std::io::Read;
            let mut encrypted = Vec::new();
            handle
                .read_to_end(&mut encrypted)
                .map_err(|e| e.to_string())?;
            let mut plaintext = protect(&encrypted, false)?;
            let result = serde_json::from_slice::<Value>(&plaintext)
                .map_err(|_| "browser_vault_invalid_data");
            plaintext.fill(0);
            let result = result?;
            if result["version"] != 1 || !result["records"].is_array() {
                return Err("browser_vault_invalid_data".into());
            }
            result
        } else {
            json!({"version":1,"records":[],"settings":{"save":true,"autofill":true}})
        };
        Ok(Self {
            file,
            data,
            error: None,
        })
    }
    pub fn metadata(row: &Value) -> Value {
        json!({"id":row["id"],"origin":row["origin"],"username":row["username"],"updatedAt":row["updatedAt"],"usedAt":row["usedAt"]})
    }
    pub fn list(&self) -> Value {
        json!({"records":self.data["records"].as_array().unwrap().iter().map(Self::metadata).collect::<Vec<_>>(),"settings":self.data["settings"]})
    }
    pub fn get(&self, id: &str) -> Result<Value, String> {
        self.ready()?;
        self.data["records"]
            .as_array()
            .unwrap()
            .iter()
            .find(|r| r["id"] == id)
            .cloned()
            .ok_or("browser_password_not_found".into())
    }
    pub fn matches(&self, origin: &str) -> Vec<Value> {
        self.data["records"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|r| r["origin"] == origin)
            .cloned()
            .collect()
    }
    pub fn persist(&mut self, next: Value) -> Result<(), String> {
        self.ready()?;
        regular(&self.file)?;
        let mut plaintext = serde_json::to_vec(&next).map_err(|e| e.to_string())?;
        let encrypted = protect(&plaintext, true);
        plaintext.fill(0);
        model::atomic(&self.file, &encrypted?)?;
        self.data = next;
        Ok(())
    }
    pub fn save(&mut self, value: &Value, id: Option<&str>) -> Result<Value, String> {
        let origin = policy::origin(value["origin"].as_str().ok_or("invalid_password_origin")?)?;
        let username = value["username"]
            .as_str()
            .filter(|s| s.len() <= 16384)
            .ok_or("invalid_password_username")?;
        let current = id.map(|id| self.get(id)).transpose()?;
        let password = value["password"]
            .as_str()
            .filter(|s| !s.is_empty())
            .or_else(|| current.as_ref().and_then(|r| r["password"].as_str()))
            .filter(|s| s.len() <= 65536)
            .ok_or("invalid_password_value")?;
        let mut next = self.data.clone();
        let rows = next["records"].as_array_mut().unwrap();
        if rows.iter().any(|r| {
            r["origin"] == origin
                && r["username"] == username
                && Some(r["id"].as_str().unwrap_or("")) != id
        }) {
            return Err("browser_password_duplicate".into());
        }
        if rows.len() >= 50000 && current.is_none() {
            return Err("browser_password_limit".into());
        }
        let row = json!({"id":id.map(str::to_owned).unwrap_or_else(policy::id),"origin":origin,"username":username,"password":password,"updatedAt":policy::now().max(current.as_ref().and_then(|r|r["updatedAt"].as_u64()).unwrap_or(0)+1),"usedAt":current.as_ref().and_then(|r|r["usedAt"].as_u64()).unwrap_or(0)});
        if let Some(existing) = rows.iter_mut().find(|r| r["id"] == row["id"]) {
            *existing = row.clone();
        } else {
            rows.push(row.clone());
        }
        self.persist(next)?;
        Ok(Self::metadata(&row))
    }
    pub fn command(&mut self, action: &str, input: &Value) -> Result<Value, String> {
        self.ready()?;
        match action {
            "list" => Ok(self.list()),
            "save" => {
                self.save(&input["value"], input["id"].as_str())?;
                Ok(self.list())
            }
            "remove" => {
                let id = input["id"].as_str().ok_or("invalid_password_id")?;
                self.get(id)?;
                let mut next = self.data.clone();
                next["records"]
                    .as_array_mut()
                    .unwrap()
                    .retain(|r| r["id"] != id);
                self.persist(next)?;
                Ok(self.list())
            }
            "settings" => {
                let value = &input["value"];
                if !value["save"].is_boolean() || !value["autofill"].is_boolean() {
                    return Err("invalid_password_settings".into());
                }
                let mut next = self.data.clone();
                next["settings"] = value.clone();
                self.persist(next)?;
                Ok(self.list())
            }
            "reveal" | "copy" => {
                let row = self.get(input["id"].as_str().ok_or("invalid_password_id")?)?;
                Ok(json!({"password":row["password"]}))
            }
            _ => Err("unsupported_browser_credentials_action".into()),
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn dpapi_roundtrip() {
        let encrypted = protect(b"synthetic browser credential", true).unwrap();
        assert!(!encrypted.windows(9).any(|w| w == b"synthetic"));
        assert_eq!(
            protect(&encrypted, false).unwrap(),
            b"synthetic browser credential"
        );
    }
    #[test]
    fn encrypted_file_survives_reopen_and_rejects_duplicate() {
        let directory = std::env::temp_dir().join(format!("astrion-vault-test-{}", policy::id()));
        std::fs::create_dir(&directory).unwrap();
        let mut vault = Vault::load(&directory).unwrap();
        let input = json!({"origin":"https://example.test/login","username":"synthetic-user","password":"synthetic-password"});
        let row = vault.save(&input, None).unwrap();
        let bytes = std::fs::read(directory.join("passwords.dpapi")).unwrap();
        assert!(!bytes.windows(18).any(|part| part == b"synthetic-password"));
        assert!(vault.save(&input, None).is_err());
        drop(vault);
        let vault = Vault::load(&directory).unwrap();
        assert_eq!(
            vault.get(row["id"].as_str().unwrap()).unwrap()["password"],
            "synthetic-password"
        );
        drop(vault);
        std::fs::write(directory.join("passwords.dpapi"), b"damaged").unwrap();
        let mut broken = Vault::open(&directory);
        assert!(broken.command("list", &json!({})).is_err());
        assert!(broken.persist(json!({})).is_err());
        assert_eq!(
            std::fs::read(directory.join("passwords.dpapi")).unwrap(),
            b"damaged"
        );
        std::fs::remove_dir_all(directory).unwrap();
    }
    #[test]
    fn rejects_hard_linked_vault() {
        let directory =
            std::env::temp_dir().join(format!("astrion-vault-link-test-{}", policy::id()));
        std::fs::create_dir(&directory).unwrap();
        let file = directory.join("passwords.dpapi");
        std::fs::write(&file, b"synthetic").unwrap();
        std::fs::hard_link(&file, directory.join("alias")).unwrap();
        assert!(Vault::load(&directory).is_err());
        std::fs::remove_dir_all(directory).unwrap();
    }
    #[test]
    fn metadata_never_contains_secret() {
        assert!(Vault::metadata(&json!({"id":"x","password":"secret"}))
            .get("password")
            .is_none());
    }
}
