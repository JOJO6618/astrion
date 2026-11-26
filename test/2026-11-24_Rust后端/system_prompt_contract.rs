use super::*;

#[test]
fn migrates_old_segments_in_python_order_once() {
    let mut metadata = json!({"frozen_disabled_tools_prompt":"disabled", "frozen_permission_prompt":"permission", "frozen_main_system_prompt":"exact main\n", "unknown":{"keep":true}}).as_object().unwrap().clone();
    freeze_metadata(&mut metadata, "provider", |_| panic!("old snapshot must not be rebuilt")).unwrap();
    assert_eq!(metadata[KEY], format!("provider\n\nexact main\n\n\npermission\n\n{LEGACY_COLLABORATION}\n\ndisabled"));
    assert!(!metadata.contains_key("frozen_main_system_prompt"));
    assert_eq!(metadata["unknown"], json!({"keep":true}));
    let first = metadata.clone();
    freeze_metadata(&mut metadata, "new prefix", |_| panic!("cached snapshot must not be rebuilt")).unwrap();
    assert_eq!(metadata, first);
}

#[test]
fn initializes_null_and_rejects_corrupt_snapshot() {
    let mut metadata = Map::new();
    metadata.insert(KEY.into(), Value::Null);
    freeze_metadata(&mut metadata, "", |_| Ok("first prompt".into())).unwrap();
    assert_eq!(metadata[KEY], "first prompt");
    metadata.insert(KEY.into(), json!(false));
    assert!(freeze_metadata(&mut metadata, "", |_| panic!()).is_err());
}
