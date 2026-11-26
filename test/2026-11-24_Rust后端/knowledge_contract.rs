use std::{
    fs,
    path::{Path, PathBuf},
};

use serde_json::{Value, json};
use uuid::Uuid;

use super::{
    Knowledge, KnowledgeAccess, KnowledgeReads, READ_SKILL, RECALL_PROJECT_MEMORY,
    SEARCH_PROJECT_MEMORY, SEARCH_SKILLS, SkillSettings, UPDATE_PROJECT_MEMORY, definitions,
    full_read_path, is_read_only_tool, tool_access,
};

struct Fixture {
    root: PathBuf,
    workspace: PathBuf,
    data: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("astrion-knowledge-{}", Uuid::new_v4()));
        let workspace = root.join("工作区 + knowledge");
        let data = root.join("existing-data");
        fs::create_dir_all(&workspace).unwrap();
        write_file(&data.join("unrelated.json"), "{\"old\":true}");
        Self {
            root,
            workspace,
            data,
        }
    }

    fn knowledge(&self) -> Knowledge {
        Knowledge::open(&self.workspace, &self.data).unwrap()
    }

    fn file(&self, relative: &str, content: &str) {
        write_file(&self.workspace.join(relative), content);
    }

    fn topic(&self, name: &str, content: &str) -> PathBuf {
        let path = self.knowledge().topics_path.join(format!("{name}.md"));
        write_file(&path, content);
        path
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.root).unwrap();
    }
}

fn write_file(path: &Path, content: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, content).unwrap();
}

fn search(tool: &Knowledge, name: &str, keywords: &[&str]) -> Value {
    tool.execute_read_only(name, &json!({"keywords": keywords}))
        .unwrap()
}

fn recall(tool: &Knowledge, name: &str, reads: &mut KnowledgeReads) -> Value {
    tool.execute(
        RECALL_PROJECT_MEMORY,
        &json!({"name": name}),
        KnowledgeAccess::ReadOnly,
        reads,
    )
    .unwrap()
}

fn update_arguments(name: &str, content: &str) -> Value {
    json!({"name": name, "description": "中文中文 Description", "content": content})
}

fn update(tool: &Knowledge, name: &str, content: &str, reads: &mut KnowledgeReads) -> Value {
    tool.execute(
        UPDATE_PROJECT_MEMORY,
        &update_arguments(name, content),
        KnowledgeAccess::ReadWrite,
        reads,
    )
    .unwrap()
}

#[test]
fn registration_and_dispatch_expose_only_read_tools_without_write_access() {
    let read_only = KnowledgeAccess::ReadOnly;
    let read_write = KnowledgeAccess::ReadWrite;
    let names = |access| {
        definitions(access)
            .into_iter()
            .map(|def| {
                assert_eq!(def["type"], "function");
                assert_eq!(def["function"]["parameters"]["type"], "object");
                def["function"]["name"].as_str().unwrap().to_owned()
            })
            .collect::<Vec<_>>()
    };
    let reads = [
        SEARCH_SKILLS,
        READ_SKILL,
        SEARCH_PROJECT_MEMORY,
        RECALL_PROJECT_MEMORY,
    ];
    assert_eq!(names(read_only), reads);
    assert_eq!(
        names(read_write),
        [reads.as_slice(), &[UPDATE_PROJECT_MEMORY]].concat()
    );
    assert!(
        reads
            .into_iter()
            .all(|name| tool_access(name) == Some(read_only) && is_read_only_tool(name))
    );
    assert_eq!(tool_access(UPDATE_PROJECT_MEMORY), Some(read_write));
    assert!(!is_read_only_tool(UPDATE_PROJECT_MEMORY));
    assert!(!is_read_only_tool("unrelated_tool"));
    assert!(!read_write.allows("unrelated_tool"));

    let fixture = Fixture::new();
    let tool = fixture.knowledge();
    assert_eq!(
        tool.execute_read_only(UPDATE_PROJECT_MEMORY, &update_arguments("test", "text"))
            .unwrap()["error_code"],
        "permission_denied"
    );
    assert!(
        tool.execute_read_only("unrelated_tool", &json!({}))
            .is_none()
    );
    assert!(!fixture.data.join("memory").exists());
    assert_eq!(search(&tool, SEARCH_PROJECT_MEMORY, &["中文"])["count"], 0);
    assert!(!fixture.data.join("memory").exists());
}

