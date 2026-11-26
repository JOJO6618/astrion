use std::{
    collections::{HashMap, HashSet},
    fs::{self, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, MutexGuard},
    time::{Duration, Instant},
};

#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;

use ring::digest::{SHA256, digest};
use serde_json::{Map, Value, json};
use uuid::Uuid;

use super::commands::{FOCUS_ACTIONS, same_conversation};
use super::model::{ConversationKind, Kind, PageType, Receipt, State, conversation_id};
use super::{Lookup, NavigationError, text};

const RECHECK_AFTER: Duration = Duration::from_secs(5);

#[derive(Clone)]
pub struct Navigation {
    path: PathBuf,
    process: Arc<Mutex<ProcessState>>,
}

#[derive(Default)]
struct ProcessState {
    checked: HashMap<String, Instant>,
    focused: HashMap<String, String>,
}

impl Navigation {
    /// Use the same DATA_DIR as the Host runtime. Clones share one writer lock.
    pub fn new(data_dir: impl AsRef<Path>) -> Self {
        Self {
            path: data_dir.as_ref().join("conversation_tabs.json"),
            process: Arc::default(),
        }
    }

    fn lock(&self) -> Result<MutexGuard<'_, ProcessState>, NavigationError> {
        self.process
            .lock()
            .map_err(|_| NavigationError::storage("Desktop navigation lock is unavailable"))
    }

    fn load(&self, user: &str) -> Result<(Map<String, Value>, State, bool), NavigationError> {
        let users: Map<String, Value> = match fs::read(&self.path) {
            Ok(data) => serde_json::from_slice(&data).map_err(NavigationError::storage)?,
            Err(error) if error.kind() == io::ErrorKind::NotFound => Map::new(),
            Err(error) => return Err(NavigationError::storage(error)),
        };
        let (state, changed) = State::load(users.get(user).unwrap_or(&Value::Null))?;
        Ok((users, state, changed))
    }

    fn save(
        &self,
        mut users: Map<String, Value>,
        user: &str,
        state: &mut State,
    ) -> Result<(), NavigationError> {
        state.revision = state
            .revision
            .checked_add(1)
            .ok_or_else(|| NavigationError::storage("Desktop navigation revision overflow"))?;
        users.insert(
            user.into(),
            serde_json::to_value(state).map_err(NavigationError::storage)?,
        );
        write_json(&self.path, &users).map_err(NavigationError::storage)
    }

    pub(super) fn snapshot(&self, user: &str, lookup: &Lookup) -> Result<State, NavigationError> {
        let mut process = self.lock()?;
        let (users, mut state, migrated) = self.load(user)?;
        let reconcile = process
            .checked
            .get(user)
            .is_none_or(|last| last.elapsed() >= RECHECK_AFTER);
        let changed = reconcile && state.reconcile(lookup);
        if migrated || changed {
            self.save(users, user, &mut state)?;
        }
        if reconcile {
            process.checked.insert(user.into(), Instant::now());
        }
        Ok(state)
    }

    pub(super) fn command(
        &self,
        user: &str,
        id: &str,
        action: &str,
        payload: &Value,
        lookup: Option<&Lookup>,
    ) -> Result<(State, Value), NavigationError> {
        if id.is_empty() || id.chars().count() > 100 {
            return Err(NavigationError::invalid("Missing command identity"));
        }
        if !payload.is_object() {
            return Err(NavigationError::invalid("Invalid navigation command"));
        }
        let mut process = self.lock()?;
        let (users, mut state, _) = self.load(user)?;
        if action == "focus" {
            let key = text(&payload["key"]);
            state.require_tab(&key)?;
            process.focused.insert(user.into(), key.clone());
            return Ok((state, json!({"key": key})));
        }
        let fingerprint = fingerprint(action, payload);
        if let Some(receipt) = state.receipts.iter().find(|item| item.id == id) {
            if receipt.fingerprint != fingerprint {
                return Err(NavigationError::conflict(
                    "Command identity has already been used",
                ));
            }
            let result = receipt.result.clone();
            return Ok((state, result));
        }
        let result = if let Some(lookup) = lookup {
            state.apply(action, payload, lookup)?
        } else {
            state.delete(payload)?
        };
        state.receipts.push(Receipt {
            id: id.into(),
            fingerprint,
            result: result.clone(),
        });
        state
            .receipts
            .drain(..state.receipts.len().saturating_sub(256));
        self.save(users, user, &mut state)?;
        if FOCUS_ACTIONS.contains(&action)
            && let Some(key) = result["key"].as_str()
        {
            process.focused.insert(user.into(), key.into());
        }
        Ok((state, result))
    }

    /// Query the last focused tab, falling back to the active main tab.
    pub fn focused_key(&self, user: &str) -> Result<Option<String>, NavigationError> {
        let process = self.lock()?;
        let (_, state, _) = self.load(user)?;
        let focused = process
            .focused
            .get(user)
            .filter(|key| state.position(key).is_some());
        Ok(focused
            .cloned()
            .or_else(|| (!state.active_key.is_empty()).then_some(state.active_key)))
    }

    /// Read a focused Agent conversation without reconciling or saving navigation state.
    pub fn focused_session(&self, user: &str) -> Result<Option<(String, String)>, NavigationError> {
        let process = self.lock()?;
        let (_, state, _) = self.load(user)?;
        let preferred = process
            .focused
            .get(user)
            .and_then(|key| state.position(key));
        let position = preferred.or_else(|| state.position(&state.active_key));
        Ok(position
            .and_then(|index| state.tabs.get(index))
            .filter(|tab| {
                tab.kind == Kind::Conv
                    && tab.conversation_kind != Some(ConversationKind::Chat)
                    && !tab.workspace_id.is_empty()
                    && !tab.conversation_id.is_empty()
            })
            .map(|tab| (tab.workspace_id.clone(), tab.conversation_id.clone())))
    }

    /// After a confirmed deletion, remove every matching tab, including detached tabs.
    pub fn deleted(&self, user: &str, workspace_id: &str, id: &str) -> Result<(), NavigationError> {
        let payload = json!({"workspace_id": workspace_id, "conversation_id": id});
        self.command(
            user,
            &Uuid::new_v4().simple().to_string(),
            "deleted",
            &payload,
            None,
        )?;
        Ok(())
    }

    /// Keep each existing tab key, renderer, order and window placement after an Agent move.
    pub fn relocated(
        &self,
        user: &str,
        from: &str,
        to: &str,
        id: &str,
        label: &str,
    ) -> Result<(), NavigationError> {
        self.relocate_before(user, from, to, id, label, |publish| publish())
    }

    /// Hold the navigation lock while the caller moves durable conversation
    /// state. Publish the relocated tab after the destination is available.
    pub(crate) fn relocate_before<T, E>(
        &self, user: &str, from: &str, to: &str, id: &str, label: &str,
        action: impl FnOnce(&mut dyn FnMut() -> Result<(), E>) -> Result<T, E>,
    ) -> Result<T, E>
    where E: From<NavigationError> {
        if to.is_empty() {
            return Err(NavigationError::invalid("Invalid target workspace").into());
        }
        let id = conversation_id(id)?;
        if id.is_empty() {
            return Err(NavigationError::invalid("Invalid conversation identity").into());
        }
        let mut guard = self.lock()?;
        action(&mut || {
            let (users, mut state, _) = self.load(user)?;
            let mut moved = false;
            for tab in state.tabs.iter_mut().filter(|tab| {
                tab.kind == Kind::Conv
                    && tab.conversation_kind == Some(ConversationKind::Agent)
                    && tab.workspace_id == from
                    && same_conversation(&tab.conversation_id, &id)
            }) {
                tab.workspace_id = to.into();
                tab.workspace_label = label.into();
                tab.error.clear();
                state.renderers.remove(&tab.key);
                moved = true;
            }
            if moved {
                self.save(users, user, &mut state)?;
                guard.checked.remove(user);
            }
            Ok(())
        })
    }
}

