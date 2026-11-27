use std::{
    collections::{BTreeSet, HashMap},
    io,
    path::Path,
};

use cap_fs_ext::DirExt;
use cap_std::fs::Dir;
use serde::Deserialize;
use serde_json::{Value, json};

use super::{
    Knowledge, SEARCH_SKILLS,
    search::{Entry, Frontmatter, Hit, Kind, Matches, Query, ellipsize},
    storage::{Document, Identity, display_io, optional_dir, read_bytes},
};

const CATALOG_ERROR: &str = "无法通过执行后端读取技能目录，请改用 read_file 按具体路径查看";

/// Personalization supplied by the trusted caller. An absent enabled list enables all skills.
#[derive(Debug, Clone)]
pub struct SkillSettings {
    pub scan_project_agents: bool,
    pub enabled_ids: Option<BTreeSet<String>>,
    pub catalog_snapshot: BTreeSet<String>,
}

impl Default for SkillSettings {
    fn default() -> Self {
        Self {
            scan_project_agents: true,
            enabled_ids: None,
            catalog_snapshot: BTreeSet::new(),
        }
    }
}

impl SkillSettings {
    fn load(data: &Dir) -> io::Result<Self> {
        let bytes = match read_bytes(data, "personalization.json", 8 * 1024 * 1024) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Self::default()),
            Err(error) => return Err(error),
        };
        Ok(Self::from_preferences(serde_json::from_slice(&bytes).unwrap_or_default()))
    }

    fn from_preferences(settings: SkillPreferences) -> Self {
        Self {
            scan_project_agents: settings.agents_skills_scan_enabled.unwrap_or(true),
            enabled_ids: preference_ids(&settings.enabled_skills),
            catalog_snapshot: preference_ids(&settings.skills_catalog_snapshot).unwrap_or_default(),
        }
    }

    pub(crate) fn enabled_in_preferences(preferences: &Value, id: &str) -> bool {
        let prefs = SkillPreferences {
            agents_skills_scan_enabled: None,
            enabled_skills: preferences["enabled_skills"].clone(),
            skills_catalog_snapshot: preferences["skills_catalog_snapshot"].clone(),
        };
        Self::from_preferences(prefs).enabled(id)
    }

    fn enabled(&self, id: &str) -> bool {
        self.enabled_ids.as_ref().is_none_or(|ids| ids.contains(id))
            || (!self.catalog_snapshot.is_empty() && !self.catalog_snapshot.contains(id))
    }
}

#[derive(Default, Deserialize)]
#[serde(default)]
struct SkillPreferences {
    agents_skills_scan_enabled: Option<bool>,
    enabled_skills: Value,
    skills_catalog_snapshot: Value,
}

fn preference_ids(value: &Value) -> Option<BTreeSet<String>> {
    Some(
        value
            .as_array()?
            .iter()
            .filter_map(Value::as_str)
            .map(str::trim)
            .filter(|name| valid_id(name))
            .map(str::to_owned)
            .collect(),
    )
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Root {
    Astrion,
    Agents,
}

impl Root {
    fn parent(self) -> &'static str {
        match self {
            Self::Astrion => ".astrion",
            Self::Agents => ".agents",
        }
    }

    fn path(self, id: &str) -> String {
        format!("{}/skills/{id}/SKILL.md", self.parent())
    }

    fn open(self, knowledge: &Knowledge) -> io::Result<Option<Dir>> {
        let Some(parent) = optional_dir(&knowledge.workspace, self.parent())? else {
            return Ok(None);
        };
        reject_data(knowledge, &parent)?;
        let Some(root) = optional_dir(&parent, "skills")? else {
            return Ok(None);
        };
        reject_data(knowledge, &root)?;
        Ok(Some(root))
    }
}

struct Skill<'a> {
    id: String,
    root: Root,
    label: String,
    description: String,
    conflict: bool,
    matched: Option<Matches<'a>>,
}

