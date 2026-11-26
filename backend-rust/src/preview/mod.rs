//! Host preview targets and the separate, token-scoped local origin.

mod routes;
mod transport;

use std::{
    collections::HashMap,
    io,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

use axum::http::{HeaderMap, header::HOST, uri::Authority};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use ring::{
    hmac,
    rand::{SecureRandom, SystemRandom},
};
use serde_json::{Value, json};
use tokio::{net::TcpListener, sync::OnceCell};

use crate::{
    legacy::LegacyDocument, tools::read_file::ReadFile, workspace_catalog::RegisteredWorkspace,
    worktree::Worktree,
};

use super::{
    Runtime, RuntimeError, Status, goals::verify_conversation_root, iso_time, legacy_error, now,
    store::Store, verify_workspace_binding, web_messages::project_preview_targets,
};

type WorkspaceLookup = dyn Fn(&str) -> io::Result<Option<RegisteredWorkspace>> + Send + Sync;

#[derive(Clone)]
pub struct Preview(Arc<Service>);

struct Service {
    runtime: Runtime,
    workspace: Box<WorkspaceLookup>,
    client: reqwest::Client,
    secret: hmac::Key,
    port: OnceCell<u16>,
    registry: Mutex<HashMap<String, Scope>>,
    current: Mutex<HashMap<String, Scope>>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct Scope {
    workspace: String,
    root: PathBuf,
    conversation: String,
}

struct Context {
    scope: Scope,
    data: PathBuf,
    metadata: Value,
}

impl Preview {
    /// Clones share a single listener and capability registry. The catalog lookup
    /// revalidates the workspace for independent requests after catalog changes.
    pub fn new(
        runtime: Runtime,
        workspace: impl Fn(&str) -> io::Result<Option<RegisteredWorkspace>> + Send + Sync + 'static,
    ) -> io::Result<Self> {
        Ok(Self(Arc::new(Service {
            runtime,
            workspace: Box::new(workspace),
            client: transport::client()?,
            secret: hmac::Key::generate(hmac::HMAC_SHA256, &SystemRandom::new())
                .map_err(|_| io::Error::other("Cannot initialize preview token generation"))?,
            port: OnceCell::new(),
            registry: Mutex::default(),
            current: Mutex::default(),
        })))
    }

    /// Publish the independent origin and capability from an authenticated Web
    /// projection. Non-loopback Web hosts retain the authenticated fallback.
    pub async fn runtime_for(
        &self,
        headers: &HeaderMap,
        workspace: &RegisteredWorkspace,
        conversation: &str,
    ) -> (Option<String>, Option<String>) {
        let scope = Scope::new(workspace, conversation);
        let isolated = host(headers).is_some_and(|host| {
            matches!(
                host.host().to_ascii_lowercase().as_str(),
                "localhost" | "127.0.0.1" | "[::1]"
            )
        });
        let service = self.clone();
        let Ok(Ok(token)) = tokio::task::spawn_blocking(move || {
            let store = service.0.runtime.store()?;
            let document = scope.read(&store)?;
            service.remember(&scope);
            Ok::<_, RuntimeError>(
                isolated
                    .then(|| service.token(&store, scope, document))
                    .flatten(),
            )
        })
        .await
        else {
            return (None, None);
        };
        let Some(token) = token else {
            return (None, None);
        };
        let Ok(port) = self.ensure_listener().await else {
            return (None, None);
        };
        (Some(format!("http://127.0.0.1:{port}")), Some(token))
    }

    async fn ensure_listener(&self) -> io::Result<u16> {
        self.0
            .port
            .get_or_try_init(|| async {
                let listener = TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0)).await?;
                let port = listener.local_addr()?.port();
                let router = self.independent_router(port);
                tokio::spawn(async move {
                    let _ = axum::serve(listener, router).await;
                });
                Ok(port)
            })
            .await
            .copied()
    }

    fn remember(&self, scope: &Scope) {
        self.0
            .current
            .lock()
            .unwrap_or_else(|lock| lock.into_inner())
            .insert(scope.workspace.clone(), scope.clone());
    }

    fn current(&self, workspace: &RegisteredWorkspace) -> Option<Scope> {
        self.0
            .current
            .lock()
            .unwrap_or_else(|lock| lock.into_inner())
            .get(&workspace.id)
            .filter(|scope| scope.root == workspace.path)
            .cloned()
    }

    fn token(&self, store: &Store, scope: Scope, document: LegacyDocument) -> Option<String> {
        let mut registry = self
            .0
            .registry
            .lock()
            .unwrap_or_else(|lock| lock.into_inner());
        let mut raw = document.into_raw();
        let saved = raw["metadata"]["preview_token"]
            .as_str()
            .filter(|token| valid_token(token));
        if let Some(token) =
            saved.filter(|token| registry.get(*token).is_none_or(|entry| *entry == scope))
        {
            registry.insert(token.to_owned(), scope);
            return Some(token.to_owned());
        }
        let mut stable = format!("{}\0{}\0", scope.workspace, scope.conversation).into_bytes();
        stable.extend_from_slice(scope.root.as_os_str().as_encoded_bytes());
        let fallback = URL_SAFE_NO_PAD.encode(&hmac::sign(&self.0.secret, &stable).as_ref()[..24]);
        if registry.get(&fallback) == Some(&scope) {
            return Some(fallback);
        }
        let active = store.tasks.values().any(|task| {
            task.status == Status::Running
                && task.workspace_id == scope.workspace
                && task.conversation_id == scope.conversation
        });
        let mut random = [0; 24];
        let persisted = (!active && SystemRandom::new().fill(&mut random).is_ok())
            .then(|| {
                let token = URL_SAFE_NO_PAD.encode(random);
                if registry.contains_key(&token) {
                    return None;
                }
                raw["metadata"]["preview_token"] = json!(token);
                let updated = LegacyDocument::from_value(raw).ok()?;
                store.persist(&scope.workspace, updated, false).ok()?;
                Some(token)
            })
            .flatten();
        let token = persisted.unwrap_or(fallback);
        if registry.get(&token).is_some_and(|entry| *entry != scope) {
            return None;
        }
        registry.insert(token.clone(), scope);
        Some(token)
    }

    fn registered(&self, token: &str) -> Option<Scope> {
        if !valid_token(token) {
            return None;
        }
        let scope = self
            .0
            .registry
            .lock()
            .unwrap_or_else(|lock| lock.into_inner())
            .get(token)
            .cloned()?;
        let workspace = (self.0.workspace)(&scope.workspace).ok()??;
        (workspace.id == scope.workspace && workspace.path == scope.root).then_some(scope)
    }

    fn context(&self, scope: &Scope) -> Result<Context, RuntimeError> {
        let store = self.0.runtime.store()?;
        let mut document = scope.read(&store)?.into_raw();
        Ok(Context {
            scope: scope.clone(),
            data: store.data_dir.clone(),
            metadata: document["metadata"].take(),
        })
    }

    fn remove(&self, scope: &Scope, key: &str) -> Result<Vec<Value>, RuntimeError> {
        let mut store = self.0.runtime.store()?;
        let mut raw = scope.read(&store)?.into_raw();
        let mut targets = project_preview_targets(&raw["metadata"]["preview_targets"], &|_| true);
        let original = targets.len();
        targets.retain(|target| target_key(target).as_deref() != Some(key));
        if targets.len() != original {
            raw["metadata"]["preview_targets"] = json!(targets);
            if let Some(timestamp) = iso_time(now()) {
                raw["updated_at"] = json!(timestamp);
            }
            let document = LegacyDocument::from_value(raw).map_err(legacy_error)?;
            store.persist(&scope.workspace, document, false)?;
            for task in store.tasks.values_mut().filter(|task| {
                task.status == Status::Running
                    && task.workspace_id == scope.workspace
                    && task.conversation_id == scope.conversation
            }) {
                task.emit(
                    "preview_targets_updated",
                    json!({
                        "conversation_id": scope.conversation,
                        "preview_targets": targets,
                    }),
                );
            }
        }
        Ok(targets)
    }
}