impl State {
    fn reconcile(&mut self, lookup: &Lookup) -> bool {
        let mut changed = false;
        let mut missing = HashSet::new();
        for tab in &mut self.tabs {
            if tab.kind == Kind::Page && tab.page_type != Some(PageType::Git) {
                continue;
            }
            let workspace = match tab.conversation_kind {
                Some(ConversationKind::Chat) => Ok(None),
                _ if tab.workspace_id.is_empty() => Err(NavigationError::no_workspace()),
                _ => lookup.workspace(&tab.workspace_id).map(Some),
            };
            let existence =
                if workspace.is_ok() && !tab.conversation_id.is_empty() && tab.kind != Kind::Page {
                    lookup
                        .session(
                            tab.conversation_kind.expect("validated tab"),
                            &tab.workspace_id,
                            &tab.conversation_id,
                        )
                        .map(|session| session.is_some())
                } else {
                    Ok(true)
                };
            match (workspace, existence) {
                (Err(_), _) | (_, Err(_)) => {
                    if tab.error != "workspace_unavailable" {
                        tab.error = "workspace_unavailable".into();
                        changed = true;
                    }
                }
                (_, Ok(false)) => {
                    missing.insert(tab.key.clone());
                }
                (Ok(workspace), Ok(true)) => {
                    if let Some(workspace) = workspace
                        && !workspace.label.is_empty()
                        && tab.workspace_label != workspace.label
                    {
                        tab.workspace_label.clone_from(&workspace.label);
                        changed = true;
                    }
                    if tab.error == "workspace_unavailable" {
                        tab.error.clear();
                        changed = true;
                    }
                }
            }
        }
        self.remove(&missing);
        changed || !missing.is_empty()
    }
}