#[test]
fn skills_cover_both_directories_and_share_settings_between_lookup_and_search() {
    let fixture = Fixture::new();
    fixture.file(".astrion/skills/xlsx/SKILL.md", &format!(
        "---\r\nname: '工作表 Spreadsheet'\r\ndescription: {}\r\n---\r\n\r\n# Table\r\n用 OPENPYXL 创建表。\r\n", "描述".repeat(180)
    ));
    fixture.file(
        ".agents/skills/slides/SKILL.md",
        "---\nname: Slides\ndescription: Slide decks\n---\n\n中文 OpenPyXL\n",
    );
    fixture.file(".agents/skills/without-skills/readme.md", "openpyxl");
    let tool = fixture.knowledge();
    let result = search(
        &tool,
        SEARCH_SKILLS,
        &["spreadSHEET", "OPENPYXL", "中文", "中文"],
    );
    assert_eq!(result["success"], true);
    assert_eq!(
        result["keywords"],
        json!(["spreadSHEET", "OPENPYXL", "中文"])
    );
    assert_eq!(result["count"], 2);
    assert_eq!(result["results"][0]["skill_id"], "xlsx");
    assert_eq!(
        result["results"][0]["path"],
        ".astrion/skills/xlsx/SKILL.md"
    );
    assert_eq!(
        result["results"][0]["description"]
            .as_str()
            .unwrap()
            .chars()
            .count(),
        300
    );
    assert_eq!(
        result["results"][0]["snippets"],
        json!([{"line": 7, "text": "用 OPENPYXL 创建表。"}])
    );
    assert_eq!(
        result["results"][1]["path"],
        ".agents/skills/slides/SKILL.md"
    );
    assert_eq!(result["results"][1]["score"], 2);
    assert!(full_read_path(SEARCH_SKILLS, &result).is_none());
    assert_eq!(
        tool.skill_list_lines().unwrap(),
        [".astrion/skills/：xlsx", ".agents/skills/：slides"]
    );

    for name in [
        "XLSX",
        "工作表 spreadsheet",
        r".astrion\skills\xlsx\SKILL.md",
    ] {
        let result = tool
            .execute_read_only(READ_SKILL, &json!({"skill_name": name}))
            .unwrap();
        assert_eq!(result["skill_id"], "xlsx");
        assert_eq!(result["skill_name"], name);
        assert_eq!(result["total_lines"], 7);
        assert_eq!(
            full_read_path(READ_SKILL, &result),
            Some(".astrion/skills/xlsx/SKILL.md")
        );
        assert!(
            result["content"]
                .as_str()
                .unwrap()
                .ends_with("用 OPENPYXL 创建表。\n")
        );
    }

    let disabled = fixture.knowledge().with_skill_settings(SkillSettings {
        enabled_ids: Some(["slides".into()].into()),
        catalog_snapshot: ["xlsx".into(), "slides".into()].into(),
        ..SkillSettings::default()
    });
    assert_eq!(search(&disabled, SEARCH_SKILLS, &["xlsx"])["count"], 0);
    assert_eq!(
        disabled
            .execute_read_only(READ_SKILL, &json!({"skill_name": "xlsx"}))
            .unwrap()["success"],
        false
    );
    fixture.file(
        ".agents/skills/new/SKILL.md",
        "OpenPyxl is detected for new skills.",
    );
    assert_eq!(search(&disabled, SEARCH_SKILLS, &["new"])["count"], 1);

    let astrion_only = fixture.knowledge().with_skill_settings(SkillSettings {
        scan_project_agents: false,
        ..SkillSettings::default()
    });
    assert_eq!(
        search(&astrion_only, SEARCH_SKILLS, &["OPENPYXL"])["count"],
        1
    );
    let none = fixture.knowledge().with_skill_settings(SkillSettings {
        enabled_ids: Some(Default::default()),
        ..SkillSettings::default()
    });
    assert_eq!(search(&none, SEARCH_SKILLS, &["OPENPYXL"])["count"], 0);
}