impl Skill<'_> {
    fn conflict_path(&self) -> String {
        if self.conflict {
            Root::Agents.path(&self.id)
        } else {
            String::new()
        }
    }
}

fn reject_data(knowledge: &Knowledge, dir: &Dir) -> io::Result<()> {
    if Identity::of(&dir.dir_metadata()?) == knowledge.data_identity {
        return Err(io::ErrorKind::PermissionDenied.into());
    }
    Ok(())
}

pub(crate) fn valid_id(id: &str) -> bool {
    let mut bytes = id.bytes();
    bytes
        .next()
        .is_some_and(|byte| byte.is_ascii_alphanumeric())
        && bytes.all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}

fn document(knowledge: &Knowledge, root: &Dir, id: &str) -> io::Result<Document> {
    let folder = root.open_dir_nofollow(id)?;
    reject_data(knowledge, &folder)?;
    Document::open(&folder, "SKILL.md")
}

fn skill_ids(root: &Dir) -> io::Result<Vec<String>> {
    let mut ids = Vec::new();
    for entry in root.entries()? {
        let Ok(id) = entry?.file_name().into_string() else {
            continue;
        };
        if valid_id(&id) {
            ids.push(id);
        }
    }
    Ok(ids)
}

/// The composer lists the prepared Astrion skills with workspace-relative paths.
pub(super) fn workspace_menu(knowledge: &Knowledge) -> io::Result<Vec<Value>> {
    let Some(root) = Root::Astrion.open(knowledge)? else {
        return Ok(Vec::new());
    };
    let saved;
    let settings = if let Some(settings) = &knowledge.skill_settings { settings } else {
        saved = SkillSettings::load(&knowledge.data)?;
        &saved
    };
    let mut ids = skill_ids(&root)?;
    ids.retain(|id| settings.enabled(id));
    ids.sort_by_cached_key(|id| (id.to_ascii_lowercase(), id.clone()));
    Ok(ids.into_iter().filter_map(|id| {
        let file = document(knowledge, &root, &id).ok()?;
        let meta = Frontmatter::parse(&file.text, true);
        let name = if meta.name.is_empty() { &id } else { meta.name };
        Some(json!({"name": name, "description": meta.description, "path": Root::Astrion.path(&id)}))
    }).collect())
}

/// Resolve only explicit composer selections, using the same confined root as /api/skills.
/// These are user-authored references, not model tool calls or changes to the skill catalog.
pub(crate) fn composer_messages(workspace: &Path, data: &Path, references: &Value) -> Result<Vec<Value>, String> {
    const MAX_REFS: usize = 10;
    const MAX_BYTES: u64 = 1024 * 1024;
    if references.is_null() {
        return Ok(Vec::new());
    }
    let references = references.as_array().ok_or("skill_refs 必须是数组。")?;
    if references.is_empty() {
        return Ok(Vec::new());
    }

    let workspace_prefix = composer_path(&workspace.to_string_lossy());
    let mut seen = BTreeSet::new();
    let mut ids = Vec::new();
    for reference in references {
        let path = reference["path"].as_str().ok_or("技能引用必须提供 path。")?;
        let path = composer_path(path.trim());
        const SKILL_ROOT: &str = ".astrion/skills/";
        let tail = path.strip_prefix(SKILL_ROOT).or_else(|| {
            let (prefix, tail) = path.rsplit_once("/.astrion/skills/")?;
            let root = Path::new(&path[..prefix.len() + 1]);
            // Resolve the workspace alias only. The skill itself is always opened
            // relative to the trusted directory handle below.
            let same_workspace = prefix == workspace_prefix.trim_end_matches('/')
                || (root.is_absolute()
                    && !root.components().any(|part| part == std::path::Component::ParentDir)
                    && root.canonicalize().ok().as_deref() == Some(workspace));
            same_workspace.then_some(tail)
        });
        let id = tail.and_then(|tail| tail.strip_suffix("/SKILL.md"))
            .filter(|id| valid_id(id))
            .ok_or("技能引用必须指向当前工作区的 .astrion/skills/<id>/SKILL.md。")?;
        let identity = if cfg!(windows) { id.to_ascii_lowercase() } else { id.to_owned() };
        if seen.insert(identity) {
            ids.push(id.to_owned());
            if ids.len() > MAX_REFS {
                return Err(format!("单次最多可附加 {MAX_REFS} 个技能。"));
            }
        }
    }

    let knowledge = Knowledge::open(workspace, data).map_err(display_io)?;
    let root = Root::Astrion.open(&knowledge).map_err(display_io)?
        .ok_or("工作区的技能目录不存在。")?;
    let mut available = MAX_BYTES;
    ids.into_iter().map(|id| {
        let path = Root::Astrion.path(&id);
        let file = (|| {
            let folder = root.open_dir_nofollow(&id)?;
            reject_data(&knowledge, &folder)?;
            Document::open_bounded(&folder, "SKILL.md", available)
        })().map_err(|error| format!("{path}：{}", display_io(error)))?;
        available -= file.size as u64;
        let meta = Frontmatter::parse(&file.text, true);
        let name = if meta.name.is_empty() { &id } else { meta.name };
        Ok(json!({
            "role": "user", "content": format!("[系统通知|skill]\n{}", file.text),
            "metadata": {"source":"skill","message_source":"skill","visibility":"hidden",
                "starts_work":false,"hidden":true,"skill_name":name,"skill_path":path}
        }))
    }).collect()
}

