use super::*;

#[test]
fn child_prompt_expands_all_fields_and_uses_fixed_access() {
    let root = Path::new("fixture-workspace");
    let scoped = system_base(root, Access::Workspace, 1_800_000_000.0);
    let direct = system_base(root, Access::Full, 1_800_000_000.0);
    for text in [&scoped, &direct] {
        for slot in ["{execution_env_section}", "{system_info}", "{current_time}", "{workspace_path}"] {
            assert!(!text.contains(slot), "unexpanded {slot}");
        }
        assert!(text.contains("- 工作区路径: fixture-workspace"));
        assert!(text.contains(&format!("- 系统: {}", std::env::consts::OS)));
        assert!(text.contains("- 当前时间: 2027-"));
    }
    assert!(scoped.contains("沙箱"));
    assert!(direct.contains("宿主机直接执行"));
    assert!(scoped.contains("bash"));
    assert!(direct.contains(if cfg!(windows) { "Windows cmd" } else { "bash" }));
}

#[test]
fn existing_rust_context_repair_keeps_appendices_and_does_not_touch_imports() {
    let record = json!({"access_level": "workspace_write", "created_at": 1_800_000_000.0});
    let suffix = "\nprevious fixed access and memory";
    let mut context = Context::new(&record, &(SYSTEM_TEMPLATE.to_owned() + suffix));
    repair_legacy_template(&mut context, Path::new("existing"), &record);
    let text = context.document["messages"][0]["content"].as_str().unwrap();
    assert!(text.contains("- 工作区路径: existing"));
    assert!(text.ends_with(suffix));
    assert!(context.rewrite_history);
    let mut imported = Context::new(&record, "Original imported prompt.");
    repair_legacy_template(&mut imported, Path::new("existing"), &record);
    assert_eq!(imported.document["messages"][0]["content"], "Original imported prompt.");
    assert!(!imported.rewrite_history);
}