#[test]
fn skill_settings_are_loaded_from_existing_personalization() {
    let fixture = Fixture::new();
    fixture.file(".astrion/skills/disabled/SKILL.md", "searchword");
    fixture.file(".astrion/skills/enabled/SKILL.md", "searchword");
    fixture.file(".agents/skills/ignored/SKILL.md", "searchword");
    let saved = json!({
        "agents_skills_scan_enabled": false,
        "enabled_skills": ["enabled", null],
        "skills_catalog_snapshot": ["disabled", "enabled"],
        "unrelated-setting": "searchword"
    })
    .to_string();
    let settings = fixture.data.join("personalization.json");
    write_file(&settings, &saved);
    let tool = fixture.knowledge();
    let result = search(&tool, SEARCH_SKILLS, &["searchword"]);
    assert_eq!(result["count"], 1);
    assert_eq!(result["results"][0]["skill_id"], "enabled");
    assert_eq!(
        tool.execute_read_only(READ_SKILL, &json!({"skill_name": "disabled"}))
            .unwrap()["success"],
        false
    );
    assert_eq!(fs::read_to_string(settings).unwrap(), saved);
}

#[test]
fn duplicate_skill_ids_report_concrete_paths_and_labels_can_be_ambiguous() {
    let fixture = Fixture::new();
    fixture.file(
        ".astrion/skills/first/SKILL.md",
        "---\nname: Shared label\n---\nReference content",
    );
    fixture.file(
        ".agents/skills/first/SKILL.md",
        "---\nname: Duplicate ID\n---\nother content",
    );
    fixture.file(
        ".agents/skills/second/SKILL.md",
        "---\nname: Shared label\n---\nReference content",
    );
    let tool = fixture.knowledge();
    let result = search(&tool, SEARCH_SKILLS, &["first"]);
    assert_eq!(result["count"], 1);
    assert_eq!(
        result["results"][0]["conflict_path"],
        ".agents/skills/first/SKILL.md"
    );
    assert!(result["content"].as_str().unwrap().contains("同名重复"));
    assert!(
        tool.skill_list_lines()
            .unwrap()
            .last()
            .unwrap()
            .contains("同名重复")
    );
    let read = |name| {
        tool.execute_read_only(READ_SKILL, &json!({"skill_name": name}))
            .unwrap()
    };
    let duplicate = read(".agents/skills/first/SKILL.md");
    let message = duplicate["error"].as_str().unwrap();
    assert!(
        message.contains("read_file")
            && message.contains(".astrion/skills/first/SKILL.md")
            && message.contains(".agents/skills/first/SKILL.md")
    );
    let ambiguous = read("shared label");
    assert!(
        ambiguous["error"]
            .as_str()
            .unwrap()
            .contains("first, second")
    );
    assert_eq!(read("second")["success"], true);
}

#[cfg(unix)]
#[test]
fn legacy_python_namespace_fixture_matches() {
    // Generated with modules/workspace_memory.py's slug and SHA256 algorithm.
    assert_eq!(
        super::topics::workspace_namespace(Path::new("/tmp/工程 + work")).unwrap(),
        "工程-work-01b7477f15f55e50"
    );
}