impl Scope {
    fn new(workspace: &RegisteredWorkspace, conversation: &str) -> Self {
        let conversation = conversation.trim();
        Self {
            workspace: workspace.id.clone(),
            root: workspace.path.clone(),
            conversation: if conversation.starts_with("conv_") {
                conversation.to_owned()
            } else {
                format!("conv_{conversation}")
            },
        }
    }

    fn read(&self, store: &Store) -> Result<LegacyDocument, RuntimeError> {
        verify_workspace_binding(store, &self.workspace, Some(&self.root))?;
        let document = store.read(&self.workspace, &self.conversation)?;
        verify_conversation_root(&self.root, document.raw())
            .map_err(|_| RuntimeError::session_not_found())?;
        Ok(document)
    }
}

impl Context {
    fn execution_root(&self) -> Option<PathBuf> {
        if self.metadata["git_worktree_enabled"] == true && !self.metadata["git_worktree"].is_null()
        {
            return Worktree::new(&self.scope.root)
                .ok()?
                .execution_root(&self.metadata)
                .ok();
        }
        Some(self.scope.root.clone())
    }

    fn targets(&self) -> Vec<Value> {
        let files = FileScope::open(
            &self.scope.root,
            self.execution_root().as_deref(),
            &self.data,
        );
        project_preview_targets(&self.metadata["preview_targets"], &|path| {
            files.exists(path)
        })
    }