fn composer_path(path: &str) -> String {
    let path = path.replace('\\', "/");
    #[cfg(windows)]
    {
        if let Some(unc) = path.strip_prefix("//?/UNC/") {
            return format!("//{unc}");
        }
        path.strip_prefix("//?/").unwrap_or(&path).to_owned()
    }
    #[cfg(not(windows))]
    path
}

fn catalog<'a>(knowledge: &Knowledge, query: Option<&Query<'a>>) -> Result<Vec<Skill<'a>>, String> {
    let saved;
    let settings = match &knowledge.skill_settings {
        Some(settings) => settings,
        None => {
            saved = SkillSettings::load(&knowledge.data).map_err(|_| CATALOG_ERROR)?;
            &saved
        }
    };
    let mut entries: Vec<Skill<'a>> = Vec::new();
    let mut indexes: HashMap<String, usize> = HashMap::new();
    for source in [Root::Astrion, Root::Agents] {
        if source == Root::Agents && !settings.scan_project_agents {
            break;
        }
        let Some(root) = source.open(knowledge).map_err(|_| CATALOG_ERROR)? else {
            continue;
        };
        let mut ids = skill_ids(&root).map_err(|_| CATALOG_ERROR)?;
        ids.sort_unstable();
        for id in ids.into_iter().filter(|id| settings.enabled(id)) {
            let Ok(file) = document(knowledge, &root, &id) else {
                continue;
            };
            if let Some(index) = indexes.get(&id) {
                entries[*index].conflict = true;
                continue;
            }
            let meta = Frontmatter::parse(&file.text, true);
            let matched = query.and_then(|query| query.matches(&id, &meta, &file.text));
            let label = if meta.name.is_empty() {
                id.clone()
            } else {
                meta.name.to_owned()
            };
            indexes.insert(id.clone(), entries.len());
            entries.push(Skill {
                id,
                root: source,
                label,
                description: ellipsize(meta.description, 300),
                conflict: false,
                matched,
            });
        }
    }
    Ok(entries)
}

