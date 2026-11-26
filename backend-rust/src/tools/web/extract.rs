use std::{cell::RefCell, time::Duration};

use base64::{Engine, engine::general_purpose::STANDARD};
use html5ever::{
    tendril::StrTendril,
    tokenizer::{
        BufferQueue, CharacterTokens, EndTag, StartTag, TagToken, Token, TokenSink,
        TokenSinkResult, Tokenizer,
    },
};
use reqwest::{
    Client, Url,
    header::{ACCEPT, LOCATION},
};
use serde_json::{Value, json};

use super::{
    EXTRACT, citation, client, credential, http_url, matches_domain, response, response_json, text,
};

const MAX_CHARACTERS: usize = 80_000;

pub(super) async fn run(args: &Value, prefs: &Value) -> Result<Value, String> {
    let url = http_url(
        text(args, "url")
            .filter(|url| url.len() <= 8192)
            .ok_or("请输入网页地址")?,
    )?;
    let mut domains = vec!["github.com".to_owned()];
    domains.extend(
        prefs["webpage_direct_extract_domains"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .filter_map(domain),
    );
    let transport = client()?;
    let result = if prefs["webpage_direct_extract_enabled"] != false && allowed(&url, &domains) {
        direct(&transport, &url, &domains).await
    } else {
        None
    };
    let (content, method) = match result {
        Some(result) => result,
        None => {
            let provider = text(prefs, "webpage_extract_provider")
                .filter(|name| matches!(*name, "jina" | "tavily" | "exa" | "parallel"))
                .unwrap_or("jina");
            let key = credential(prefs, provider);
            let content =
                provider_content(&transport, provider, &url, key.as_deref(), args).await?;
            (content, provider)
        }
    };
    let content = content.trim();
    if content.is_empty() {
        return Err("网页提取服务未返回正文".into());
    }
    let count = content.chars().count();
    if count > MAX_CHARACTERS {
        return Err("网页正文超过 80000 字符".into());
    }
    let annotation = citation(&url, url.as_str(), content, "", EXTRACT);
    let summary = format!(
        "🌐 网页内容 ({count} 字符) [提取方式: {method}]\n📍 URL: {url}\n来源: [{}]\n\n{content}",
        annotation["id"].as_str().unwrap_or_default()
    );
    Ok(
        json!({"success":true,"url":url.as_str(),"content":content,"method":method,
        "summary":summary,"char_count":count,"citation_id":annotation["id"],"citations":[annotation]}),
    )
}

fn domain(raw: &str) -> Option<String> {
    let raw = raw.trim();
    let wrapped;
    let input = if raw.contains("://") {
        raw
    } else {
        wrapped = format!("https://{raw}");
        &wrapped
    };
    Some(
        http_url(input)
            .ok()?
            .host_str()?
            .trim_end_matches('.')
            .to_ascii_lowercase(),
    )
}

fn allowed(url: &Url, domains: &[String]) -> bool {
    url.host_str()
        .is_some_and(|host| domains.iter().any(|domain| matches_domain(host, domain)))
}

async fn direct(client: &Client, url: &Url, domains: &[String]) -> Option<(String, &'static str)> {
    if matches!(url.host_str(), Some("github.com" | "www.github.com"))
        && let Some(parts) = url.path_segments().map(|parts| parts.collect::<Vec<_>>())
        && let [owner, repo, "blob", branch, path @ ..] = parts.as_slice()
        && !path.is_empty()
    {
        let path = path.join("/");
        let cdn = format!("https://cdn.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}");
        if let Ok(bytes) = response(client.get(cdn)).await
            && !bytes.is_empty()
        {
            return Some((String::from_utf8_lossy(&bytes).into_owned(), "jsdelivr"));
        }
        let api = format!("https://api.github.com/repos/{owner}/{repo}/contents/{path}");
        if let Ok(body) = response_json(
            client
                .get(api)
                .query(&[("ref", branch)])
                .header(ACCEPT, "application/vnd.github+json"),
        )
        .await
            && body["encoding"] == "base64"
            && let Some(encoded) = text(&body, "content")
            && let Ok(bytes) = STANDARD.decode(encoded.replace(['\n', '\r'], ""))
            && !bytes.is_empty()
        {
            return Some((String::from_utf8_lossy(&bytes).into_owned(), "github_api"));
        }
    }
    let mut target = url.clone();
    for _ in 0..5 {
        let response = client
            .get(target.clone())
            .timeout(Duration::from_secs(30))
            .header(ACCEPT, "text/html,text/plain,application/xhtml+xml")
            .send()
            .await
            .ok()?;
        if response.status().is_redirection() {
            let location = response.headers().get(LOCATION)?.to_str().ok()?;
            let joined = target.join(location).ok()?;
            target = http_url(joined.as_str()).ok()?;
            if !allowed(&target, domains) {
                return None;
            }
            continue;
        }
        let html = response
            .headers()
            .get("content-type")
            .and_then(|value| value.to_str().ok())
            .is_some_and(|mime| {
                mime.starts_with("text/html") || mime.starts_with("application/xhtml+xml")
            });
        let bytes = super::response_body(response).await.ok()?;
        let source = String::from_utf8_lossy(&bytes);
        let content = if html || source.trim_start().starts_with('<') {
            html_text(&source)
        } else {
            source.into_owned()
        };
        if !content.trim().is_empty() {
            return Some((content, "direct"));
        }
        return None;
    }
    None
}

async fn provider_content(
    client: &Client,
    provider: &str,
    url: &Url,
    key: Option<&str>,
    args: &Value,
) -> Result<String, String> {
    if provider == "jina" {
        let mut request = client
            .get(format!("https://r.jina.ai/{url}"))
            .header(ACCEPT, "text/plain");
        if let Some(key) = key {
            request = request.bearer_auth(key);
        }
        return response(request)
            .await
            .map(|bytes| String::from_utf8_lossy(&bytes).into_owned());
    }
    let key = key.ok_or_else(|| format!("尚未配置 {provider} 网页提取凭证"))?;
    let url = url.as_str();
    let result = match provider {
        "exa" => response_json(client.post("https://api.exa.ai/contents").header("x-api-key",key)
            .json(&json!({"urls":[url],"text":{"maxCharacters":50000}}))).await?,
        "parallel" => response_json(client.post("https://api.parallel.ai/v1/extract").header("x-api-key",key)
            .json(&json!({"urls":[url],"advanced_settings":{"full_content":{"max_chars_per_result":50000}}}))).await?,
        _ => {
            let depth = text(args,"extract_depth").filter(|depth| matches!(*depth,"basic"|"advanced")).unwrap_or("basic");
            response_json(client.post("https://api.tavily.com/extract").bearer_auth(key)
                .json(&json!({"urls":[url],"extract_depth":depth,"include_images":false}))).await?
        }
    };
    for item in result["results"].as_array().into_iter().flatten() {
        let content = match provider {
            "exa" => text(item, "text").unwrap_or_default().to_owned(),
            "parallel" => text(item, "full_content")
                .map(str::to_owned)
                .or_else(|| {
                    item["excerpts"].as_array().map(|items| {
                        items
                            .iter()
                            .filter_map(Value::as_str)
                            .collect::<Vec<_>>()
                            .join("\n")
                    })
                })
                .unwrap_or_default(),
            _ => text(item, "raw_content").unwrap_or_default().to_owned(),
        };
        if !content.trim().is_empty() {
            return Ok(content);
        }
    }
    Err(format!("{provider} 网页提取未返回正文"))
}

#[derive(Default)]
struct Page {
    content: RefCell<String>,
    hidden: RefCell<Vec<String>>,
}

impl TokenSink for Page {
    type Handle = ();

    fn process_token(&self, token: Token, _: u64) -> TokenSinkResult<()> {
        match token {
            CharacterTokens(text) if self.hidden.borrow().is_empty() => {
                self.content.borrow_mut().push_str(&text)
            }
            TagToken(tag) => {
                let tag_name = tag.name.as_ref();
                let mut hidden = self.hidden.borrow_mut();
                if matches!(
                    tag_name,
                    "script" | "style" | "head" | "nav" | "noscript" | "template" | "svg"
                ) {
                    if tag.kind == StartTag && !tag.self_closing {
                        hidden.push(tag_name.into());
                    } else if tag.kind == EndTag
                        && let Some(position) = hidden.iter().rposition(|name| name == tag_name)
                    {
                        hidden.truncate(position);
                    }
                } else if hidden.is_empty()
                    && matches!(
                        tag_name,
                        "p" | "h1"
                            | "h2"
                            | "h3"
                            | "br"
                            | "li"
                            | "div"
                            | "article"
                            | "section"
                            | "blockquote"
                            | "tr"
                            | "pre"
                    )
                {
                    self.content.borrow_mut().push('\n');
                }
            }
            _ => (),
        }
        TokenSinkResult::Continue
    }
}

fn html_text(source: &str) -> String {
    let input = BufferQueue::default();
    input.push_back(StrTendril::from_slice(source));
    let tokenizer = Tokenizer::new(Page::default(), Default::default());
    let _ = tokenizer.feed(&input);
    tokenizer.end();
    tokenizer
        .sink
        .content
        .into_inner()
        .lines()
        .map(|line| line.split_whitespace().collect::<Vec<_>>().join(" "))
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}
