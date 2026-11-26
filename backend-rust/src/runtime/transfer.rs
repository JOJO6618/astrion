//! Authenticated conversation transfer through the ordinary Host writer.
mod http;

pub use self::http::router;

use std::{
    fs,
    io::{self, ErrorKind},
    path::{Path, PathBuf},
    sync::Arc,
    time::{Duration, SystemTime},
};

use cap_std::fs::Dir;
use serde_json::{Value, json};
use uuid::Uuid;

use crate::{
    catalog::CatalogSource,
    chat_mode::migration_summary,
    conversation_db::Database,
    conversation_transfer::{
        MAX_UPLOAD,
        archive::{self, Archive, Manifest},
        assets::{self, Assets},
        bad, conflict,
        document::{self, Import},
        encode, io_error, kind_name,
        restore::Restore,
        sha256,
    },
    knowledge::storage::{open_canonical_dir, publish, read_bytes},
    legacy::{LegacyDocument, LegacyKind},
    navigation::Navigation,
    workspace_catalog::RegisteredWorkspace,
};

use super::{
    ConversationSelection, Runtime, RuntimeError, Status, Store,
    goals::verify_conversation_root,
    iso_time, now,
    store::{legacy_error, read_json},
    verify_workspace_binding,
};

const DEFAULT_FILENAME: &str = "conversation.astrion.zip";
const BACKUPS: &str = "conversation_transfer_backups";
const RECEIPTS: &str = "conversation_transfer_receipts";

#[derive(Clone)]
struct Transfer {
    runtime: Runtime,
    navigation: Navigation,
    selection: Arc<ConversationSelection>,
    catalog: CatalogSource,
    workspace: Arc<http::WorkspaceResolver>,
    material_workspace:
        Arc<dyn Fn(&str) -> Result<RegisteredWorkspace, RuntimeError> + Send + Sync>,
}

#[derive(Clone)]
enum Scope {
    Chat,
    Agent(RegisteredWorkspace),
}

impl Scope {
    fn kind(&self) -> LegacyKind {
        match self {
            Self::Chat => LegacyKind::Chat,
            Self::Agent(_) => LegacyKind::Agent,
        }
    }

    fn workspace(&self) -> Option<&RegisteredWorkspace> {
        match self {
            Self::Chat => None,
            Self::Agent(workspace) => Some(workspace),
        }
    }

    fn data(&self, store: &Store) -> PathBuf {
        match self {
            Self::Chat => store.data_dir.join("chat"),
            Self::Agent(_) => store.data_dir.clone(),
        }
    }

    fn project(&self) -> Option<&Path> {
        self.workspace().map(|workspace| workspace.path.as_path())
    }

    fn identity(&self) -> Value {
        let workspace = self.workspace();
        json!({
            "workspace_id":workspace.map(|ws| &ws.id),"workspace_name":workspace.map(|ws| &ws.label),
            "project_path":self.project(),"environment":"host",
        })
    }

    fn export_directory(&self, data: &Path) -> (PathBuf, &'static str) {
        match self {
            Self::Chat => (data.join("chat"), "exports"),
            Self::Agent(workspace) => (workspace.path.clone(), ".astrion/conversation_exports"),
        }
    }
}

impl Transfer {
    fn export(&self, scope: &Scope, id: &str) -> Result<Value, RuntimeError> {
        let (data, identity, encoded, resources) = {
            let store = self.runtime.store()?;
            let raw = self.read(&store, scope, id)?;
            self.idle(&store, scope, id, &raw)?;
            let mut identity = scope.identity();
            identity["api_protocol"] = json!(document::source_protocol(&raw, &self.catalog.load()));
            let encoded = encode(&raw)?;
            let resources = self.collect(&store, scope, &raw, false)?;
            (store.data_dir.clone(), identity, encoded, resources)
        };
        let kind = kind_name(scope.kind());
        let manifest = Manifest::new(kind, &encoded, identity, resources.resources, false);
        let archive = archive::write(&encoded, &manifest, &resources.files)?;
        let token = Uuid::new_v4().simple().to_string();
        let (root, relative) = scope.export_directory(&data);
        let directory = open_canonical_dir(&root).map_err(io_error)?;
        let directory = assets::subdir(&directory, relative, true).map_err(io_error)?;
        expire_exports(&directory)?;
        let leaf = format!("{token}.astrion.zip");
        publish(&directory, &leaf, &archive, false).map_err(io_error)?;
        let mut url = format!("/api/conversation-transfer/download/{token}?kind={kind}");
        if let Some(workspace) = scope.workspace() {
            url.push_str(&format!("&workspace_id={}", workspace.id));
        }
        Ok(
            json!({"path":scope.workspace().map(|_| format!("{relative}/{leaf}")),
            "filename":format!("{id}.astrion.zip"),"url":url}),
        )
    }