fn write_json(target: &Path, users: &Map<String, Value>) -> io::Result<()> {
    let parent = target
        .parent()
        .ok_or_else(|| io::Error::other("Missing data directory"))?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(
        ".conversation_tabs.json.{}.tmp",
        Uuid::new_v4().simple()
    ));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    options.mode(0o600);
    let mut file = options.open(&temporary)?;
    let result = serde_json::to_writer_pretty(&mut file, users)
        .map_err(io::Error::other)
        .and_then(|()| file.write_all(b"\n"))
        .and_then(|()| file.sync_all());
    drop(file);
    let result = result.and_then(|()| replace(&temporary, target));
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

#[cfg(not(windows))]
fn replace(from: &Path, to: &Path) -> io::Result<()> {
    fs::rename(from, to)
}

#[cfg(windows)]
fn replace(from: &Path, to: &Path) -> io::Result<()> {
    let mut delay = Duration::from_millis(50);
    for _ in 0..7 {
        match fs::rename(from, to) {
            Err(error) if matches!(error.raw_os_error(), Some(5 | 32)) => {
                std::thread::sleep(delay);
                delay = (delay * 2).min(Duration::from_millis(1600));
            }
            result => return result,
        }
    }
    fs::rename(from, to)
}

fn fingerprint(action: &str, payload: &Value) -> String {
    // Python receipts are reused across backend restarts and upgrades. Preserve its
    // sorted keys, Unicode strings and default comma/colon spacing for the hash.
    let mut encoded = String::new();
    python_json(&json!([action, payload]), &mut encoded);
    digest(&SHA256, encoded.as_bytes())
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn python_json(value: &Value, out: &mut String) {
    match value {
        Value::Object(object) => {
            out.push('{');
            let mut entries: Vec<_> = object.iter().collect();
            entries.sort_unstable_by(|(left, _), (right, _)| left.cmp(right));
            for (index, (key, value)) in entries.into_iter().enumerate() {
                if index != 0 {
                    out.push_str(", ");
                }
                out.push_str(&Value::String(key.clone()).to_string());
                out.push_str(": ");
                python_json(value, out);
            }
            out.push('}');
        }
        Value::Array(items) => {
            out.push('[');
            for (index, value) in items.iter().enumerate() {
                if index != 0 {
                    out.push_str(", ");
                }
                python_json(value, out);
            }
            out.push(']');
        }
        Value::Number(number) if number.is_f64() => {
            let raw = format!("{:?}", number.as_f64().expect("finite JSON float"));
            if let Some((mantissa, exponent)) = raw.split_once('e') {
                let exponent: i32 = exponent.parse().expect("float exponent");
                out.push_str(&format!("{mantissa}e{exponent:+03}"));
            } else {
                out.push_str(&raw);
            }
        }
        _ => out.push_str(&value.to_string()),
    }
}
