//! External image analysis used by a text-only Agent model.
use std::path::PathBuf;

use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};

use crate::{legacy::media::legacy_mime, runtime::guidance::media::read_tool_media};

pub(crate) const NAME: &str = "vlm_analyze";

pub(crate) fn definition() -> Value {
    json!({"type":"function","function":{"name":NAME,"description":"用配置的视觉模型阅读本地图片中的文字、表格和布局。",
        "parameters":{"type":"object","properties":{"path":{"type":"string"},"prompt":{"type":"string"},
            "intent":{"type":"string"}},"required":["path","prompt"]}}})
}

pub(crate) async fn execute(
    data: PathBuf,
    workspace: PathBuf,
    execution: PathBuf,
    aliases: Value,
    path: String,
    prompt: &Value,
) -> Value {
    let Some(prompt) = prompt
        .as_str()
        .map(str::trim)
        .filter(|prompt| !prompt.is_empty())
    else {
        return failed("请提供图片分析提示。");
    };
    let Some((endpoint, key, model)) = configuration() else {
        return failed("VLM 配置缺失，请设置 OCR_API_BASE_URL、OCR_API_KEY 和 OCR_MODEL_ID。");
    };
    let mime = legacy_mime(&path)
        .filter(|(kind, _)| *kind == "image")
        .map(|(_, mime)| mime);
    let warning = mime.is_none();
    let mime = mime.unwrap_or("image/jpeg");
    let encoded = tokio::task::spawn_blocking(move || {
        let (bytes, mime) = read_tool_media(
            &data, &workspace, &execution, &aliases, &path, "image", mime,
        )?;
        Ok::<_, String>(format!("data:{mime};base64,{}", STANDARD.encode(bytes)))
    })
    .await;
    let image = match encoded {
        Ok(Ok(value)) => value,
        Ok(Err(error)) => return failed(&error),
        Err(_) => return failed("读取图片中断。"),
    };
    let max = std::env::var("OCR_MAX_TOKENS")
        .ok()
        .and_then(|value| value.parse::<u32>().ok())
        .filter(|tokens| *tokens > 0)
        .unwrap_or(4096);
    let request = json!({"model":model,"messages":[{"role":"user","content":[
        {"type":"image_url","image_url":{"url":image}},{"type":"text","text":prompt}]}],
        "max_tokens":max,"temperature":0});
    let client = match super::web::client() {
        Ok(client) => client,
        Err(_) => return failed("无法初始化视觉模型连接。"),
    };
    match super::web::response_json(client.post(endpoint).bearer_auth(key).json(&request)).await {
        Ok(response) => {
            let warnings: Vec<_> = warning
                .then_some("未知图片格式，按 JPEG 提交。")
                .into_iter()
                .collect();
            json!({"success":true,"content":response["choices"][0]["message"]["content"].as_str().unwrap_or_default(),"warnings":warnings})
        }
        Err(error) => failed(&error),
    }
}

fn configuration() -> Option<(reqwest::Url, String, String)> {
    let read = |name| {
        std::env::var(name)
            .ok()
            .map(|value| value.trim().to_owned())
            .filter(|value| !value.is_empty())
    };
    let base = read("OCR_API_BASE_URL")?;
    let mut endpoint = super::web::http_url(&base).ok()?;
    endpoint.set_query(None);
    let path = endpoint.path().trim_end_matches('/');
    let endpoint_path = if path.ends_with("/v1") {
        format!("{path}/chat/completions")
    } else {
        format!("{path}/v1/chat/completions")
    };
    endpoint.set_path(&endpoint_path);
    Some((endpoint, read("OCR_API_KEY")?, read("OCR_MODEL_ID")?))
}

fn failed(error: &str) -> Value {
    json!({"success":false,"error":error,"warnings":[]})
}
