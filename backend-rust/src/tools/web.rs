//! Shared browser-independent search and webpage extraction for Agent and Chat.
mod extract;
mod search;

use std::{collections::{HashMap, HashSet}, path::Path, time::Duration};

use reqwest::{Client, RequestBuilder, Url};
use ring::digest::{SHA1_FOR_LEGACY_USE_ONLY, digest};
use serde_json::{Value, json};

use crate::web_assets::personalization::web_service_preferences;

pub(crate) const SEARCH: &str = "web_search";
pub(crate) const EXTRACT: &str = "extract_webpage";
const MAX_RESPONSE_BYTES: usize = 3 * 1024 * 1024;

pub(crate) fn supports(name: &str) -> bool {
    matches!(name, SEARCH | EXTRACT)
}

pub(crate) fn definitions() -> Vec<Value> {
    let text = |description: &str| json!({"type":"string","description":description});
    vec![
        definition(SEARCH, "搜索外部信息；结果含来源 ID，引用使用【cite:src_xxx】。", json!({
            "query":text("查询内容"),"max_results":{"type":"integer"},
            "topic":{"type":"string","enum":["general","news","finance"]},
            "time_range":text("day/week/month/year"),"days":{"type":"integer"},
            "start_date":text("YYYY-MM-DD"),"end_date":text("YYYY-MM-DD"),"country":text("国家"),
            "include_domains":{"type":"array","items":{"type":"string"}},
        }), "query"),
        definition(EXTRACT, "提取网页正文，保留来源引用；正文超过 80000 字符时拒绝。", json!({
            "url":text("要提取的 HTTP 或 HTTPS 网页地址"),
            "extract_depth":{"type":"string","enum":["basic","advanced"]},
        }), "url"),
    ]
}

fn definition(name: &str, description: &str, mut properties: Value, required: &str) -> Value {
    properties["intent"] = json!({"type":"string","description":"简短说明操作目的"});
    json!({"type":"function","function":{"name":name,"description":description,
        "parameters":{"type":"object","properties":properties,"required":[required]}
    }})
}

pub(crate) async fn execute(data: &Path, name: &str, arguments: &Value) -> Value {
    let prefs = match web_service_preferences(data) {
        Ok(prefs) => prefs,
        Err(_) => return failure("无法读取网页服务设置"),
    };
    let result = match name {
        SEARCH => search::run(arguments, &prefs).await,
        EXTRACT => extract::run(arguments, &prefs).await,
        _ => Err("未知网页工具".into()),
    };
    result.unwrap_or_else(|message| failure(&message))
}

fn text<'a>(value: &'a Value, key: &str) -> Option<&'a str> {
    value[key].as_str().map(str::trim).filter(|value| !value.is_empty())
}

fn failure(message: &str) -> Value {
    json!({"success":false,"error":message,"results":[]})
}

fn credential(prefs: &Value, provider: &str) -> Option<String> {
    let (key, env) = match provider {
        "tavily" => ("tavily_api_key", "AGENT_TAVILY_API_KEY"),
        "bocha" => ("bocha_api_key", "AGENT_BOCHA_API_KEY"),
        "exa" => ("exa_api_key", "AGENT_EXA_API_KEY"),
        "parallel" => ("parallel_api_key", "AGENT_PARALLEL_API_KEY"),
        "jina" => ("jina_api_key", "AGENT_JINA_API_KEY"),
        "searxng" => ("searxng_base_url", "AGENT_SEARXNG_BASE_URL"),
        _ => return None,
    };
    text(prefs, key).map(str::to_owned)
        .or_else(|| std::env::var(env).ok().map(|value| value.trim().to_owned()))
        .filter(|value| !value.is_empty() && value != "your-tavily-api-key")
}

fn http_url(raw: &str) -> Result<Url, String> {
    let mut url = Url::parse(raw).map_err(|_| "请输入有效的 HTTP 或 HTTPS 地址")?;
    if !matches!(url.scheme(), "http" | "https") || !url.has_host()
        || !url.username().is_empty() || url.password().is_some()
    {
        return Err("请输入有效的 HTTP 或 HTTPS 地址".into());
    }
    url.set_fragment(None);
    Ok(url)
}

