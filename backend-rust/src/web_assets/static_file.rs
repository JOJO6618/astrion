use super::{
    AssetRoot, Page, TerminalPolicy, no_store, options, public_path, read_asset, redirect, render,
    show,
};
use crate::web_auth::BrowserSession;
use axum::{
    Extension,
    extract::{Path as RoutePath, State},
    http::{HeaderMap, Method, StatusCode, header},
    response::{IntoResponse, Response},
};
use ring::digest::{SHA256, digest};
use std::{fmt::Write, io::Read, sync::Arc};
use time::{OffsetDateTime, format_description::well_known::Rfc2822};

pub(super) async fn serve(
    State(assets): State<Arc<AssetRoot>>,
    RoutePath(filename): RoutePath<String>,
    method: Method,
    session: Option<Extension<BrowserSession>>,
    policy: Option<Extension<TerminalPolicy>>,
    headers: HeaderMap,
) -> Response {
    if !public_path(&filename) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let lower_name = filename.to_ascii_lowercase();
    let admin_dashboard = lower_name.starts_with("admin_dashboard");
    if admin_dashboard && session.is_none() {
        return no_store(StatusCode::NOT_FOUND);
    }
    if method == Method::OPTIONS {
        return options().await;
    }
    match lower_name.as_str() {
        "index.html" => return show(Page::Main, assets, session, policy).await,
        "terminal.html" => return show(Page::Terminal, assets, session, policy).await,
        "admin_policy/index.html" => return show(Page::AdminPolicy, assets, session, policy).await,
        "custom_tools/index.html" => return show(Page::AdminTools, assets, session, policy).await,
        "login.html" => return redirect("/login"),
        "chrome.html" => return render(assets, "chrome.html").await,
        "browser-window.html" => return render(assets, "browser-window.html").await,
        "quick.html" => return render(assets, "quick.html").await,
        "custom_tools/guide.html" => return render(assets, "custom_tools/guide.html").await,
        _ => (),
    }
    read_asset(move || {
        let mut file = assets.file(&filename)?;
        let modified = file
            .metadata()?
            .modified()
            .ok()
            .map(|time| OffsetDateTime::from(time.into_std()));
        let mut contents = Vec::new();
        file.read_to_end(&mut contents)?;
        let content_type = content_type(&lower_name);
        if admin_dashboard || content_type.starts_with("text/html") {
            return Ok(no_store(([(header::CONTENT_TYPE, content_type)], contents)));
        }
        let etag = etag(&contents);
        let response_headers = [
            (header::CACHE_CONTROL, "no-cache"),
            (header::ETAG, etag.as_str()),
        ];
        let unchanged = if headers.contains_key(header::IF_NONE_MATCH) {
            matching_etag(&headers, &etag)
        } else {
            modified.is_some_and(|date| modified_since(&headers, date))
        };
        let mut response = if unchanged {
            (StatusCode::NOT_MODIFIED, response_headers).into_response()
        } else {
            (
                response_headers,
                [(header::CONTENT_TYPE, content_type)],
                contents,
            )
                .into_response()
        };
        if let Some(date) = modified.and_then(|date| date.format(&Rfc2822).ok()) {
            let date = date.replace("+0000", "GMT");
            response.headers_mut().insert(
                header::LAST_MODIFIED,
                date.parse().expect("valid HTTP date"),
            );
        }
        Ok(response)
    })
    .await
}

fn etag(contents: &[u8]) -> String {
    let mut tag = String::with_capacity(66);
    tag.push('"');
    for byte in digest(&SHA256, contents).as_ref() {
        write!(&mut tag, "{byte:02x}").expect("writing to a String");
    }
    tag.push('"');
    tag
}

fn matching_etag(headers: &HeaderMap, tag: &str) -> bool {
    headers
        .get_all(header::IF_NONE_MATCH)
        .iter()
        .filter_map(|value| value.to_str().ok())
        .flat_map(|value| value.split(','))
        .map(str::trim)
        .any(|value| value == "*" || value.strip_prefix("W/").unwrap_or(value) == tag)
}

fn modified_since(headers: &HeaderMap, modified: OffsetDateTime) -> bool {
    headers
        .get(header::IF_MODIFIED_SINCE)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| OffsetDateTime::parse(value, &Rfc2822).ok())
        .is_some_and(|since| modified.unix_timestamp() <= since.unix_timestamp())
}

fn content_type(name: &str) -> &'static str {
    match name.rsplit_once('.').map(|(_, suffix)| suffix) {
        Some("html" | "htm") => "text/html; charset=utf-8",
        Some("css") => "text/css; charset=utf-8",
        Some("js" | "mjs") => "text/javascript; charset=utf-8",
        Some("txt") => "text/plain; charset=utf-8",
        Some("json" | "map") => "application/json",
        Some("wasm") => "application/wasm",
        Some("svg") => "image/svg+xml",
        Some("ico") => "image/vnd.microsoft.icon",
        Some("gif") => "image/gif",
        Some("png") => "image/png",
        Some("jpg" | "jpeg") => "image/jpeg",
        Some("webp") => "image/webp",
        Some("avif") => "image/avif",
        Some("woff") => "font/woff",
        Some("woff2") => "font/woff2",
        Some("otf") => "font/otf",
        Some("ttf") => "font/ttf",
        Some("mp3") => "audio/mpeg",
        Some("ogg") => "audio/ogg",
        Some("wav") => "audio/wav",
        Some("mp4") => "video/mp4",
        Some("webm") => "video/webm",
        _ => "application/octet-stream",
    }
}