    fn open_download(
        &self,
        scope: &Scope,
        token: &str,
    ) -> Result<(fs::File, PathBuf), RuntimeError> {
        if !token_name(token) {
            return Err(bad("Invalid export identity"));
        }
        let store = self.runtime.store()?;
        if let Some(workspace) = scope.workspace() {
            verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
        }
        let (root, directory) = scope.export_directory(&store.data_dir);
        let root_handle = open_canonical_dir(&root).map_err(io_error)?;
        let relative = format!("{directory}/{token}.astrion.zip");
        let file = assets::open_file(&root_handle, &relative).map_err(io_error)?;
        if file.metadata().map_err(io_error)?.len() > MAX_UPLOAD as u64 {
            return Err(bad("Conversation export exceeds the size limit"));
        }
        Ok((file, root.join(relative)))
    }

    fn import(
        &self,
        scope: &Scope,
        archive: Archive,
        op: &str,
        model: Option<&str>,
    ) -> Result<Value, RuntimeError> {
        if archive.manifest.kind != kind_name(scope.kind()) {
            return Err(bad("Import this file in its original Chat or Agent mode"));
        }
        document::check_model(
            &archive.document,
            &archive.manifest.source,
            model,
            &self.catalog.load(),
        )?;
        let store = self.runtime.store()?;
        if let Some(workspace) = scope.workspace() {
            verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
        }
        let target = scope.identity();
        let key = format!("import-{}.json", sha256(op.as_bytes()));
        let id = format!("conv_import_{}", &sha256(op.as_bytes())[..32]);
        let binding = json!({"id":id,"document_sha256":archive.manifest.document_sha256,
            "workspace_id":target["workspace_id"],"project_path":target["project_path"],"model_key":model});
        let receipts = receipt_directory(&store)?;
        let receipt = read_receipt(&receipts, &key)?;
        if let Some(existing) = &receipt
            && existing["binding"] != binding
        {
            return Err(conflict(
                "Import operation was reused for a different file, target or model",
            ));
        }
        if let Some(existing) = self.existing(&store, scope, &id)? {
            if receipt.is_none() {
                return Err(conflict(
                    "The target already contains this conversation identity",
                ));
            }
            document::verify_import(
                &existing,
                id_of(&archive.document)?,
                &archive.manifest.document_sha256,
                op,
            )?;
        } else {
            if receipt.as_ref().is_some_and(|value| value["ready"] == true) {
                return Err(conflict("The import destination is no longer available"));
            }
            if receipt.is_none() {
                put_json(
                    &receipts,
                    &key,
                    &json!({"binding":binding,"ready":false}),
                    false,
                )?;
            }
            self.publish(&store, scope, &id, op, model, &target, archive)?;
        }
        if !receipt.as_ref().is_some_and(|value| value["ready"] == true) {
            put_json(
                &receipts,
                &key,
                &json!({"binding":binding,"ready":true}),
                true,
            )?;
        }
        Ok(destination(scope, &id))
    }

