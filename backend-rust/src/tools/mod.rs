pub mod read_file;
pub mod write_file;
pub(crate) mod web;

use crate::knowledge::{self, Knowledge, KnowledgeAccess};
use serde_json::{Value, json};
use std::{io, path::Path, sync::Arc};

/// Capabilities are created by trusted startup code, never from model arguments.
#[derive(Clone, Default)]
pub struct ToolSet {
    reader: Option<Arc<read_file::ReadFile>>,
    knowledge: Option<Arc<Knowledge>>,
    writer: Option<Arc<write_file::WorkspaceFileWriter>>,
}

impl ToolSet {
    pub fn for_workspace(root: &Path, data: &Path) -> io::Result<Self> {
        let reader = Arc::new(read_file::ReadFile::open(root, data)?);
        let writer = Arc::new(write_file::WorkspaceFileWriter::new(reader.clone()));
        Ok(Self {
            reader: Some(reader),
            knowledge: Some(Arc::new(Knowledge::open(root, data)?)),
            writer: Some(writer),
        })
    }

    pub(crate) fn with_execution_root(&self, root: &Path, data: &Path) -> io::Result<Self> {
        let reader = Arc::new(read_file::ReadFile::open(root, data)?);
        let writer = Arc::new(write_file::WorkspaceFileWriter::new(reader.clone()));
        Ok(Self { reader: Some(reader), writer: Some(writer), knowledge: self.knowledge.clone() })
    }

    pub(crate) fn matches_workspace(&self, path: &Path) -> bool {
        self.reader.as_ref().is_none_or(|reader| reader.0.matches_root(path))
    }

    pub(crate) fn writer(&self) -> Option<Arc<write_file::WorkspaceFileWriter>> {
        self.writer.clone()
    }

    pub fn schemas(&self) -> Vec<Value> {
        let mut tools = vec![json!({"type":"function", "function": {
            "name":"astrion_echo", "description":"Returns the supplied text unchanged.",
            "parameters":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"],"additionalProperties":false}
        }})];
        if self.reader.is_some() {
            tools.push(read_file::schema());
        }
        if self.knowledge.is_some() {
            tools.extend(knowledge::definitions(KnowledgeAccess::ReadOnly));
        }
        tools
    }

    pub fn validate(&self, name: &str, args: &Value) -> Result<(), String> {
        let Some(object) = args.as_object() else {
            return Err("Tool arguments must be an object".into());
        };
        match name {
            "astrion_echo" if object.len() == 1 && args["text"].is_string() => Ok(()),
            "astrion_echo" => {
                Err("astrion_echo requires exactly one string argument named text".into())
            }
            read_file::NAME if self.reader.is_some() => Ok(()),
            name if self.knowledge.is_some() && KnowledgeAccess::ReadOnly.allows(name) => Ok(()),
            _ => Err(format!("Unknown tool: {name}")),
        }
    }

    pub async fn execute(&self, name: &str, args: Value) -> Result<Value, String> {
        self.validate(name, &args)?;
        match name {
            "astrion_echo" => Ok(args["text"].clone()),
            read_file::NAME => {
                let reader = self.reader.clone().ok_or("File reading unavailable")?;
                tokio::task::spawn_blocking(move || reader.execute(&args))
                    .await
                    .map_err(|e| e.to_string())
            }
            name if KnowledgeAccess::ReadOnly.allows(name) => {
                let knowledge = self.knowledge.clone().ok_or("Knowledge unavailable")?;
                let name = name.to_owned();
                tokio::task::spawn_blocking(move || knowledge.execute_read_only(&name, &args))
                    .await
                    .map_err(|e| e.to_string())?
                    .ok_or_else(|| "Unknown knowledge tool".to_owned())
            }
            _ => Err(format!("Unknown tool: {name}")),
        }
    }
}
