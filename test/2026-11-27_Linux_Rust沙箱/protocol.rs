use std::collections::BTreeMap;

use astrion_linux_sandbox::protocol::{Network, Request, RunRequest, mount_path};
use serde_json::{Value, json};

fn request() -> RunRequest {
    RunRequest {
        workspace: "/home/dev/project".into(),
        cwd: "/home/dev/project/subdir".into(),
        reads: vec!["/home/dev/reference".into()],
        writes: vec![],
        readonly: false,
        workspace_only: true,
        network: Network::Restricted,
        argv: vec!["/bin/bash".into(), "-c".into(), "printf '中文'".into()],
        env: BTreeMap::from([("TERM".into(), "xterm".into())]),
        umask: 0o022,
        workspace_write_root: Some("/home/dev/project/subdir".into()),
    }
}

#[test]
fn request_round_trips_and_enforces_fixed_write_scope() {
    let plan = request();
    plan.validate().unwrap();
    let mut payload = serde_json::to_value(Request::Run(plan)).unwrap();
    assert_eq!(payload["operation"], "run");
    assert_eq!(payload["network"], "restricted");
    assert_eq!(payload["workspace_write_root"], "/home/dev/project/subdir");

    payload["writes"] = json!(["/home/dev/another"]);
    let Request::Run(plan) = serde_json::from_value(payload.clone()).unwrap() else {
        panic!("expected run")
    };
    assert!(plan.validate().is_err());

    payload["writes"] = json!([]);
    payload["workspace_write_root"] = json!("/home/dev/project-other");
    let Request::Run(plan) = serde_json::from_value(payload.clone()).unwrap() else {
        panic!("expected run")
    };
    assert!(plan.validate().is_err());

    payload["workspace_write_root"] = Value::Null;
    assert!(serde_json::from_value::<Request>(payload).is_err());
}

#[test]
fn identity_and_privileged_fields_cannot_be_supplied_by_the_client() {
    let mut payload = serde_json::to_value(Request::Run(request())).unwrap();
    for field in ["uid", "gid", "groups", "helper_path", "unit", "profile"] {
        payload[field] = json!("unexpected");
        assert!(
            serde_json::from_value::<Request>(payload.clone()).is_err(),
            "{field}"
        );
        payload.as_object_mut().unwrap().remove(field);
    }
    assert!(serde_json::from_value::<Request>(json!({"operation":"status","uid":0})).is_err());
    assert!(
        serde_json::from_str::<Request>(r#"{"operation":"run","operation":"status"}"#).is_err()
    );

    let mut plan = request();
    plan.env
        .insert("LD_PRELOAD".into(), "/home/dev/unsafe.so".into());
    assert!(plan.validate().is_err());
}

#[test]
fn mount_paths_reject_kernel_runtime_and_noncanonical_locations() {
    for path in [
        "relative",
        "/",
        "//proc",
        "/proc/self",
        "/dev/pts",
        "/sys/kernel",
        "/.astrion-runtime/lease",
        "/work/../sys",
        "/work/.",
        "/work//nested",
        "/work/",
        "/nul\0path",
    ] {
        assert!(mount_path(path).is_err(), "{path}");
    }
    for path in [
        "/workspace",
        "/work/文件",
        "/process",
        "/.astrion-runtime-other",
    ] {
        assert!(mount_path(path).is_ok(), "{path}");
    }
}
