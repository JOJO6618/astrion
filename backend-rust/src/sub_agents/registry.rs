use std::{
    collections::{BTreeMap, HashMap},
    fs::{self, OpenOptions},
    io::{self, ErrorKind},
    path::{Path, PathBuf},
    sync::{Mutex, MutexGuard},
    time::Duration,
};

use rusqlite::{Connection, OpenFlags, OptionalExtension, TransactionBehavior, params};
use serde_json::{Map, Value, json};

use super::{
    activity::{self, ActivityPage},
    normalize_conversation, valid_task_id,
};

const DATABASE: &str = "sub_agents.sqlite3";

// Execution holds this through each read-modify-write of the child registry.
// Migration stages its files before taking the same lock for publication.
pub(super) fn mutation_guard() -> io::Result<MutexGuard<'static, ()>> {
    static MUTATION: Mutex<()> = Mutex::new(());
    MUTATION
        .lock()
        .map_err(|_| io::Error::other("Child registry is unavailable"))
}

pub(super) type Record = Map<String, Value>;
pub(super) type Records = BTreeMap<String, Record>;
type ByConversation = HashMap<String, Records>;

pub(super) struct Registry {
    data_dir: PathBuf,
}

impl Registry {
    pub fn new(data_dir: PathBuf) -> Self {
        Self { data_dir }
    }

    pub(super) fn data_dir(&self) -> &Path {
        &self.data_dir
    }

    pub(super) fn connect(&self, write: bool) -> io::Result<Option<Connection>> {
        let root = match self.data_dir.canonicalize() {
            Ok(root) => root,
            Err(error) if error.kind() == ErrorKind::NotFound && !write => return Ok(None),
            Err(error) => return Err(error),
        };
        let path = root.join(DATABASE);
        if write {
            let mut options = OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            match options.open(&path) {
                Ok(_) => (),
                Err(error) if error.kind() == ErrorKind::AlreadyExists => (),
                Err(error) => return Err(error),
            }
        }
        match fs::symlink_metadata(&path) {
            Ok(metadata) if metadata.is_file() => (),
            Ok(_) => return Err(ErrorKind::PermissionDenied.into()),
            Err(error) if error.kind() == ErrorKind::NotFound && !write => return Ok(None),
            Err(error) => return Err(error),
        }
        let access = if write {
            OpenFlags::SQLITE_OPEN_READ_WRITE
        } else {
            OpenFlags::SQLITE_OPEN_READ_ONLY
        };
        let mut db = Connection::open_with_flags(path, access | OpenFlags::SQLITE_OPEN_NOFOLLOW)
            .map_err(sql)?;
        db.busy_timeout(Duration::from_secs(15)).map_err(sql)?;
        let version = db
            .query_row("PRAGMA user_version", [], |row| row.get::<_, u64>(0))
            .map_err(sql)?;
        if version == 0 && write {
            db.execute_batch("PRAGMA journal_mode=WAL;").map_err(sql)?;
            let tx = db
                .transaction_with_behavior(TransactionBehavior::Immediate)
                .map_err(sql)?;
            if tx
                .query_row("PRAGMA user_version", [], |row| row.get::<_, u64>(0))
                .map_err(sql)?
                == 0
            {
                tx.execute_batch(include_str!("schema.sql")).map_err(sql)?;
            }
            tx.commit().map_err(sql)?;
        } else if version == 0 {
            return Ok(None);
        } else if version != 1 {
            return Err(io::Error::new(
                ErrorKind::InvalidData,
                format!("Unsupported child database version {version}"),
            ));
        }
        if write {
            db.execute_batch("PRAGMA foreign_keys=ON; PRAGMA synchronous=FULL;")
                .map_err(sql)?;
        }
        Ok(Some(db))
    }

