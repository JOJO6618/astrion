use std::collections::{HashMap, HashSet};

use serde_json::{Value, json};
use uuid::Uuid;

use super::{LegacyChatTurn, LegacyError, storage_message, update_message_counts};

struct Node<'a> {
    index: usize,
    message: &'a Value,
    parent: Option<&'a str>,
    turn: Option<&'a str>,
}

struct Tree<'a> {
    nodes: HashMap<&'a str, Node<'a>>,
    turns: HashMap<&'a str, (usize, &'a Value)>,
    latest: Option<&'a str>,
    active: Option<&'a str>,
    revision: u64,
}

impl<'a> Tree<'a> {
    fn read(document: &'a Value) -> Result<Self, LegacyError> {
        let chat = &document["metadata"]["chat"];
        if chat["version"].as_u64() != Some(1) {
            return Err(LegacyError::invalid("Unsupported legacy Chat tree version"));
        }
        if !chat["commands"].is_object() {
            return Err(LegacyError::invalid("Invalid legacy Chat command receipts"));
        }
        let revision = chat["revision"]
            .as_u64()
            .ok_or_else(|| LegacyError::invalid("Invalid legacy Chat revision"))?;
        let turns = chat["turns"]
            .as_array()
            .ok_or_else(|| LegacyError::invalid("Invalid legacy Chat turns"))?;
        let mut tree = Self {
            nodes: HashMap::new(),
            turns: HashMap::with_capacity(turns.len()),
            latest: nullable_id(chat.get("latest_leaf_id"), "latest_leaf_id")?,
            active: nullable_id(chat.get("active_turn_id"), "active_turn_id")?,
            revision,
        };
        for (index, turn) in turns.iter().enumerate() {
            let id = required_id(&turn["id"], "turn ID")?;
            if tree.turns.insert(id, (index, turn)).is_some() {
                return Err(LegacyError::invalid("Duplicate legacy Chat turn identity"));
            }
        }
        let messages = document["messages"]
            .as_array()
            .expect("validated messages array");
        tree.nodes.reserve(messages.len());
        for (index, message) in messages.iter().enumerate() {
            let id = required_id(&message["message_id"], "message identity")?;
            let relation = &message["metadata"]["chat"];
            let parent = nullable_id(relation.get("parent_id"), "parent_id")?;
            let turn = relation
                .get("turn_id")
                .map(|id| nullable_id(Some(id), "turn_id"))
                .transpose()?
                .flatten();
            // Python stores nodes in append order, so every parent must already exist.
            if parent.is_some_and(|parent| !tree.nodes.contains_key(parent)) {
                return Err(LegacyError::invalid(
                    "Missing or non-topological legacy Chat parent",
                ));
            }
            if turn.is_some_and(|turn| !tree.turns.contains_key(turn)) {
                return Err(LegacyError::invalid("Unknown legacy Chat message turn"));
            }
            if tree
                .nodes
                .insert(
                    id,
                    Node {
                        index,
                        message,
                        parent,
                        turn,
                    },
                )
                .is_some()
            {
                return Err(LegacyError::invalid(
                    "Duplicate legacy Chat message identity",
                ));
            }
        }
        if let Some(latest) = tree.latest {
            tree.node(latest)?;
        }
        for (id, (_, turn)) in &tree.turns {
            let parent = required_id(&turn["parent_id"], "turn parent")?;
            if tree.node(parent)?.message["role"] != "user" {
                return Err(LegacyError::invalid(
                    "A legacy Chat turn must start at a user message",
                ));
            }
            if let Some(tip) = nullable_id(turn.get("tip_id"), "tip_id")?
                && tree.node(tip)?.turn != Some(id)
            {
                return Err(LegacyError::invalid(
                    "Legacy Chat turn tip belongs to another turn",
                ));
            }
        }
        if let Some(active) = tree.active
            && tree.turn(active)?.1["status"] != "running"
        {
            return Err(LegacyError::invalid(
                "The active legacy Chat turn must be running",
            ));
        }
        Ok(tree)
    }

    fn node(&self, id: &str) -> Result<&Node<'a>, LegacyError> {
        self.nodes
            .get(id)
            .ok_or_else(|| LegacyError::invalid("Unknown legacy Chat message"))
    }

    fn turn(&self, id: &str) -> Result<(usize, &'a Value), LegacyError> {
        self.turns
            .get(id)
            .copied()
            .ok_or_else(|| LegacyError::invalid("Unknown legacy Chat turn"))
    }

    fn path<'branch>(
        &'branch self,
        mut leaf: Option<&'branch str>,
    ) -> Result<Vec<Value>, LegacyError> {
        let mut path = Vec::new();
        while let Some(id) = leaf {
            let node = self.node(id)?;
            path.push(node.message.clone());
            leaf = node.parent;
        }
        path.reverse();
        Ok(path)
    }

    fn turn_tip(&self, id: &str) -> Result<&'a str, LegacyError> {
        let (_, turn) = self.turn(id)?;
        Ok(turn["tip_id"]
            .as_str()
            .or_else(|| turn["parent_id"].as_str())
            .expect("validated turn parent"))
    }

    fn next_revision(&self, steps: u64) -> Result<u64, LegacyError> {
        self.revision
            .checked_add(steps)
            .ok_or_else(|| LegacyError::invalid("Legacy Chat revision overflow"))
    }
}