fn client() -> Result<Client, String> {
    Client::builder().timeout(Duration::from_secs(45)).user_agent("Astrion/1.0")
        .redirect(reqwest::redirect::Policy::none()).build()
        .map_err(|_| "无法初始化网页服务".into())
}

async fn response(request: RequestBuilder) -> Result<Vec<u8>, String> {
    let response = request.send().await.map_err(|_| "网页服务暂时不可用")?;
    response_body(response).await
}

async fn response_body(mut response: reqwest::Response) -> Result<Vec<u8>, String> {
    if !response.status().is_success() {
        return Err(format!("网页服务返回 HTTP {}", response.status().as_u16()));
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| "网页服务响应中断")? {
        if chunk.len() > MAX_RESPONSE_BYTES.saturating_sub(bytes.len()) {
            return Err("网页服务响应超出大小限制".into());
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}

async fn response_json(request: RequestBuilder) -> Result<Value, String> {
    serde_json::from_slice(&response(request).await?).map_err(|_| "网页服务返回无效 JSON".into())
}

fn citation(url: &Url, title: &str, snippet: &str, time: &str, tool: &str) -> Value {
    let mut normalized = format!("{}{}", url.origin().ascii_serialization(), url.path().trim_end_matches('/'));
    if let Some(query) = url.query().filter(|query| !query.is_empty()) {
        normalized.push('?');
        normalized.push_str(query);
    }
    // Keep citation IDs compatible with archived Python conversations.
    let hash = digest(&SHA1_FOR_LEGACY_USE_ONLY, normalized.as_bytes());
    let id: String = hash.as_ref()[..5].iter().map(|byte| format!("{byte:02x}")).collect();
    let title = if title.is_empty() { url.as_str() } else { title };
    let domain = url.host_str().map(|host| host.strip_prefix("www.").unwrap_or(host));
    let mut value = json!({"id":format!("src_{id}"),"type":"url_citation","url":url.as_str(),
        "title":title,"domain":domain,"snippet":snippet.chars().take(300).collect::<String>(),"source_tool":tool});
    if !time.is_empty() { value["published_date"] = json!(time); }
    value
}

fn matches_domain(host: &str, domain: &str) -> bool {
    host == domain || host.strip_suffix(domain).is_some_and(|prefix| prefix.ends_with('.'))
}

/// Resolve only citations that were returned by an actual web tool call.
pub(crate) fn annotate(message: &mut Value, history: &[Value]) {
    let Some(text) = message["content"].as_str().filter(|text| text.contains("【cite:")) else { return; };
    let call_ids: HashSet<_> = history.iter().filter(|row| row["role"] == "assistant")
        .flat_map(|row| row["tool_calls"].as_array().into_iter().flatten())
        .filter(|call| call["function"]["name"].as_str().is_some_and(supports))
        .filter_map(|call| call["id"].as_str()).collect();
    let mut registry = HashMap::new();
    for result in history.iter().filter(|row| row["role"] == "tool"
        && row["tool_call_id"].as_str().is_some_and(|id| call_ids.contains(id)))
    {
        let Some(value) = result["content"].as_str().and_then(|text| serde_json::from_str::<Value>(text).ok()) else { continue; };
        for citation in value["citations"].as_array().into_iter().flatten() {
            if let Some(id) = citation["id"].as_str() {
                registry.entry(id.to_owned()).or_insert_with(|| citation.clone());
            }
        }
    }
    let mut seen = HashSet::new();
    let mut citations = Vec::new();
    let mut clean = String::new();
    let mut remaining = text;
    while let Some((before, marker)) = remaining.split_once("【cite:") {
        let Some((refs, after)) = marker.split_once('】') else { break; };
        clean.push_str(before);
        let valid: Vec<_> = refs.split([',','，']).map(str::trim).filter(|id| {
            let Some(citation) = registry.get(*id) else { return false; };
            if seen.insert(*id) {
                citations.push(citation);
            }
            true
        }).collect();
        if !valid.is_empty() {
            clean.push_str("【cite:");
            clean.push_str(&valid.join(","));
            clean.push('】');
        }
        remaining = after;
    }
    clean.push_str(remaining);
    message["content"] = json!(clean);
    message["metadata"]["citations"] = json!(citations);
}