    pub fn snapshot(&self, parent: &str) -> io::Result<ByConversation> {
        let mut parents = ByConversation::new();
        let Some(db) = self.connect(false)? else {
            return Ok(parents);
        };
        let mut statement = db
            .prepare("SELECT id, record FROM child_task WHERE parent_id = ? ORDER BY id")
            .map_err(sql)?;
        let rows = statement
            .query_map([parent], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })
            .map_err(sql)?;
        for row in rows {
            let (id, record) = row.map_err(sql)?;
            parents
                .entry(parent.to_owned())
                .or_default()
                .insert(id, serde_json::from_str(&record)?);
        }
        Ok(parents)
    }

    pub(super) fn document(&self) -> io::Result<Value> {
        let mut document = json!({"version":1,"tasks":{},"states":{}});
        let Some(mut db) = self.connect(false)? else {
            return Ok(document);
        };
        let tx = db.transaction().map_err(sql)?;
        for (key, query) in [
            ("tasks", "SELECT id, record FROM child_task"),
            ("states", "SELECT parent_id, state FROM child_state"),
        ] {
            let mut statement = tx.prepare(query).map_err(sql)?;
            let rows = statement
                .query_map([], |row| {
                    Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
                })
                .map_err(sql)?;
            for row in rows {
                let (id, value) = row.map_err(sql)?;
                document[key][id] = serde_json::from_str(&value)?;
            }
        }
        tx.commit().map_err(sql)?;
        Ok(document)
    }

    pub(super) fn store(&self, document: &Value) -> io::Result<()> {
        self.store_with_context(document, None)
    }

    pub(super) fn store_with_context(
        &self,
        document: &Value,
        context: Option<&Value>,
    ) -> io::Result<()> {
        self.store_checkpoint(document, context, None)
    }

    pub(super) fn store_checkpoint(
        &self,
        document: &Value,
        context: Option<&Value>,
        output: Option<(&Value, &Value)>,
    ) -> io::Result<()> {
        let mut db = self.connect(true)?.ok_or(ErrorKind::NotFound)?;
        let tx = db
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(sql)?;
        let tasks = document["tasks"]
            .as_object()
            .ok_or(ErrorKind::InvalidData)?;
        let states = document["states"]
            .as_object()
            .ok_or(ErrorKind::InvalidData)?;
        for (id, record) in tasks {
            let parent = checked_parent(id, record).ok_or(ErrorKind::InvalidData)?;
            tx.execute(
                "INSERT INTO child_task(id, parent_id, record) VALUES(?, ?, ?)
                 ON CONFLICT(id) DO UPDATE SET record=excluded.record
                 WHERE child_task.parent_id=excluded.parent_id AND child_task.record<>excluded.record",
                params![id, parent, record.to_string()],
            ).map_err(sql)?;
        }
        for (parent, state) in states {
            tx.execute(
                "INSERT INTO child_state VALUES(?, ?)
                 ON CONFLICT(parent_id) DO UPDATE SET state=excluded.state
                 WHERE child_state.state<>excluded.state",
                params![parent, state.to_string()],
            )
            .map_err(sql)?;
        }
        if let Some(context) = context {
            write_context(&tx, context)?;
            if let Some((event, output)) = output {
                let id = context["task_id"].as_str().ok_or(ErrorKind::InvalidData)?;
                activity::append(&tx, id, event)?;
                tx.execute(
                    "UPDATE child_task SET output=? WHERE id=?",
                    params![output.to_string(), id],
                )
                .map_err(sql)?;
            }
        }
        tx.commit().map_err(sql)
    }

    pub(super) fn context(&self, task_id: &str) -> io::Result<Option<Value>> {
        self.task_value(task_id, "SELECT context FROM child_task WHERE id=?")
    }

    pub(super) fn stats(&self, task_id: &str) -> io::Result<Option<Value>> {
        self.task_value(task_id, "SELECT stats FROM child_task WHERE id=?")
    }

    fn task_value(&self, task_id: &str, query: &str) -> io::Result<Option<Value>> {
        check_id(task_id)?;
        self.connect(false)?
            .map(|db| {
                db.query_row(query, [task_id], |row| row.get::<_, Option<String>>(0))
                    .optional()
                    .map_err(sql)?
                    .flatten()
                    .map(|value| serde_json::from_str(&value).map_err(io::Error::from))
                    .transpose()
            })
            .transpose()
            .map(Option::flatten)
    }

    pub(super) fn save_context(&self, context: &Value) -> io::Result<()> {
        let db = self.connect(true)?.ok_or(ErrorKind::NotFound)?;
        write_context(&db, context)
    }

    pub(super) fn append_activity(&self, task_id: &str, event: &Value) -> io::Result<()> {
        check_id(task_id)?;
        let mut db = self.connect(true)?.ok_or(ErrorKind::NotFound)?;
        let tx = db
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(sql)?;
        activity::append(&tx, task_id, event)?;
        tx.commit().map_err(sql)
    }

    pub(super) fn activity(
        &self,
        task_id: &str,
        offset: Option<u64>,
        before: Option<u64>,
        limit: usize,
    ) -> io::Result<ActivityPage> {
        check_id(task_id)?;
        let Some(mut db) = self.connect(false)? else {
            return Ok(activity::empty(offset, before));
        };
        let tx = db.transaction().map_err(sql)?;
        let page = activity::read_database_page(&tx, task_id, offset, before, limit)?;
        tx.commit().map_err(sql)?;
        Ok(page)
    }
}

pub(super) fn sql(error: rusqlite::Error) -> io::Error {
    io::Error::other(error)
}

pub(super) fn checked_parent(id: &str, record: &Value) -> Option<String> {
    (valid_task_id(id) && record["task_id"] == id)
        .then(|| normalize_conversation(record["conversation_id"].as_str()?))?
}

fn check_id(id: &str) -> io::Result<()> {
    if valid_task_id(id) {
        Ok(())
    } else {
        Err(ErrorKind::InvalidInput.into())
    }
}

fn write_context(db: &Connection, context: &Value) -> io::Result<()> {
    let id = context["task_id"].as_str().ok_or(ErrorKind::InvalidData)?;
    check_id(id)?;
    let stats = context["stats"]
        .as_object()
        .map(|stats| Value::Object(stats.clone()).to_string());
    let changed = db
        .execute(
            "UPDATE child_task SET context=?, stats=COALESCE(?, stats) WHERE id=?",
            params![context.to_string(), stats, id],
        )
        .map_err(sql)?;
    if changed != 1 {
        return Err(ErrorKind::NotFound.into());
    }
    Ok(())
}
