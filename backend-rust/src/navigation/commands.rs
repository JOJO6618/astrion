use std::collections::HashSet;

use serde_json::{Value, json};

use super::model::{
    ConversationKind, Kind, MAX_TABS, PageType, Placement, State, Tab, clipped, conversation_id,
    valid_id,
};
use super::{Lookup, NavigationError, nonempty, text};

pub(super) const FOCUS_ACTIONS: &[&str] = &["create", "open", "workspace", "activate", "page"];

impl State {
    pub fn apply(
        &mut self,
        action: &str,
        payload: &Value,
        lookup: &Lookup,
    ) -> Result<Value, NavigationError> {
        let key = text(&payload["key"]);
        let nothing = json!({});
        match action {
            "create" | "open" | "workspace" => self.open(action, payload, lookup),
            "page" => self.open_page(payload, lookup),
            "activate" => {
                let index = self.require_main(&key)?;
                self.activate(index);
                Ok(json!({"key": key}))
            }
            "close" => {
                self.remove(&HashSet::from([key]));
                Ok(nothing)
            }
            "reorder" => {
                self.reorder(&key, &text(&payload["before_key"]))?;
                Ok(nothing)
            }
            "detach" => {
                self.detach(&key)?;
                Ok(nothing)
            }
            "dock" => self.dock(&key),
            "attach" => {
                let index = self.require_tab(&key)?;
                if payload.get("workspace_id").is_some()
                    && payload["workspace_id"].as_str() != Some(&self.tabs[index].workspace_id)
                {
                    return Err(NavigationError::conflict("Tab owner has changed"));
                }
                let renderer = text(&payload["renderer_id"]);
                if renderer.is_empty() || renderer.chars().count() > 200 {
                    return Err(NavigationError::invalid("Missing renderer identity"));
                }
                self.renderers.insert(key, renderer);
                self.tabs[index].error.clear();
                Ok(nothing)
            }
            "bind" => self.bind(&key, payload, lookup),
            "fail" => self.fail(&key, payload, lookup),
            "title" => {
                let id = conversation_id(&text(&payload["conversation_id"]))?;
                for tab in self.tabs.iter_mut().filter(|tab| {
                    tab.kind != Kind::Page
                        && !id.is_empty()
                        && same_conversation(&tab.conversation_id, &id)
                        && payload["workspace_id"].as_str() == Some(&tab.workspace_id)
                }) {
                    tab.title = clipped(&text(&payload["title"]), 200);
                }
                Ok(nothing)
            }
            _ => Err(NavigationError::invalid(
                "Unknown desktop navigation command",
            )),
        }
    }

    pub fn delete(&mut self, payload: &Value) -> Result<Value, NavigationError> {
        let workspace = text(&payload["workspace_id"]);
        let id = conversation_id(&text(&payload["conversation_id"]))?;
        if !id.is_empty() {
            let keys = self
                .tabs
                .iter()
                .filter(|tab| {
                    tab.kind != Kind::Page
                        && tab.workspace_id == workspace
                        && same_conversation(&tab.conversation_id, &id)
                })
                .map(|tab| tab.key.clone())
                .collect();
            self.remove(&keys);
        }
        Ok(json!({}))
    }

    pub fn require_tab(&self, key: &str) -> Result<usize, NavigationError> {
        self.position(key)
            .ok_or_else(|| NavigationError::conflict("Tab is closed"))
    }

    fn require_main(&self, key: &str) -> Result<usize, NavigationError> {
        let index = self.require_tab(key)?;
        if !self.tabs[index].is_main() {
            return Err(NavigationError::conflict("Tab is detached"));
        }
        Ok(index)
    }

    fn workspace(
        &self,
        payload: &Value,
        lookup: &Lookup,
    ) -> Result<(ConversationKind, String, String), NavigationError> {
        let requested = text(&payload["workspace_id"]);
        match payload.get("conversation_kind").map(Value::as_str) {
            Some(Some("chat")) if requested.is_empty() => {
                Ok((ConversationKind::Chat, String::new(), String::new()))
            }
            Some(Some("chat")) => Err(NavigationError::invalid("Chat has no workspace")),
            None | Some(Some("agent")) => {
                let workspace = lookup.workspace(nonempty(&requested, &self.workspace_id))?;
                let fallback = text(&payload["workspace_label"]);
                Ok((
                    ConversationKind::Agent,
                    workspace.id.clone(),
                    nonempty(&workspace.label, &fallback).into(),
                ))
            }
            _ => Err(NavigationError::invalid("Invalid conversation kind")),
        }
    }

