use super::*;
use serde_json::json;

#[test]
fn only_full_installed_guide_reads_mark_the_checkpoint() {
    for (_, checkpoint, path) in [TERMINAL, SUB_AGENT, COMMAND] {
        let full = json!({"type":"read","success":true,"truncated":false,"path":path,
            "line_start":1,"line_end":5,"total_lines":5});
        assert_eq!(completed_read(&full), Some(checkpoint));
        let mut partial = full.clone();
        partial["line_end"] = json!(2);
        assert_eq!(completed_read(&partial), None);
        let mut truncated = full.clone();
        truncated["truncated"] = json!(true);
        assert_eq!(completed_read(&truncated), None);
        let mut unrelated = full;
        unrelated["path"] = json!(format!("other/{path}"));
        assert_eq!(completed_read(&unrelated), None);
    }
}

#[test]
fn all_four_policy_switches_are_independent_and_readable() {
    let data = std::env::temp_dir().join(format!("astrion-guide-fixture-{}", uuid::Uuid::new_v4()));
    fs::create_dir(&data).unwrap();
    let config = data.join("personalization.json");
    for (setting, (guide, checkpoint, path)) in [
        ("skill_strict_terminal_enabled", TERMINAL),
        (SUB_AGENT_SETTING, SUB_AGENT),
        ("skill_strict_run_command_foreground_enabled", COMMAND),
        ("skill_strict_run_command_background_enabled", COMMAND),
    ] {
        fs::write(&config, json!({setting: true}).to_string()).unwrap();
        assert!(enforce(&data, setting, &json!({})).unwrap_err().contains(path));
        assert!(enforce(&data, setting, &json!({checkpoint: true})).is_ok());
        fs::write(&config, json!({setting: false}).to_string()).unwrap();
        assert!(enforce(&data, setting, &json!({})).is_ok());
        fs::write(&config, json!({setting: true,"enabled_skills":[],"skills_catalog_snapshot":[guide]}).to_string()).unwrap();
        assert!(enforce(&data, setting, &json!({})).is_ok());
    }
    fs::remove_dir_all(data).unwrap();
}
