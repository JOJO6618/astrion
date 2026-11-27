//! 运行期模式切换通知契约：基线对齐、drift 比对、通知消息形态。

use super::*;

fn scratch_dir() -> std::path::PathBuf {
    let unique = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("system clock")
        .as_nanos();
    let root = std::env::temp_dir().join(format!("astrion-mode-notice-{unique}"));
    std::fs::create_dir_all(&root).expect("scratch dir");
    root
}

fn metadata(value: Value) -> Map<String, Value> {
    value.as_object().expect("object metadata").clone()
}

fn base() -> Map<String, Value> {
    metadata(json!({
        "work_mode": "execute",
        "permission_mode": "approval",
        "execution_mode": "sandbox",
        "network_permission": "full",
    }))
}

#[test]
fn aligns_baseline_silently_without_notifying() {
    let root = scratch_dir();
    let mut metadata = base();
    assert!(drift(&root, &mut metadata).is_empty());
    assert_eq!(metadata[BASELINE]["permission_mode"], "approval");
    assert_eq!(metadata[BASELINE]["execution_mode"], "sandbox");
    // 基线已对齐，重复比对不再产生通知。
    assert!(drift(&root, &mut metadata).is_empty());
    std::fs::remove_dir_all(&root).ok();
}

#[test]
fn reports_drift_once_then_stays_quiet() {
    let root = scratch_dir();
    let mut metadata = base();
    assert!(drift(&root, &mut metadata).is_empty());
    metadata.insert("permission_mode".into(), json!("unrestricted"));
    assert_eq!(drift(&root, &mut metadata), vec![("permission_mode", "unrestricted".to_owned())]);
    // 通知注入后同步基线，下一条消息不再重复补发。
    mark_known(&root, &mut metadata);
    assert!(drift(&root, &mut metadata).is_empty());
    std::fs::remove_dir_all(&root).ok();
}

#[test]
fn plan_linkage_reports_work_permission_and_execution() {
    let root = scratch_dir();
    let mut metadata = metadata(json!({
        "work_mode": "execute",
        "permission_mode": "unrestricted",
        "execution_mode": "direct",
        "network_permission": "full",
    }));
    assert!(drift(&root, &mut metadata).is_empty());
    metadata.insert("work_mode".into(), json!("plan"));
    let changes = drift(&root, &mut metadata);
    let kinds: Vec<&str> = changes.iter().map(|(kind, _)| *kind).collect();
    assert_eq!(kinds, vec!["work_mode", "permission_mode", "execution_mode"]);
    assert_eq!(changes[1].1, "readonly");
    assert_eq!(changes[2].1, "sandbox");
    std::fs::remove_dir_all(&root).ok();
}

#[test]
fn ignores_modes_absent_from_metadata() {
    let root = scratch_dir();
    let mut metadata = metadata(json!({ "work_mode": "execute" }));
    assert!(drift(&root, &mut metadata).is_empty());
    let baseline = metadata[BASELINE].as_object().expect("baseline object");
    assert_eq!(baseline.len(), 1);
    assert_eq!(baseline["work_mode"], "execute");
    std::fs::remove_dir_all(&root).ok();
}

#[test]
fn message_carries_compact_runtime_notice_metadata() {
    let notice = message("permission_mode", "readonly");
    assert_eq!(notice["role"], "user");
    assert_eq!(notice["metadata"]["source"], "permission_change");
    assert_eq!(notice["metadata"]["message_source"], "runtime_notice");
    assert_eq!(notice["metadata"]["runtime_mode_notice"], true);
    assert_eq!(notice["metadata"]["visibility"], "compact");
    assert_eq!(notice["metadata"]["starts_work"], false);
    assert_eq!(notice["metadata"]["kind"], "permission_mode");
    assert_eq!(notice["metadata"]["mode"], "readonly");
    let text = notice["content"].as_str().expect("content");
    assert!(text.contains("权限模式：只读（readonly）"), "{text}");
    assert!(text.contains("以本次规则为准"), "{text}");

    let unrestricted = message("permission_mode", "unrestricted");
    let text = unrestricted["content"].as_str().expect("content");
    assert_eq!(unrestricted["metadata"]["mode"], "unrestricted");
    assert!(text.starts_with("权限模式被用户修改为无限制。"), "{text}");
    assert!(text.contains("权限模式：无限制（unrestricted）"), "{text}");
}

#[test]
fn execution_notice_reuses_sandbox_and_direct_rule_text() {
    let direct = message("execution_mode", "direct");
    let text = direct["content"].as_str().expect("content");
    assert_eq!(direct["metadata"]["source"], "execution_change");
    assert!(text.contains("执行环境：完全访问（direct）"), "{text}");
    assert!(text.contains("直接在宿主机执行"), "{text}");
    let sandbox = message("execution_mode", "sandbox");
    let text = sandbox["content"].as_str().expect("content");
    assert!(text.contains("执行环境：受限执行（sandbox）"), "{text}");
    assert!(text.contains("仅在当前任务提供的能力范围内操作。"), "{text}");
}

#[test]
fn work_and_network_notices_use_their_own_sources() {
    let work = message("work_mode", "ask");
    assert_eq!(work["metadata"]["source"], "work_mode_change");
    assert!(work["content"].as_str().expect("content").contains("运行模式：询问（ask）"));
    let network = message("network_permission", "restricted");
    assert_eq!(network["metadata"]["source"], "network_change");
    assert_eq!(network["content"].as_str().expect("content"), "网络权限被用户修改为受限。");
}
