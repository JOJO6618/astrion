use super::*;
use crate::web_auth::tests::{FIRST_GENERATION, HASH, PASSWORD, Scratch, write_config};
use serde_json::json;

#[test]
fn reads_old_storage_and_writes_the_same_disabled_schema() {
    let scratch = Scratch::new();
    let absent = Credentials::load(&scratch.0).unwrap();
    assert!(!absent.enabled);
    assert_eq!(absent.generation, "");
    assert!(!scratch.0.join(FILENAME).exists());
    write_config(&scratch.0, FIRST_GENERATION, true);
    let state = Credentials::load(&scratch.0).unwrap();
    assert!(state.enabled);
    assert!(state.verify(Some(PASSWORD)).unwrap());
    assert!(!state.verify(Some(PASSWORD.trim())).unwrap());
    assert!(!state.verify(None).unwrap());
    assert!(matches!(
        disable(
            &scratch.0,
            "stale-generation",
            "unused-generation".to_owned(),
            Some(PASSWORD)
        ),
        Err(DisableError::Changed)
    ));
    let new_generation = "synthetic-replacement-generation";
    assert!(
        disable(
            &scratch.0,
            FIRST_GENERATION,
            new_generation.to_owned(),
            Some(PASSWORD)
        )
        .is_ok()
    );
    let saved: serde_json::Value =
        serde_json::from_slice(&fs::read(scratch.0.join(FILENAME)).unwrap()).unwrap();
    assert_eq!(
        saved,
        json!({"version": 1, "enabled": false, "generation": new_generation, "password_hash": ""})
    );
    assert_eq!(fs::read_dir(&scratch.0).unwrap().count(), 2);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        for name in [FILENAME, ".host_auth.lock"] {
            assert_eq!(
                fs::metadata(scratch.0.join(name))
                    .unwrap()
                    .permissions()
                    .mode()
                    & 0o777,
                0o600
            );
        }
    }
}

#[test]
fn rejects_invalid_configuration_instead_of_treating_it_as_missing() {
    let scratch = Scratch::new();
    let path = scratch.0.join(FILENAME);
    for payload in [
        json!({"version": 2, "enabled": false, "generation": FIRST_GENERATION, "password_hash": ""}),
        json!({"version": 1, "enabled": true, "generation": FIRST_GENERATION, "password_hash": HASH.to_ascii_uppercase()}),
        json!({"version": 1, "enabled": true, "generation": "too-short", "password_hash": HASH}),
        json!({"version": 1, "enabled": true, "generation": FIRST_GENERATION, "password_hash": "scrypt:32768:8:1$$1234"}),
        json!({"version": 1, "enabled": false, "generation": FIRST_GENERATION, "password_hash": null}),
    ] {
        fs::write(&path, serde_json::to_vec(&payload).unwrap()).unwrap();
        assert!(Credentials::load(&scratch.0).is_err());
    }
    fs::write(&path, vec![b' '; MAX_FILE_BYTES as usize + 1]).unwrap();
    assert!(Credentials::load(&scratch.0).is_err());
    fs::remove_file(&path).unwrap();
    fs::create_dir(&path).unwrap();
    assert!(Credentials::load(&scratch.0).is_err());
}

#[cfg(unix)]
#[test]
fn rejects_symlinked_config_and_lock() {
    use std::os::unix::fs::symlink;
    let scratch = Scratch::new();
    let other = Scratch::new();
    write_config(&other.0, FIRST_GENERATION, true);
    let path = scratch.0.join(FILENAME);
    symlink(other.0.join(FILENAME), &path).unwrap();
    assert!(Credentials::load(&scratch.0).is_err());
    fs::remove_file(other.0.join(FILENAME)).unwrap();
    assert!(Credentials::load(&scratch.0).is_err());
    fs::remove_file(&path).unwrap();
    write_config(&scratch.0, FIRST_GENERATION, true);
    let sentinel = other.0.join("sentinel");
    fs::write(&sentinel, b"untouched").unwrap();
    symlink(&sentinel, scratch.0.join(".host_auth.lock")).unwrap();
    assert!(matches!(
        disable(
            &scratch.0,
            FIRST_GENERATION,
            "synthetic-generation".to_owned(),
            Some(PASSWORD)
        ),
        Err(DisableError::Unavailable)
    ));
    assert_eq!(fs::read(sentinel).unwrap(), b"untouched");
    assert!(Credentials::load(&scratch.0).unwrap().enabled);
}