pub(super) fn list_lines(knowledge: &Knowledge) -> Result<Vec<String>, String> {
    let entries = catalog(knowledge, None)?;
    let mut lines = Vec::new();
    for root in [Root::Astrion, Root::Agents] {
        let group: Vec<&str> = entries
            .iter()
            .filter(|skill| skill.root == root)
            .map(|skill| skill.id.as_str())
            .collect();
        if !group.is_empty() {
            lines.push(format!("{}/skills/：{}", root.parent(), group.join(", ")));
        }
    }
    let conflicts: Vec<String> = entries
        .iter()
        .filter(|skill| skill.conflict)
        .map(|skill| format!("{}（.astrion/skills/ 与 .agents/skills/ 各一份）", skill.id))
        .collect();
    if !conflicts.is_empty() {
        lines.push(format!(
            "同名重复（read_skill 会报错，请用 read_file 按具体路径读取）：{}",
            conflicts.join("，")
        ));
    }
    Ok(lines)
}

pub(super) fn search(knowledge: &Knowledge, arguments: &Value) -> Result<Value, String> {
    let query = Query::parse(arguments, SEARCH_SKILLS)?;
    let entries = catalog(knowledge, Some(&query))?;
    let empty = entries.is_empty();
    let hits = entries
        .into_iter()
        .filter_map(|mut skill| {
            let matches = skill.matched.take()?;
            let path = skill.root.path(&skill.id);
            let conflict_path = skill.conflict_path();
            Some(Hit {
                entry: Entry::Skill {
                    id: skill.id,
                    path,
                    conflict_path,
                },
                description: skill.description,
                matches,
            })
        })
        .collect();
    Ok(query.result(Kind::Skill, hits, empty))
}

pub(super) fn read(knowledge: &Knowledge, arguments: &Value) -> Result<Value, String> {
    let requested = arguments["skill_name"].as_str().unwrap_or_default();
    let name = normalize(requested);
    if name.is_empty() {
        return Err("skill_name 不能为空".into());
    }
    let entries = catalog(knowledge, None)?;
    let skill = resolve(&entries, &name)?;
    if skill.conflict {
        let primary = skill.root.path(&skill.id);
        let conflict = skill.conflict_path();
        return Err(format!(
            "技能 '{}' 存在同名重复：{primary} 与 {conflict} 均存在。请用 read_file 按具体路径读取。",
            skill.id
        ));
    }
    let root = skill
        .root
        .open(knowledge)
        .map_err(|_| CATALOG_ERROR)?
        .ok_or("文件不存在")?;
    let file = document(knowledge, &root, &skill.id).map_err(display_io)?;
    let mut result = file.read_result(&skill.root.path(&skill.id));
    result["skill_id"] = json!(skill.id);
    result["skill_name"] = json!(requested);
    Ok(result)
}

fn normalize(name: &str) -> String {
    let clean = name.trim().replace('\\', "/");
    let mut name = clean.trim_matches('/');
    for prefix in [".astrion/skills/", ".agents/skills/"] {
        if name
            .get(..prefix.len())
            .is_some_and(|part| part.eq_ignore_ascii_case(prefix))
        {
            name = &name[prefix.len()..];
            break;
        }
    }
    if name.to_ascii_lowercase().ends_with("/skill.md") {
        name = &name[..name.len() - "/skill.md".len()];
    }
    name.trim().to_lowercase()
}

fn resolve<'a, 'b>(entries: &'a [Skill<'b>], name: &str) -> Result<&'a Skill<'b>, String> {
    let mut matches: Vec<&Skill<'_>> = entries
        .iter()
        .filter(|entry| entry.id.eq_ignore_ascii_case(name))
        .collect();
    if matches.is_empty() {
        matches.extend(
            entries
                .iter()
                .filter(|entry| entry.label.to_lowercase() == name),
        );
    }
    match matches.as_slice() {
        [found] => Ok(found),
        [] => Err(format!("未找到技能: {name}")),
        _ => {
            let ids = matches
                .iter()
                .map(|entry| entry.id.as_str())
                .collect::<BTreeSet<_>>();
            let ids = ids.into_iter().collect::<Vec<_>>().join(", ");
            Err(format!(
                "skill_name 匹配到多个技能: {ids}，请用唯一的技能 ID 或 read_file 按具体路径读取。"
            ))
        }
    }
}
