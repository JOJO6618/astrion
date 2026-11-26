use std::collections::{BTreeMap, HashMap, HashSet};

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use uuid::Uuid;

use super::{NavigationError, text};

pub(super) const MAX_TABS: usize = 100;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ConversationKind {
    Agent,
    Chat,
}

#[derive(Clone, Copy, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum Kind {
    Conv,
    New,
    Page,
}

#[derive(Clone, Copy, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum Placement {
    Main,
    Detached,
}

#[derive(Clone, Copy, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum PageType {
    Settings,
    Workflows,
    Git,
    Schedules,
}

#[derive(Clone, Deserialize, PartialEq, Serialize)]
pub(super) struct Tab {
    pub key: String,
    pub kind: Kind,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub conversation_kind: Option<ConversationKind>,
    pub conversation_id: String,
    pub workspace_id: String,
    pub workspace_label: String,
    pub title: String,
    pub error: String,
    pub placement: Placement,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub page_type: Option<PageType>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub route: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub opener_key: Option<String>,
    #[serde(flatten)]
    extra: Map<String, Value>,
}

#[derive(Deserialize, Serialize)]
pub(super) struct Receipt {
    pub id: String,
    pub fingerprint: String,
    pub result: Value,
}

#[derive(Deserialize, Serialize)]
pub(super) struct State {
    pub version: u64,
    pub revision: u64,
    pub tabs: Vec<Tab>,
    pub active_key: String,
    pub workspace_id: String,
    pub renderers: BTreeMap<String, String>,
    pub receipts: Vec<Receipt>,
    pub closed_keys: Vec<String>,
    #[serde(flatten)]
    pub extra: Map<String, Value>,
}

pub(super) fn valid_id(id: &str, limit: usize) -> bool {
    !id.is_empty()
        && id.len() <= limit
        && id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}

pub(super) fn conversation_id(raw: &str) -> Result<String, NavigationError> {
    let id = raw.trim();
    if id.is_empty() {
        return Ok(String::new());
    }
    if !valid_id(id, 180) {
        return Err(NavigationError::invalid("Invalid conversation identity"));
    }
    Ok(if id.starts_with("conv_") {
        id.into()
    } else {
        format!("conv_{id}")
    })
}

pub(super) fn clipped(value: &str, limit: usize) -> String {
    value.chars().take(limit).collect()
}

fn new_key() -> String {
    format!("tab_{}", Uuid::new_v4().simple())
}

impl Tab {
    pub fn conversation(
        key: String,
        kind: ConversationKind,
        workspace_id: String,
        workspace_label: String,
        conversation_id: String,
        title: String,
    ) -> Self {
        Self {
            key: if key.is_empty() { new_key() } else { key },
            kind: if conversation_id.is_empty() {
                Kind::New
            } else {
                Kind::Conv
            },
            conversation_kind: Some(kind),
            conversation_id,
            workspace_id,
            workspace_label,
            title: clipped(&title, 200),
            error: String::new(),
            placement: Placement::Main,
            page_type: None,
            route: None,
            opener_key: None,
            extra: Map::new(),
        }
    }

    pub fn page(
        page_type: PageType,
        route: String,
        opener: String,
        id: String,
        label: String,
    ) -> Self {
        Self {
            key: new_key(),
            kind: Kind::Page,
            conversation_kind: None,
            conversation_id: String::new(),
            workspace_id: id,
            workspace_label: label,
            title: String::new(),
            error: String::new(),
            placement: Placement::Main,
            page_type: Some(page_type),
            route: Some(route),
            opener_key: Some(opener),
            extra: Map::new(),
        }
    }

    pub fn is_main(&self) -> bool {
        self.placement == Placement::Main
    }
}

impl State {
    fn empty() -> Self {
        Self {
            version: 3,
            revision: 0,
            tabs: Vec::new(),
            active_key: String::new(),
            workspace_id: String::new(),
            renderers: BTreeMap::new(),
            receipts: Vec::new(),
            closed_keys: Vec::new(),
            extra: Map::new(),
        }
    }