#[test]
fn topics_use_indexed_layout_preserve_markdown_and_rank_bilingual_matches() {
    let fixture = Fixture::new();
    let topic = fixture.topic("stable_api", "---\nname: 项目 API\ndescription: API 迁移\n---\n\nAPI 迁移第一步\n中文片段\nAPI\nAPI\nAPI\nAPI\nAPI\n");
    let permanent = topic.parent().unwrap().parent().unwrap().join("MEMORY.md");
    write_file(&permanent, "API 永久记忆");
    fixture.topic("00_READ_ME_项目记忆已迁移", "API 通知");
    fixture.topic("单独主题", "API text");
    fixture.file(".astrion/memory/old.md", "API legacy");
    let tool = fixture.knowledge();
    let namespace = topic
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .file_name()
        .unwrap()
        .to_str()
        .unwrap();
    assert!(namespace.starts_with("工作区-knowledge-"));
    assert_eq!(namespace.rsplit_once('-').unwrap().1.len(), 16);
    let result = search(&tool, SEARCH_PROJECT_MEMORY, &["API", "迁移", "中文"]);
    assert_eq!(result["count"], 2);
    assert_eq!(result["results"][0]["memory_name"], "stable_api");
    assert_eq!(result["results"][0]["name"], "项目 API");
    assert_eq!(result["results"][0]["score"], 27);
    assert_eq!(
        result["results"][0]["snippets"].as_array().unwrap().len(),
        3
    );
    assert_eq!(
        result["results"][0]["snippets"][0],
        json!({"line": 6, "text": "API 迁移第一步"})
    );
    assert_eq!(
        search(&tool, SEARCH_PROJECT_MEMORY, &["永久", "legacy", "通知"])["count"],
        0
    );
    assert!(full_read_path(SEARCH_PROJECT_MEMORY, &result).is_none());
    let mut reads = KnowledgeReads::default();
    let recalled = recall(&tool, "stable_api", &mut reads);
    assert_eq!(recalled["content"], fs::read_to_string(&topic).unwrap());
    assert_eq!(
        full_read_path(RECALL_PROJECT_MEMORY, &recalled),
        Some(topic.as_os_str().to_str().unwrap())
    );
}

#[test]
fn overwrite_requires_the_current_version_from_the_same_conversation() {
    let fixture = Fixture::new();
    let tool = fixture.knowledge();
    let mut first = KnowledgeReads::default();
    let mut second = KnowledgeReads::default();
    let created = update(&tool, "决策", "初始正文", &mut first);
    assert_eq!(created["success"], true, "{created}");
    let path = PathBuf::from(created["path"].as_str().unwrap());
    assert_eq!(
        fs::read_to_string(&path).unwrap(),
        "---\nname: 决策\ndescription: 中文中文 Description\n---\n\n初始正文\n"
    );
    assert_eq!(
        tool.execute(
            SEARCH_PROJECT_MEMORY,
            &json!({"keywords": ["初始"]}),
            KnowledgeAccess::ReadWrite,
            &mut second
        )
        .unwrap()["count"],
        1
    );
    assert_eq!(
        update(&tool, "决策", "search is a snippet", &mut second)["error_code"],
        "read_before_edit"
    );
    assert_eq!(recall(&tool, "决策", &mut second)["success"], true);
    let mut second: KnowledgeReads =
        serde_json::from_value(serde_json::to_value(second).unwrap()).unwrap();
    assert_eq!(
        update(&tool, "决策", "一号修改", &mut first)["success"],
        true
    );
    assert_eq!(
        update(&tool, "决策", "二号旧修改", &mut second)["error_code"],
        "project_memory_conflict"
    );
    assert!(fs::read_to_string(&path).unwrap().contains("一号修改"));
    assert_eq!(recall(&tool, "决策", &mut second)["success"], true);
    assert_eq!(
        update(&tool, "决策", "二号新修改", &mut second)["success"],
        true
    );
    assert!(fs::read_to_string(&path).unwrap().contains("二号新修改"));
    assert_eq!(fs::read_dir(path.parent().unwrap()).unwrap().count(), 1);

    fixture.topic("long", &"字".repeat(50_001));
    let partial = recall(&tool, "long", &mut second);
    assert_eq!(partial["truncated"], true);
    assert!(full_read_path(RECALL_PROJECT_MEMORY, &partial).is_none());
    assert_eq!(
        update(&tool, "long", "unfinished read", &mut second)["error_code"],
        "read_before_edit"
    );
}