    fn port_registered(&self, port: u16) -> bool {
        let targets = project_preview_targets(&self.metadata["preview_targets"], &|_| false);
        targets.iter().any(|target| {
            target["type"] == "server"
                && target["origin"].as_str().and_then(explicit_port) == Some(port)
        })
    }
}

struct FileScope {
    workspace: PathBuf,
    execution: Option<PathBuf>,
    primary: Option<ReadFile>,
    worktree: Option<ReadFile>,
}

impl FileScope {
    fn open(workspace: &Path, execution: Option<&Path>, data: &Path) -> Self {
        Self {
            workspace: workspace.to_owned(),
            execution: execution.map(Path::to_owned),
            primary: ReadFile::open(workspace, data).ok(),
            worktree: execution
                .filter(|root| *root != workspace)
                .and_then(|root| ReadFile::open(root, data).ok()),
        }
    }

    fn resolve(&self, path: &str) -> Option<(&Path, &ReadFile)> {
        if path.starts_with("/workspace/")
            || Path::new(path).is_absolute() && Path::new(path).starts_with(&self.workspace)
        {
            return self
                .primary
                .as_ref()
                .map(|files| (self.workspace.as_path(), files));
        }
        let execution = self.execution.as_deref()?;
        let files = if execution == self.workspace {
            &self.primary
        } else {
            &self.worktree
        };
        Some((execution, files.as_ref()?))
    }

    fn exists(&self, path: &str) -> bool {
        let path = path.trim();
        let Some((root, _)) = self.resolve(path) else {
            return false;
        };
        let candidate = root.join(path.strip_prefix("/workspace/").unwrap_or(path));
        candidate
            .canonicalize()
            .is_ok_and(|canonical| canonical.starts_with(root) && canonical.is_file())
    }

    fn read(&self, path: &str) -> Result<Vec<u8>, String> {
        let path = path.trim();
        let (_, files) = self
            .resolve(path)
            .ok_or_else(|| "File is unavailable".to_owned())?;
        files.read_bytes(path)
    }
}

fn target_key(target: &Value) -> Option<String> {
    if target["type"] == "server" {
        Some(format!("server:{}", target["url"].as_str()?))
    } else {
        Some(format!("file:{}", target["path"].as_str()?))
    }
}

fn explicit_port(origin: &str) -> Option<u16> {
    let (_, authority) = origin.split_once("://")?;
    authority
        .parse::<Authority>()
        .ok()?
        .port_u16()
        .filter(|port| *port > 0)
}

fn valid_token(value: &str) -> bool {
    value.len() >= 16
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"-_".contains(&byte))
}

fn host(headers: &HeaderMap) -> Option<Authority> {
    let mut values = headers.get_all(HOST).iter();
    let authority = values.next()?.to_str().ok()?.parse().ok()?;
    values.next().is_none().then_some(authority)
}

#[cfg(test)]
#[path = "../../../test/2026-11-26_Rust预览/preview_contract.rs"]
mod tests;