    pub fn load(raw: &Value) -> Result<(Self, bool), NavigationError> {
        let bad = || NavigationError::storage("Invalid desktop navigation state");
        if raw.is_null() {
            return Ok((Self::empty(), true));
        }
        let tabs = raw["tabs"].as_array().ok_or_else(bad)?;
        let (state, changed) = match raw["version"].as_u64() {
            Some(3) => (
                serde_json::from_value(raw.clone()).map_err(NavigationError::storage)?,
                false,
            ),
            Some(2) => (Self::migrate_v2(raw, tabs)?, true),
            None if raw["version"].is_null() => (Self::migrate_v1(raw, tabs), true),
            _ => {
                return Err(NavigationError::storage(
                    "Unsupported desktop navigation version",
                ));
            }
        };
        state.validate()?;
        Ok((state, changed))
    }

    fn validate(&self) -> Result<(), NavigationError> {
        let mut keys = HashSet::new();
        for tab in &self.tabs {
            if !valid_id(&tab.key, 100) || !keys.insert(&tab.key) {
                return Err(NavigationError::storage("Invalid desktop tab identity"));
            }
            let valid = match tab.kind {
                Kind::Page => {
                    tab.is_main()
                        && tab.page_type.is_some()
                        && tab.route.is_some()
                        && tab.opener_key.is_some()
                        && (tab.page_type != Some(PageType::Git) || !tab.workspace_id.is_empty())
                }
                _ => match tab.conversation_kind {
                    Some(ConversationKind::Agent) => true,
                    Some(ConversationKind::Chat) => tab.workspace_id.is_empty(),
                    None => false,
                },
            };
            if !valid {
                return Err(NavigationError::storage("Invalid desktop tab"));
            }
        }
        if !self.active_key.is_empty()
            && !self
                .tabs
                .iter()
                .any(|tab| tab.is_main() && tab.key == self.active_key)
        {
            return Err(NavigationError::storage("Invalid desktop active tab"));
        }
        Ok(())
    }

    fn migrate_v2(raw: &Value, tabs: &[Value]) -> Result<Self, NavigationError> {
        let bad = || NavigationError::storage("Invalid v2 desktop navigation state");
        let active = raw["active_key"].as_str().ok_or_else(bad)?;
        let resume = raw["resume_key"].as_str().ok_or_else(bad)?;
        let nav = raw.get("navigation").ok_or_else(bad)?;
        if !raw["renderer_id"].is_string()
            || (!nav.is_null()
                && (!nav.is_object()
                    || ["id", "key", "page", "error"]
                        .iter()
                        .any(|key| !nav[*key].is_string())
                    || !matches!(
                        nav["status"].as_str(),
                        Some("pending" | "committed" | "error")
                    )))
        {
            return Err(bad());
        }
        let mut state = Self::empty();
        state.revision = raw["revision"].as_u64().ok_or_else(bad)?;
        state.workspace_id = raw["workspace_id"].as_str().ok_or_else(bad)?.into();
        state.receipts =
            serde_json::from_value(raw["receipts"].clone()).map_err(NavigationError::storage)?;
        state.closed_keys =
            serde_json::from_value(raw["closed_keys"].clone()).map_err(NavigationError::storage)?;
        for item in tabs {
            let mut item = item.as_object().ok_or_else(bad)?.clone();
            if !matches!(
                item.get("kind").and_then(Value::as_str),
                Some("conv" | "new")
            ) {
                return Err(bad());
            }
            item.entry("conversation_kind")
                .or_insert(Value::from("agent"));
            item.insert("placement".into(), Value::from("main"));
            state.tabs.push(
                serde_json::from_value(Value::Object(item)).map_err(NavigationError::storage)?,
            );
        }
        state.active_key = [active, resume]
            .into_iter()
            .find(|key| state.position(key).is_some())
            .or_else(|| state.tabs.first().map(|tab| tab.key.as_str()))
            .unwrap_or_default()
            .into();
        Ok(state)
    }