pub(super) fn chat_path(
    document: &Value,
    leaf_id: Option<&str>,
) -> Result<Vec<Value>, LegacyError> {
    let tree = Tree::read(document)?;
    tree.path(leaf_id.or(tree.latest))
}

pub(super) fn chat_turn_path(document: &Value, turn_id: &str) -> Result<Vec<Value>, LegacyError> {
    let tree = Tree::read(document)?;
    tree.path(Some(tree.turn_tip(turn_id)?))
}

pub(super) fn siblings(document: &Value, message_id: &str) -> Result<Vec<String>, LegacyError> {
    let tree = Tree::read(document)?;
    let node = tree.node(message_id)?;
    let mut siblings: Vec<_> = tree
        .nodes
        .iter()
        .filter(|(_, candidate)| {
            candidate.parent == node.parent && candidate.message["role"] == node.message["role"]
        })
        .map(|(id, candidate)| (candidate.index, *id))
        .collect();
    siblings.sort_unstable();
    Ok(siblings.into_iter().map(|(_, id)| id.to_owned()).collect())
}

pub(super) fn revision(document: &Value) -> Result<u64, LegacyError> {
    Ok(Tree::read(document)?.revision)
}

pub(super) fn begin(
    document: &mut Value,
    selection: LegacyChatTurn<'_>,
    timestamp: &str,
) -> Result<String, LegacyError> {
    let tree = Tree::read(document)?;
    if tree.active.is_some() {
        return Err(LegacyError::conflict(
            "A legacy Chat generation is already active",
        ));
    }
    if document["metadata"]["chat"]["handoffs"]
        .as_object()
        .is_some_and(|handoffs| {
            handoffs
                .values()
                .any(|handoff| handoff["status"] == "preparing")
        })
    {
        return Err(LegacyError::conflict(
            "Wait for the current Agent handoff to finish",
        ));
    }
    let (parent, new_user) = match selection {
        LegacyChatTurn::Send { parent_id, message } => (parent_id, Some(message)),
        LegacyChatTurn::Regenerate { turn_id } => {
            (tree.turn(turn_id)?.1["parent_id"].as_str(), None)
        }
        LegacyChatTurn::EditAndSend {
            message_id,
            content,
        } => {
            let node = tree.node(message_id)?;
            if content.trim().is_empty() {
                return Err(LegacyError::invalid("Edit-and-send requires nonempty text"));
            }
            let mut message = node.message.clone();
            let fields = message.as_object_mut().expect("validated Chat node object");
            fields.shift_remove("message_id");
            fields.insert("timestamp".into(), timestamp.into());
            let metadata = fields
                .get_mut("metadata")
                .and_then(Value::as_object_mut)
                .expect("validated Chat metadata");
            for key in ["chat", "work_timer", "token_speed", "edited_at"] {
                metadata.shift_remove(key);
            }
            edit_text(&mut message, content);
            (node.parent, Some(message))
        }
    };
    if let Some(parent) = parent {
        tree.node(parent)?;
    }
    let revision = tree.next_revision(if new_user.is_some() { 2 } else { 1 })?;
    let mut identities = tree.nodes.keys().map(|id| (*id).to_owned()).collect();
    let user = new_user
        .map(|message| {
            if message["role"] != "user" {
                return Err(LegacyError::invalid("Expected a legacy Chat user message"));
            }
            prepare_node(message, parent, None, timestamp, &mut identities)
        })
        .transpose()?;
    let parent = match &user {
        Some(message) => message["message_id"]
            .as_str()
            .expect("prepared Chat message identity"),
        None => parent.expect("validated Chat turn parent"),
    }
    .to_owned();
    let id = loop {
        let candidate = format!("turn_{}", Uuid::new_v4().simple());
        if !tree.turns.contains_key(candidate.as_str()) {
            break candidate;
        }
    };
    if let Some(user) = user {
        document["messages"]
            .as_array_mut()
            .expect("validated messages array")
            .push(user);
    }
    let chat = &mut document["metadata"]["chat"];
    chat["turns"]
        .as_array_mut()
        .expect("validated Chat turns")
        .push(json!({
            "id":id, "parent_id":parent, "tip_id":null, "status":"running"
        }));
    chat["active_turn_id"] = id.clone().into();
    chat["latest_leaf_id"] = parent.into();
    chat["revision"] = revision.into();
    touch(document, timestamp);
    Ok(id)
}