    fn open(
        &mut self,
        action: &str,
        payload: &Value,
        lookup: &Lookup,
    ) -> Result<Value, NavigationError> {
        let key = text(&payload["key"]);
        if action == "workspace" && !key.is_empty() {
            let index = self.require_tab(&key)?;
            let tab = &self.tabs[index];
            if tab.kind != Kind::New
                || tab.is_main()
                || tab.conversation_kind == Some(ConversationKind::Chat)
            {
                return Err(NavigationError::invalid(
                    "Only a blank detached tab can change workspace in place",
                ));
            }
            let (_, workspace, label) = self.workspace(payload, lookup)?;
            if workspace.is_empty() {
                return Err(NavigationError::no_workspace());
            }
            let tab = &mut self.tabs[index];
            tab.workspace_id = workspace;
            tab.workspace_label = label;
            tab.error.clear();
            return Ok(json!({"key": key, "rebound": true}));
        }
        let (kind, workspace, label) = self.workspace(payload, lookup)?;
        let raw_id = match action {
            "create" => String::new(),
            "workspace" => lookup.active_conversation(&workspace)?,
            _ => text(&payload["conversation_id"]),
        };
        let id = conversation_id(&raw_id)?;
        let existing = self.tabs.iter().position(|tab| {
            !id.is_empty()
                && tab.kind == Kind::Conv
                && tab.conversation_kind == Some(kind)
                && tab.workspace_id == workspace
                && same_conversation(&tab.conversation_id, &id)
        });
        let index = if let Some(index) = existing {
            index
        } else {
            if !key.is_empty() && !valid_id(&key, 100) {
                return Err(NavigationError::invalid("Invalid tab identity"));
            }
            if !key.is_empty() && (self.closed_keys.contains(&key) || self.position(&key).is_some())
            {
                return Err(NavigationError::conflict(
                    "Tab identity has already been used",
                ));
            }
            self.require_capacity()?;
            let hint = text(&payload["title"]);
            let title = if id.is_empty() {
                hint
            } else {
                let session = lookup
                    .session(kind, &workspace, &id)?
                    .ok_or_else(|| NavigationError::conflict("Conversation no longer exists"))?;
                nonempty(&session.title, &hint).into()
            };
            self.tabs
                .push(Tab::conversation(key, kind, workspace, label, id, title));
            self.tabs.len() - 1
        };
        let key = self.tabs[index].key.clone();
        if self.tabs[index].is_main() {
            self.activate(index);
            Ok(json!({"key": key}))
        } else {
            Ok(json!({"key": key, "focus": "detached"}))
        }
    }

    fn require_capacity(&self) -> Result<(), NavigationError> {
        if self.tabs.len() >= MAX_TABS {
            return Err(NavigationError::invalid("Too many open tabs"));
        }
        Ok(())
    }

    fn open_page(&mut self, payload: &Value, lookup: &Lookup) -> Result<Value, NavigationError> {
        let route = clipped(
            nonempty(&text(&payload["route"]), &text(&payload["page"])),
            500,
        );
        let path = route.split('?').next().unwrap_or_default();
        let page_type = match path {
            "/settings" => PageType::Settings,
            path if path.starts_with("/settings/") => PageType::Settings,
            "/workflows" | "/workflow" => PageType::Workflows,
            path if path.starts_with("/workflow/") => PageType::Workflows,
            "/git" => PageType::Git,
            "/schedules" => PageType::Schedules,
            _ => return Err(NavigationError::invalid("Invalid desktop page")),
        };
        if !payload["page_type"].is_null()
            && payload["page_type"] != ""
            && payload["page_type"] != json!(page_type)
        {
            return Err(NavigationError::invalid("Invalid desktop page"));
        }
        let (workspace, label) = if page_type == PageType::Git {
            let parsed = reqwest::Url::parse(&format!("http://localhost{route}"))
                .map_err(|_| NavigationError::invalid("Invalid desktop page"))?;
            let query = parsed
                .query_pairs()
                .find(|(key, value)| key == "workspace_id" && !value.is_empty())
                .map(|(_, value)| value.into_owned())
                .unwrap_or_default();
            let fallback = text(&payload["workspace_id"]);
            let requested = nonempty(&query, &fallback);
            if requested.is_empty() {
                return Err(NavigationError::invalid("Git page requires a workspace"));
            }
            let workspace = lookup
                .workspace(requested)
                .map_err(|_| NavigationError::invalid("Workspace unavailable"))?;
            (
                workspace.id.clone(),
                nonempty(&workspace.label, &workspace.id).into(),
            )
        } else {
            (String::new(), String::new())
        };
        let index = if let Some(index) = self.tabs.iter().position(|tab| {
            tab.kind == Kind::Page
                && tab.page_type == Some(page_type)
                && tab.workspace_id == workspace
        }) {
            let tab = &mut self.tabs[index];
            tab.route = Some(route);
            if !label.is_empty() {
                tab.workspace_label = label;
            }
            if self.active_key != tab.key && !self.active_key.is_empty() {
                tab.opener_key = Some(self.active_key.clone());
            }
            index
        } else {
            self.require_capacity()?;
            self.tabs.push(Tab::page(
                page_type,
                route,
                self.active_key.clone(),
                workspace,
                label,
            ));
            self.tabs.len() - 1
        };
        self.activate(index);
        Ok(json!({"key": self.active_key}))
    }

