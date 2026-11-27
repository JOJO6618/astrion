use super::{dispatch, exports, history};
use rusqlite::Connection;
use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

fn csv(content: &str) -> Result<Value, &'static str> {
    dispatch(
        &json!({ "action": "preview-file", "value": { "format": "password-csv", "content": content } }),
    )
}

#[test]
fn csv_keeps_quoted_fields_bom_and_newlines() {
    let result = csv(concat!(
        "\u{feff}name,url,username,password,note\r\n",
        "Chrome,https://EXAMPLE.test:443/login,\"user,中文\",\"synthetic,\"\"quoted\"\"\r\n第二行\",\r\n",
        "Chrome,https://empty.test,empty,,\n",
        "truncated,too,few\r",
        "\r\n"
    )).unwrap();
    assert_eq!(result["passwords"][0]["origin"], "https://example.test");
    assert_eq!(result["passwords"][0]["username"], "user,中文");
    assert_eq!(
        result["passwords"][0]["password"],
        "synthetic,\"quoted\"\r\n第二行"
    );
    assert_eq!(
        result["stats"]["passwords"],
        json!({
            "read": 3, "ready": 1, "skipped": 1, "failed": 1, "conflicts": 0,
            "reasons": { "invalid_record": 1, "not_password": 1 }
        })
    );
}

#[test]
fn csv_rejects_malformed_or_oversized_exports() {
    for input in [
        "url,url,username,password\nhttps://example.test,,a,b",
        "url,username,password\nhttps://example.test,user,\"broken",
        "url,username,password\nhttps://example.test,user,\"closed\"suffix",
        "url,username,password\nhttps://example.test,user,\0",
    ] {
        assert_eq!(csv(input), Err("invalid_import_file"));
    }
    assert_eq!(
        csv(&"x".repeat(4 * 1024 * 1024 + 1)),
        Err("import_file_too_large")
    );
    let too_many = format!(
        "url,username,password\n{}",
        "https://example.test,user,synthetic\n".repeat(10_001)
    );
    assert_eq!(csv(&too_many), Err("import_file_too_many_records"));
}

#[test]
fn cookie_exports_validate_partition_keys_session_and_expiry() {
    let mut session = json!({
        "domain": "example.test", "name": "synthetic", "value": "example", "path": "/",
        "secure": true, "httpOnly": true, "hostOnly": true, "session": true, "sameSite": "no_restriction"
    });
    session["partitionKey"] =
        json!({ "topLevelSite": "https://top.test/", "hasCrossSiteAncestor": false });
    let mut persistent = session.clone();
    persistent["session"] = false.into();
    persistent["hostOnly"] = false.into();
    persistent["expirationDate"] = 4_102_444_800i64.into();
    persistent["partitionKey"] = Value::Null;
    let mut expired = persistent.clone();
    expired["expirationDate"] = 1.into();
    let mut invalid = session.clone();
    invalid["partitionKey"]["hasCrossSiteAncestor"] = "false".into();
    let bundle = json!({ "format": "astrion-cookie-export", "version": 1, "cookies": [session, persistent, expired, invalid] });
    let result =
        exports::preview(&json!({ "format": "cookie-json", "content": bundle.to_string() }))
            .unwrap();
    assert_eq!(result.cookies[0]["domain"], "example.test");
    assert_eq!(result.cookies[0]["expires"], -1.0);
    assert_eq!(
        result.cookies[0]["partitionKey"],
        json!({ "topLevelSite": "https://top.test", "hasCrossSiteAncestor": false })
    );
    assert_eq!(result.cookies[1]["domain"], ".example.test");
    assert_eq!(result.stats.cookies.reasons["expired"], 1);
    assert_eq!(result.stats.cookies.reasons["invalid_record"], 1);
    let invalid = json!({ "format": "astrion-cookie-export", "version": 1.0, "cookies": [] });
    assert_eq!(
        exports::preview(&json!({ "format": "cookie-json", "content": invalid.to_string() })).err(),
        Some("invalid_import_file")
    );
}

struct Fixture(PathBuf);

impl Fixture {
    fn new() -> Self {
        let serial = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let base = std::env::temp_dir().canonicalize().unwrap();
        let directory = base.join(format!(
            "astrion-chrome-test-{}-{serial}",
            std::process::id()
        ));
        fs::create_dir(&directory).unwrap();
        Self(directory)
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn lists_chrome_profiles_and_reads_committed_history_without_writing_records() {
    let fixture = Fixture::new();
    let root = &fixture.0;
    for dir in ["Default", "Profile 7", "Other"] {
        fs::create_dir(root.join(dir)).unwrap();
    }
    fs::write(
        root.join("Local State"),
        json!({
            "profile": { "info_cache": { "Default": { "name": "合成用户" } } },
        })
        .to_string(),
    )
    .unwrap();
    let path = root.join("Default/History");
    let db = Connection::open(&path).unwrap();
    db.execute_batch(
        "
        PRAGMA journal_mode=WAL;
        CREATE TABLE urls (url TEXT, title TEXT, last_visit_time INTEGER);
        INSERT INTO urls VALUES ('https://history.example.test/', '', 11644473600010000);
        INSERT INTO urls VALUES ('about:blank', 'Internal', 11644473600010000);
        INSERT INTO urls VALUES ('https://', 'Bad URL', 11644473600010000);
        INSERT INTO urls VALUES ('https://history.example.test/', 'Bad time', 0);
    ",
    )
    .unwrap();
    let before = fs::read(&path).unwrap();
    let wal_before = fs::read(root.join("Default/History-wal")).unwrap();
    assert_eq!(
        json!(history::sources(root).unwrap()),
        json!([
            { "id": "Default", "name": "合成用户" }, { "id": "Profile 7", "name": "Profile 7" },
        ])
    );
    let result = history::preview(root, "Default").unwrap();
    assert_eq!(result.stats.history.reasons["history_non_web_url"], 1);
    assert_eq!(result.stats.history.reasons["history_invalid_url"], 1);
    assert_eq!(result.stats.history.reasons["history_invalid_time"], 1);
    assert_eq!(
        result.history,
        vec![
            json!({ "url": "https://history.example.test/", "title": "https://history.example.test/", "at": 10 })
        ]
    );
    assert_eq!(fs::read(&path).unwrap(), before);
    assert_eq!(
        fs::read(root.join("Default/History-wal")).unwrap(),
        wal_before
    );
    assert_eq!(
        history::preview(root, "Profile 7")
            .unwrap()
            .stats
            .history
            .reasons["missing_database"],
        1
    );
    assert_eq!(
        history::preview(root, "../Default").err(),
        Some("invalid_source")
    );
    assert_eq!(
        dispatch(&json!({ "action": "preview" })),
        Err("chrome_read_failed")
    );
}