    fn migrate_v1(raw: &Value, tabs: &[Value]) -> Self {
        let mut state = Self::empty();
        let mut mapping = HashMap::new();
        let mut conversations = HashMap::new();
        for item in tabs.iter().take(MAX_TABS) {
            let workspace = text(&item["workspace_id"]);
            let id = match item["kind"].as_str() {
                Some("new") => String::new(),
                Some("conv") => match conversation_id(&text(&item["conversation_id"])) {
                    Ok(id) if !id.is_empty() => id,
                    _ => continue,
                },
                _ => continue,
            };
            let old = text(&item["key"]);
            let identity = (workspace.clone(), id.clone());
            if !id.is_empty()
                && let Some(key) = conversations.get(&identity)
            {
                mapping.insert(old, String::clone(key));
                continue;
            }
            let tab = Tab::conversation(
                String::new(),
                ConversationKind::Agent,
                workspace,
                text(&item["workspace_label"]),
                id,
                text(&item["title"]),
            );
            conversations.insert(identity, tab.key.clone());
            mapping.insert(old, tab.key.clone());
            state.tabs.push(tab);
        }
        state.active_key = mapping
            .remove(&text(&raw["active_key"]))
            .unwrap_or_default();
        state
    }

    pub fn position(&self, key: &str) -> Option<usize> {
        self.tabs.iter().position(|tab| tab.key == key)
    }

    pub fn activate(&mut self, index: usize) {
        let tab = &self.tabs[index];
        self.active_key.clone_from(&tab.key);
        if tab.conversation_kind == Some(ConversationKind::Agent) && tab.kind != Kind::Page {
            self.workspace_id.clone_from(&tab.workspace_id);
        }
    }

    pub fn successor(&self, gone: &HashSet<String>) -> Option<String> {
        let active = self.position(&self.active_key).unwrap_or(self.tabs.len());
        let available = |tab: &&Tab| tab.is_main() && !gone.contains(&tab.key);
        let opener = self
            .tabs
            .get(active)
            .filter(|tab| tab.kind == Kind::Page)
            .and_then(|tab| tab.opener_key.as_deref())
            .and_then(|key| {
                self.tabs
                    .iter()
                    .find(|tab| tab.key == key && available(tab))
            });
        opener
            .or_else(|| self.tabs.iter().skip(active + 1).find(available))
            .or_else(|| self.tabs[..active].iter().rev().find(available))
            .map(|tab| tab.key.clone())
    }

    pub fn activate_successor(&mut self, key: Option<String>) {
        self.active_key.clear();
        if let Some(index) = key.and_then(|key| self.position(&key)) {
            self.activate(index);
        }
    }

    pub fn ensure_main(&mut self, template: &Tab) {
        if let Some(first) = self.tabs.iter().position(Tab::is_main) {
            if !self
                .position(&self.active_key)
                .is_some_and(|index| self.tabs[index].is_main())
            {
                self.activate(first);
            }
            return;
        }
        let kind = template
            .conversation_kind
            .unwrap_or(ConversationKind::Agent);
        let workspace = match kind {
            ConversationKind::Chat => String::new(),
            ConversationKind::Agent if template.workspace_id.is_empty() => {
                self.workspace_id.clone()
            }
            ConversationKind::Agent => template.workspace_id.clone(),
        };
        self.tabs.push(Tab::conversation(
            String::new(),
            kind,
            workspace,
            template.workspace_label.clone(),
            String::new(),
            String::new(),
        ));
        self.activate(self.tabs.len() - 1);
    }

    pub fn remove(&mut self, keys: &HashSet<String>) {
        let active_removed = keys.contains(&self.active_key);
        let successor = active_removed.then(|| self.successor(keys)).flatten();
        let mut template: Option<Tab> = None;
        self.tabs.retain(|tab| {
            if !keys.contains(&tab.key) {
                return true;
            }
            if template
                .as_ref()
                .is_none_or(|prior| prior.kind == Kind::Page && tab.kind != Kind::Page)
            {
                template = Some(tab.clone());
            }
            self.closed_keys.push(tab.key.clone());
            self.renderers.remove(&tab.key);
            false
        });
        if let Some(template) = template {
            self.closed_keys
                .drain(..self.closed_keys.len().saturating_sub(2048));
            if active_removed {
                self.activate_successor(successor);
            }
            if active_removed || !self.tabs.iter().any(Tab::is_main) {
                self.ensure_main(&template);
            }
        }
    }
}