#[test]
fn existing_shared_data_directory_exposes_only_the_bound_workspace_namespace() {
    let fixture = Fixture::new();
    let other = fixture.root.join("other-workspace");
    fs::create_dir(&other).unwrap();
    assert!(Knowledge::open(&fixture.workspace, &fixture.workspace).is_err());
    let tool = fixture.knowledge();
    let mut reads = KnowledgeReads::default();
    for name in [
        "../external",
        "..",
        "path/name",
        r"path\name",
        "/a",
        "a:b",
        "null\0byte",
        "00_READ_ME_项目记忆已迁移",
    ] {
        assert_eq!(recall(&tool, name, &mut reads)["success"], false, "{name}");
        assert_eq!(
            update(&tool, name, "blocked", &mut reads)["success"],
            false,
            "{name}"
        );
    }
    assert!(!fixture.data.join("memory").exists());
    fixture.topic("first_workspace", "identity of the first workspace");
    let other_tool = Knowledge::open(&other, &fixture.data).unwrap();
    assert_eq!(
        search(&other_tool, SEARCH_PROJECT_MEMORY, &["identity"])["count"],
        0
    );
    assert_eq!(
        recall(&other_tool, "first_workspace", &mut reads)["success"],
        false
    );
    assert_eq!(
        fs::read_to_string(fixture.data.join("unrelated.json")).unwrap(),
        "{\"old\":true}"
    );

    let private = fixture.workspace.join(".astrion/skills/runtime");
    write_file(&private.join("SKILL.md"), "secret data workspace runtime");
    let tool = Knowledge::open(&fixture.workspace, &private).unwrap();
    assert_eq!(search(&tool, SEARCH_SKILLS, &["secret"])["count"], 0);
}

#[cfg(unix)]
#[test]
fn linked_candidates_and_redirected_capability_directories_cannot_reach_external_data() {
    use std::os::unix::fs::symlink;

    let fixture = Fixture::new();
    let outside = fixture.root.join("outside");
    let secret = outside.join("SKILL.md");
    write_file(&secret, "external secret");
    let skills = fixture.workspace.join(".astrion/skills");
    fs::create_dir_all(skills.join("linked-file")).unwrap();
    fs::create_dir_all(skills.join("hardlinked-file")).unwrap();
    symlink(&outside, skills.join("linked-folder")).unwrap();
    symlink(&secret, skills.join("linked-file/SKILL.md")).unwrap();
    fs::hard_link(&secret, skills.join("hardlinked-file/SKILL.md")).unwrap();
    let tool = fixture.knowledge();
    assert_eq!(
        search(&tool, SEARCH_SKILLS, &["external", "linked"])["count"],
        0
    );
    symlink(&outside, fixture.workspace.join(".agents")).unwrap();
    assert_eq!(
        search(&tool, SEARCH_SKILLS, &["external"])["success"],
        false
    );

    let mut reads = KnowledgeReads::default();
    let file = fixture.topic("seed", "safe");
    let dir = file.parent().unwrap();
    let alias = dir.join("outside.md");
    symlink(&secret, &alias).unwrap();
    fs::hard_link(&secret, dir.join("hard.md")).unwrap();
    assert_eq!(
        search(&tool, SEARCH_PROJECT_MEMORY, &["external"])["count"],
        0
    );
    assert_eq!(recall(&tool, "outside", &mut reads)["success"], false);
    assert_eq!(
        update(&tool, "outside", "write attempt", &mut reads)["success"],
        false
    );
    assert_eq!(
        update(&tool, "hard", "write attempt", &mut reads)["success"],
        false
    );
    fs::remove_dir_all(dir).unwrap();
    symlink(&outside, dir).unwrap();
    assert_eq!(
        search(&tool, SEARCH_PROJECT_MEMORY, &["external"])["success"],
        false
    );
    assert_eq!(
        update(&tool, "redirected", "write attempt", &mut reads)["success"],
        false
    );
    assert!(!outside.join("redirected.md").exists());
    assert_eq!(fs::read_to_string(secret).unwrap(), "external secret");
}
