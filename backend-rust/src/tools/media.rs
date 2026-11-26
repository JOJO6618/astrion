//! Attach scoped local media to the model after the complete tool result batch.
use std::{borrow::Cow, collections::HashSet, path::Path};

use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};

use crate::{
    legacy::media::{BlobStore, legacy_mime},
    runtime::guidance::media::store_tool_media,
};

pub(crate) const IMAGE: &str = "view_image";
pub(crate) const VIDEO: &str = "view_video";
pub(crate) const READ: &str = "read_mediafile";

pub(crate) fn definition(name: &str) -> Value {
    let description = match name {
        IMAGE => "将本地图片附加到模型上下文。支持 PNG、JPEG、GIF、WebP、BMP。",
        VIDEO => "将本地视频附加到模型上下文。支持 MP4、MOV、MKV、AVI、WebM。",
        _ => "读取工作区图片或视频，并附加到模型上下文。",
    };
    json!({"type":"function","function":{"name":name,"description":description,
        "parameters":{"type":"object","properties":{"path":{"type":"string"},"intent":{"type":"string"}},
            "required":["path"]}}})
}

pub(crate) fn execute(
    data: &Path,
    workspace: &Path,
    execution: &Path,
    aliases: &Value,
    name: &str,
    path: &str,
    images: bool,
    videos: bool,
) -> Value {
    let allowed = |kind| match name {
        IMAGE => kind == "image" && images,
        VIDEO => kind == "video" && videos,
        READ => (kind == "image" && images) || (kind == "video" && videos),
        _ => false,
    };
    let kind = legacy_mime(path)
        .filter(|(_, mime)| *mime != "image/avif")
        .or_else(|| {
            match Path::new(path)
                .extension()?
                .to_str()?
                .to_ascii_lowercase()
                .as_str()
            {
                "mkv" => Some(("video", "video/x-matroska")),
                "avi" => Some(("video", "video/x-msvideo")),
                _ => None,
            }
        });
    let Some((kind, mime)) = kind.filter(|(kind, _)| allowed(*kind)) else {
        return json!({"success":false,"error":"文件格式不受支持，或当前模型无法读取该类媒体。"});
    };
    match store_tool_media(data, workspace, execution, aliases, path, kind, mime) {
        Ok(reference) => {
            json!({"success":true,"path":path,"size":reference["size"],"media_ref":reference,"message":"媒体已附加。"})
        }
        Err(error) => json!({"success":false,"error":error}),
    }
}

pub(crate) fn annotate(message: &mut Value, result: &Value) {
    if result["media_ref"].is_object() {
        message["media_refs"] = json!([result["media_ref"]]);
        message["metadata"]["media_refs"] = message["media_refs"].clone();
    }
}

pub(crate) fn model_messages<'a>(
    messages: &'a [Value],
    data: &Path,
    images: bool,
    videos: bool,
) -> Cow<'a, [Value]> {
    if !images && !videos {
        return Cow::Borrowed(messages);
    }
    let calls: HashSet<_> = messages
        .iter()
        .filter(|row| row["role"] == "assistant")
        .flat_map(|row| row["tool_calls"].as_array().into_iter().flatten())
        .filter(|call| {
            matches!(
                call["function"]["name"].as_str(),
                Some(IMAGE | VIDEO | READ)
            )
        })
        .filter_map(|call| call["id"].as_str())
        .collect();
    if calls.is_empty() {
        return Cow::Borrowed(messages);
    }
    let Some(store) = BlobStore::open(data) else {
        return Cow::Borrowed(messages);
    };
    let mut projected = Vec::with_capacity(messages.len());
    let mut pending = Vec::new();
    for message in messages {
        if message["role"] != "tool" {
            push_parts(&mut projected, &mut pending);
        } else if message["tool_call_id"]
            .as_str()
            .is_some_and(|id| calls.contains(id))
            && let Some(reference) = message["media_refs"]
                .as_array()
                .and_then(|refs| refs.first())
            && let Some(mime) = reference["mime_type"].as_str()
            && let Some(kind) = reference["kind"].as_str().filter(|kind| match *kind {
                "image" => images && mime.starts_with("image/"),
                "video" => videos && mime.starts_with("video/"),
                _ => false,
            })
            && let Some(bytes) = store.read(reference)
        {
            let key = format!("{kind}_url");
            pending.push(json!({"type":key, (key.clone()):{"url":format!("data:{mime};base64,{}", STANDARD.encode(bytes))}}));
        }
        projected.push(message.clone());
    }
    push_parts(&mut projected, &mut pending);
    Cow::Owned(projected)
}

fn push_parts(messages: &mut Vec<Value>, parts: &mut Vec<Value>) {
    if parts.is_empty() {
        return;
    }
    let mut content = vec![json!({"type":"text","text":"上一批工具调用读取的本地媒体。"})];
    content.append(parts);
    messages.push(json!({"role":"user","content":content}));
}