    fn detach(&mut self, key: &str) -> Result<(), NavigationError> {
        let index = self.require_tab(key)?;
        if self.tabs[index].kind == Kind::Page {
            return Err(NavigationError::invalid(
                "Only conversation tabs can be detached",
            ));
        }
        if !self.tabs[index].is_main() {
            return Ok(());
        }
        let loses_active = self.active_key == key;
        let next = loses_active
            .then(|| self.successor(&HashSet::from([key.into()])))
            .flatten();
        self.tabs[index].placement = Placement::Detached;
        if loses_active {
            self.activate_successor(next);
        }
        self.ensure_main(&self.tabs[index].clone());
        Ok(())
    }

    fn dock(&mut self, key: &str) -> Result<Value, NavigationError> {
        let index = self.require_tab(key)?;
        if self.tabs[index].kind == Kind::New && !self.tabs[index].is_main() {
            self.remove(&HashSet::from([key.into()]));
            return Ok(json!({"removed": true}));
        }
        let mut tab = self.tabs.remove(index);
        tab.placement = Placement::Main;
        self.tabs.push(tab);
        if self.active_key.is_empty() {
            self.activate(self.tabs.len() - 1);
        }
        Ok(json!({}))
    }

    fn reorder(&mut self, key: &str, before: &str) -> Result<(), NavigationError> {
        let index = self.require_main(key)?;
        let before_index = if before.is_empty() {
            self.tabs
                .iter()
                .rposition(Tab::is_main)
                .map_or(0, |last| last + 1)
        } else {
            self.require_main(before)
                .map_err(|_| NavigationError::conflict("Reorder target is unavailable"))?
        };
        let item = self.tabs.remove(index);
        self.tabs
            .insert(before_index - usize::from(index < before_index), item);
        Ok(())
    }

    fn require_owner(&self, key: &str, payload: &Value) -> Result<(), NavigationError> {
        let renderer = payload["renderer_id"].as_str().filter(|id| !id.is_empty());
        if renderer.is_none() || self.renderers.get(key).map(String::as_str) != renderer {
            return Err(NavigationError::conflict("Tab owner has changed"));
        }
        Ok(())
    }

    fn bind(
        &mut self,
        key: &str,
        payload: &Value,
        lookup: &Lookup,
    ) -> Result<Value, NavigationError> {
        let Some(index) = self.position(key) else {
            return Ok(json!({"bound": false}));
        };
        let tab = &self.tabs[index];
        if payload["workspace_id"].as_str() != Some(&tab.workspace_id) {
            return Err(NavigationError::conflict("Tab owner has changed"));
        }
        self.require_owner(key, payload)?;
        let id = conversation_id(&text(&payload["conversation_id"]))?;
        if id.is_empty() {
            return Ok(json!({"bound": false}));
        }
        if tab.kind != Kind::New {
            if same_conversation(&tab.conversation_id, &id) {
                return Ok(json!({"bound": true}));
            }
            return Err(NavigationError::conflict("Tab is already bound"));
        }
        let kind = tab.conversation_kind.expect("validated conversation tab");
        if lookup.session(kind, &tab.workspace_id, &id)?.is_none() {
            return Err(NavigationError::conflict("Conversation no longer exists"));
        }
        let tab = &mut self.tabs[index];
        tab.kind = Kind::Conv;
        tab.conversation_id = id;
        tab.title = clipped(&text(&payload["title"]), 200);
        Ok(json!({"bound": true}))
    }

    fn fail(
        &mut self,
        key: &str,
        payload: &Value,
        lookup: &Lookup,
    ) -> Result<Value, NavigationError> {
        let Some(index) = self.position(key) else {
            return Ok(json!({}));
        };
        self.require_owner(key, payload)?;
        let tab = &mut self.tabs[index];
        if tab.kind == Kind::Conv
            && let Some(kind) = tab.conversation_kind
            && lookup
                .session(kind, &tab.workspace_id, &tab.conversation_id)
                .is_ok_and(|session| session.is_none())
        {
            self.remove(&HashSet::from([key.into()]));
            return Ok(json!({"removed": true}));
        }
        tab.error = clipped(nonempty(&text(&payload["error"]), "load_failed"), 500);
        Ok(json!({}))
    }
}

pub(super) fn same_conversation(left: &str, normalized: &str) -> bool {
    left == normalized || conversation_id(left).is_ok_and(|id| id == normalized)
}