pub(super) fn append(
    document: &mut Value,
    turn_id: &str,
    messages: Vec<Value>,
    timestamp: &str,
) -> Result<(), LegacyError> {
    let tree = Tree::read(document)?;
    let (index, turn) = tree.turn(turn_id)?;
    if tree.active != Some(turn_id) || turn["status"] != "running" {
        return Err(LegacyError::conflict(
            "Generation no longer owns this legacy Chat turn",
        ));
    }
    if messages.is_empty() {
        return Ok(());
    }
    let revision = tree.next_revision(messages.len() as u64)?;
    let mut parent = tree.turn_tip(turn_id)?.to_owned();
    let mut identities = tree.nodes.keys().map(|id| (*id).to_owned()).collect();
    let mut appended = Vec::with_capacity(messages.len());
    for message in messages {
        let message = prepare_node(
            message,
            Some(&parent),
            Some(turn_id),
            timestamp,
            &mut identities,
        )?;
        parent = message["message_id"]
            .as_str()
            .expect("prepared Chat message identity")
            .to_owned();
        appended.push(message);
    }
    document["messages"]
        .as_array_mut()
        .expect("validated messages array")
        .extend(appended);
    let chat = &mut document["metadata"]["chat"];
    chat["turns"][index]["tip_id"] = parent.clone().into();
    chat["latest_leaf_id"] = parent.into();
    chat["revision"] = revision.into();
    touch(document, timestamp);
    Ok(())
}

pub(super) fn finish(
    document: &mut Value,
    turn_id: &str,
    status: &str,
    timestamp: &str,
) -> Result<(), LegacyError> {
    if !matches!(status, "complete" | "stopped" | "failed") {
        return Err(LegacyError::invalid("Invalid legacy Chat turn status"));
    }
    let tree = Tree::read(document)?;
    let (index, turn) = tree.turn(turn_id)?;
    if turn["status"] == status {
        return Ok(());
    }
    if tree.active != Some(turn_id) {
        return Err(LegacyError::conflict(
            "Generation no longer owns this legacy Chat turn",
        ));
    }
    let revision = tree.next_revision(1)?;
    let chat = &mut document["metadata"]["chat"];
    chat["turns"][index]["status"] = status.into();
    chat["active_turn_id"] = Value::Null;
    chat["revision"] = revision.into();
    touch(document, timestamp);
    Ok(())
}

pub(super) fn edit(
    document: &mut Value,
    message_id: &str,
    content: &str,
    timestamp: &str,
) -> Result<(), LegacyError> {
    let tree = Tree::read(document)?;
    let node = tree.node(message_id)?;
    if !matches!(node.message["role"].as_str(), Some("user" | "assistant")) {
        return Err(LegacyError::invalid(
            "Legacy Chat tool messages cannot be edited",
        ));
    }
    if node.message["metadata"]["streaming"] == true {
        return Err(LegacyError::conflict(
            "Streaming legacy Chat content cannot be edited",
        ));
    }
    let index = node.index;
    let revision = tree.next_revision(1)?;
    let message = &mut document["messages"][index];
    edit_text(message, content);
    message["metadata"]["edited_at"] = timestamp.into();
    let chat = &mut document["metadata"]["chat"];
    if let Some(summaries) = chat["summaries"].as_array_mut() {
        for summary in summaries {
            if summary["message_ids"]
                .as_array()
                .is_some_and(|ids| ids.iter().any(|id| id == message_id))
            {
                summary["valid"] = false.into();
            }
        }
    }
    chat["revision"] = revision.into();
    touch(document, timestamp);
    Ok(())
}

fn edit_text(message: &mut Value, text: &str) {
    if let Some(parts) = message["content"].as_array_mut() {
        parts.retain(|part| part["type"] != "text");
        parts.insert(0, json!({"type":"text","text":text}));
    } else {
        message["content"] = text.into();
    }
}

fn prepare_node(
    message: Value,
    parent: Option<&str>,
    turn: Option<&str>,
    timestamp: &str,
    identities: &mut HashSet<String>,
) -> Result<Value, LegacyError> {
    if !matches!(
        message["role"].as_str(),
        Some("user" | "assistant" | "tool")
    ) {
        return Err(LegacyError::invalid("Unsupported legacy Chat message role"));
    }
    if !message["message_id"].is_null() {
        let id = required_id(&message["message_id"], "message identity")?;
        if identities.contains(id) {
            return Err(LegacyError::conflict(
                "Legacy Chat message identity already exists",
            ));
        }
    }
    if message
        .get("metadata")
        .is_some_and(|metadata| !metadata.is_object())
    {
        return Err(LegacyError::invalid(
            "Expected legacy Chat message metadata to be an object",
        ));
    }
    let mut stored = storage_message(message, identities, timestamp)?;
    stored["metadata"]["chat"] = json!({"parent_id":parent, "turn_id":turn});
    Ok(stored)
}

fn touch(document: &mut Value, timestamp: &str) {
    document["updated_at"] = timestamp.into();
    update_message_counts(document);
}

fn required_id<'a>(value: &'a Value, field: &str) -> Result<&'a str, LegacyError> {
    value
        .as_str()
        .filter(|id| !id.is_empty())
        .ok_or_else(|| LegacyError::invalid(format!("Invalid legacy Chat {field}")))
}

fn nullable_id<'a>(value: Option<&'a Value>, field: &str) -> Result<Option<&'a str>, LegacyError> {
    match value {
        Some(Value::Null) => Ok(None),
        Some(value) => required_id(value, field).map(Some),
        None => Err(LegacyError::invalid(format!("Missing legacy Chat {field}"))),
    }
}