    fn migrate(
        &self,
        from: &RegisteredWorkspace,
        to: &RegisteredWorkspace,
        id: &str,
        op: &str,
    ) -> Result<Value, RuntimeError> {
        if from.id == to.id || from.path == to.path {
            return Err(bad("Choose a different target workspace"));
        }
        self.navigation.relocate_before("host", &from.id, &to.id, id, &to.label, |relocate| {
            let source = Scope::Agent(from.clone());
            let target = Scope::Agent(to.clone());
            let identity = target.identity();
            let mut store = self.runtime.store()?;
            verify_workspace_binding(&store, &from.id, Some(&from.path))?;
            verify_workspace_binding(&store, &to.id, Some(&to.path))?;
            let operation = format!("migration:{}:{id}:{op}", from.id);
            let key = format!("move-{}.json", sha256(operation.as_bytes()));
            let receipts = receipt_directory(&store)?;
            let receipt = read_receipt(&receipts, &key)?;
            if let Some(saved) = &receipt
                && (saved["workspace_id"] != to.id || saved["project_path"] != json!(to.path))
            {
                return Err(conflict("Migration operation reused for a different target"));
            }
            let original = self.existing(&store, &source, id)?;
            if receipt.is_none() && original.is_none() {
                return Err(RuntimeError::session_not_found());
            }
            if let Some(raw) = &original {
                self.idle(&store, &source, id, raw)?;
            }
            let encoded = original.as_ref().map(encode).transpose()?;
            let hash = receipt.as_ref().and_then(|item| item["document_sha256"].as_str())
                .map(str::to_owned).or_else(|| encoded.as_ref().map(|bytes| sha256(bytes)))
                .ok_or_else(|| conflict("The migration source is no longer available"))?;
            if encoded.as_ref().is_some_and(|bytes| sha256(bytes) != hash) {
                return Err(conflict("The source has changed since migration; both conversations are preserved"));
            }
            if let Some(existing) = self.existing(&store, &target, id)? {
                if receipt.is_none() { return Err(conflict("The target workspace already contains this conversation identity")); }
                document::verify_import(&existing, id, &hash, &operation)?;
            } else {
                if receipt.as_ref().is_some_and(|item| item["ready"] == true) {
                    return Err(conflict("The migration destination is unavailable; the source is preserved"));
                }
                let raw = original.as_ref().ok_or_else(|| conflict("The migration destination is unavailable"))?;
                document::check_model(raw, &Value::Null, None, &self.catalog.load())?;
                let mut origin = source.identity();
                origin["api_protocol"] = json!(document::source_protocol(raw, &self.catalog.load()));
                let assets = self.collect(&store, &source, raw, true)?;
                let manifest = Manifest::new("agent", encoded.as_ref().expect("source encoded"), origin, assets.resources, true);
                if receipt.is_none() {
                    put_json(&receipts, &key, &json!({
                        "workspace_id":to.id,"project_path":to.path,"document_sha256":hash,"ready":false,
                    }), false)?;
                }
                self.publish(&store, &target, id, &operation, None, &identity,
                    Archive { document: raw.clone(), manifest, files: assets.files })?;
            }
            if !receipt.as_ref().is_some_and(|value| value["ready"] == true) {
                put_json(&receipts, &key, &json!({
                    "workspace_id":to.id,"project_path":to.path,"document_sha256":hash,"ready":true,
                }), true)?;
            }
            if let Some(raw) = original {
                let root = open_canonical_dir(&store.data_dir).map_err(io_error)?;
                let backups = assets::subdir(&root, BACKUPS, true).map_err(io_error)?;
                let name = format!("{id}-{}.json", &sha256(operation.as_bytes())[..24]);
                let backup = json!({"conversation":raw,"destination":destination(&target, id)});
                match read_receipt(&backups, &name)? {
                    Some(saved) if saved != backup => return Err(conflict("The migration source backup does not match")),
                    Some(_) => (),
                    None => put_json(&backups, &name, &backup, false)?,
                }
                relocate()?;
                let path = store.conversation_path(&from.id, id)?;
                store.delete_conversation(&from.id, id)?;
                Runtime::close_process_scope(&mut store, &from.id, Some(id));
                Runtime::release_mcp_scope(&mut store, &from.id, Some(id));
                store.search_sentences.remove(&path);
                self.selection.invalidate(from, id)?;
            } else {
                relocate()?;
            }
            Ok(destination(&target, id))
        })
    }

    fn collect(
        &self,
        store: &Store,
        scope: &Scope,
        raw: &Value,
        shared: bool,
    ) -> Result<Assets, RuntimeError> {
        Assets::collect(
            raw,
            &scope.data(store),
            scope.project(),
            shared,
            self.material_workspace.as_ref(),
        )
    }

    fn read(&self, store: &Store, scope: &Scope, id: &str) -> Result<Value, RuntimeError> {
        self.existing(store, scope, id)?
            .ok_or_else(RuntimeError::session_not_found)
    }

    fn existing(
        &self,
        store: &Store,
        scope: &Scope,
        id: &str,
    ) -> Result<Option<Value>, RuntimeError> {
        if !id.strip_prefix("conv_").is_some_and(|value| {
            !value.is_empty()
                && id.len() <= 185
                && id
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || b"_-".contains(&byte))
        }) {
            return Err(bad("Invalid conversation ID"));
        }
        let raw = match scope {
            Scope::Chat => {
                let directory = scope.data(store).join("conversations");
                let db = Database::open(&directory).map_err(RuntimeError::storage)?;
                let row = db.read(id).map_err(RuntimeError::storage)?;
                if row.is_none()
                    && !db.imported(id).map_err(RuntimeError::storage)?
                    && directory.join(format!("{id}.json")).is_file()
                {
                    return Err(conflict(crate::conversation_db::MIGRATION_REQUIRED));
                }
                row
            }
            Scope::Agent(workspace) => {
                verify_workspace_binding(store, &workspace.id, Some(&workspace.path))?;
                match store.read(&workspace.id, id) {
                    Ok(doc) => {
                        verify_conversation_root(&workspace.path, doc.raw())?;
                        Some(doc.into_raw())
                    }
                    Err(error) if error.status == 404 => None,
                    Err(error) => return Err(error),
                }
            }
        };
        if let Some(raw) = &raw
            && (document::validate(raw)? != scope.kind() || raw["id"] != id)
        {
            return Err(RuntimeError::session_not_found());
        }
        Ok(raw)
    }

    fn idle(
        &self,
        store: &Store,
        scope: &Scope,
        id: &str,
        raw: &Value,
    ) -> Result<(), RuntimeError> {
        let busy = match scope {
            Scope::Chat => {
                !raw["metadata"]["chat"]["active_turn_id"].is_null()
                    || raw["metadata"]["chat"]["handoffs"]
                        .as_object()
                        .is_some_and(|rows| rows.values().any(|item| item["status"] == "preparing"))
            }
            Scope::Agent(workspace) => {
                store.tasks.values().any(|task| {
                    task.workspace_id == workspace.id
                        && task.conversation_id == id
                        && task.status == Status::Running
                }) || Runtime::conversation_has_background(store, &workspace.id, id)
                    || self.runtime.sub_agents.as_ref().is_some_and(|children| {
                        children
                            .has_running_for_bound(workspace, id)
                            .unwrap_or(true)
                    })
            }
        };
        if busy {
            return Err(conflict(
                "Stop the conversation and its background activity before transferring it",
            ));
        }
        Ok(())
    }

    fn publish(
        &self,
        store: &Store,
        scope: &Scope,
        id: &str,
        op: &str,
        model: Option<&str>,
        target: &Value,
        archive: Archive,
    ) -> Result<(), RuntimeError> {
        let now = iso_time(now()).ok_or_else(|| RuntimeError::storage("Invalid transfer time"))?;
        let data = scope.data(store);
        let settings = if scope.workspace().is_some() {
            let preferences =
                read_json(&store.data_dir.join("personalization.json"))?.unwrap_or_default();
            document::modes(&preferences)
        } else {
            json!({})
        };
        let prepared = Import {
            manifest: &archive.manifest,
            target,
            modes: &settings,
            selected: model,
            operation: op,
            id,
            timestamp: &now,
        };
        // Validate the projected tree before writing any project files or media.
        let mut raw = prepared.prepare(&archive.document, json!({}))?;
        let stamp = format!(
            "{op}:{}:{model:?}:{target}",
            archive.manifest.document_sha256
        );
        let restored = Restore::prepare(
            &data,
            scope.project(),
            id,
            &stamp,
            &archive.document,
            &archive.manifest,
            &archive.files,
            &now,
        )?;
        raw["metadata"]["conversation_transfer"]["paths"] = restored.paths.clone();
        match scope {
            Scope::Chat => {
                let mut db =
                    Database::open(&data.join("conversations")).map_err(RuntimeError::storage)?;
                db.save(&raw, &migration_summary(&raw))
                    .map_err(RuntimeError::storage)?;
            }
            Scope::Agent(workspace) => {
                store.persist(
                    &workspace.id,
                    LegacyDocument::from_value(raw).map_err(legacy_error)?,
                    true,
                )?;
            }
        }
        restored.commit();
        Ok(())
    }
}

impl Runtime {
    pub(super) fn transfer_paths(&self, task_id: &str) -> Result<Value, String> {
        let store = self.store().map_err(|error| error.to_string())?;
        let task = store.tasks.get(task_id).ok_or("Task unavailable")?;
        let directory = store
            .conversation_path(&task.workspace_id, &task.conversation_id)
            .map_err(|error| error.to_string())?;
        let directory = directory.parent().ok_or("Missing conversation directory")?;
        if !directory.join(crate::conversation_db::FILENAME).is_file() {
            return Ok(Value::Null);
        }
        let database = Database::open(directory)?;
        Ok(database
            .field(&task.conversation_id, "metadata", "conversation_transfer")?
            .and_then(|metadata| metadata.get("paths").cloned())
            .unwrap_or_default())
    }
}

pub(super) fn read_arguments(name: &str, mut arguments: Value, aliases: &Value) -> Value {
    if name == crate::tools::read_file::NAME
        && let Some(path) = arguments["path"].as_str()
        && let Some(mapped) = aliases[path].as_str()
        && mapped.starts_with(".astrion/conversation_assets/")
        && assets::components(mapped).is_ok()
    {
        arguments["path"] = json!(mapped);
    }
    arguments
}

fn id_of(value: &Value) -> Result<&str, RuntimeError> {
    value["id"]
        .as_str()
        .ok_or_else(|| bad("Invalid conversation identity"))
}

fn destination(scope: &Scope, id: &str) -> Value {
    json!({"conversation_id":id,"workspace_id":scope.workspace().map(|workspace| &workspace.id)})
}

fn receipt_directory(store: &Store) -> Result<Dir, RuntimeError> {
    let root = open_canonical_dir(&store.data_dir).map_err(io_error)?;
    assets::subdir(&root, RECEIPTS, true).map_err(io_error)
}

fn read_receipt(directory: &Dir, name: &str) -> Result<Option<Value>, RuntimeError> {
    match read_bytes(directory, name, 36 * 1024 * 1024) {
        Ok(bytes) => serde_json::from_slice(&bytes)
            .map(Some)
            .map_err(io::Error::from)
            .map_err(io_error),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => Err(io_error(error)),
    }
}

fn put_json(directory: &Dir, name: &str, value: &Value, exists: bool) -> Result<(), RuntimeError> {
    let bytes = serde_json::to_vec(value).map_err(RuntimeError::storage)?;
    publish(directory, name, &bytes, exists).map_err(io_error)
}

fn token_name(token: &str) -> bool {
    token.len() == 32
        && token
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn expire_exports(directory: &Dir) -> Result<(), RuntimeError> {
    use cap_std::time::SystemTime as CapTime;
    let cutoff = CapTime::from_std(SystemTime::now() - Duration::from_secs(24 * 60 * 60));
    for entry in directory.entries().map_err(io_error)? {
        let entry = entry.map_err(io_error)?;
        let filename = entry.file_name();
        if filename
            .to_str()
            .and_then(|name| name.strip_suffix(".astrion.zip"))
            .is_some_and(token_name)
            && entry.file_type().map_err(io_error)?.is_file()
            && entry
                .metadata()
                .ok()
                .and_then(|data| data.modified().ok())
                .is_some_and(|modified| modified < cutoff)
            && let Err(error) = directory.remove_file(filename)
            && error.kind() != ErrorKind::NotFound
        {
            return Err(io_error(error));
        }
    }
    Ok(())
}
