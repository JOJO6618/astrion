use std::{io, time::Duration};

use axum::{
    body::{Body, to_bytes},
    extract::Request,
    http::{HeaderMap, HeaderName, HeaderValue, Method, StatusCode, Uri, header},
    response::Response,
};
use reqwest::{Client, Url};

use super::routes::Failure;

const MAX_BYTES: usize = 50 * 1024 * 1024;
const HOP_HEADERS: &[&str] = &[
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
];

pub(super) fn client() -> io::Result<Client> {
    Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(io::Error::other)
}

pub(super) fn file_response(
    uri: &Uri,
    (mime, mut body): (String, Vec<u8>),
    isolated: bool,
) -> Response {
    if mime == "text/html" {
        let directory = uri
            .path()
            .rsplit_once('/')
            .map_or("", |(directory, _)| directory);
        body = insert_base(
            String::from_utf8_lossy(&body).into_owned(),
            &format!("{directory}/"),
        )
        .into_bytes();
    }
    let mut response = Response::new(Body::from(body));
    let mime = if mime.starts_with("text/") {
        format!("{mime}; charset=utf-8")
    } else {
        mime
    };
    if let Ok(value) = HeaderValue::from_str(&mime) {
        response.headers_mut().insert(header::CONTENT_TYPE, value);
    }
    preview_headers(response.headers_mut(), isolated);
    response
}

pub(super) async fn proxy(
    client: &Client,
    port: u16,
    request: Request,
    isolated: bool,
) -> Result<Response, Failure> {
    let (parts, body) = request.into_parts();
    let (route, tail) = parts
        .uri
        .path()
        .split_once("/proxy/")
        .ok_or_else(invalid_path)?;
    let (port_segment, suffix) = tail.split_once('/').ok_or_else(invalid_path)?;
    let prefix = format!("{route}/proxy/{port_segment}");
    let mut target =
        Url::parse(&format!("http://127.0.0.1:{port}/{suffix}")).map_err(|_| invalid_path())?;
    target.set_query(parts.uri.query());
    let headers = request_headers(&parts.headers);
    let is_head = parts.method == Method::HEAD;
    let mut upstream = client
        .request(parts.method.clone(), target)
        .headers(headers)
        .header(header::ACCEPT_ENCODING, "identity");
    if parts.method != Method::GET {
        let bytes = to_bytes(body, MAX_BYTES).await.map_err(|_| {
            Failure::message(StatusCode::PAYLOAD_TOO_LARGE, "预览代理请求体不可用或过大")
        })?;
        upstream = upstream.body(bytes);
    }
    let mut upstream = upstream
        .send()
        .await
        .map_err(|error| upstream_error(error, port))?;
    let status = upstream.status();
    let mut headers = response_headers(upstream.headers(), &prefix, port);
    let html = headers
        .get(header::CONTENT_TYPE)
        .and_then(|mime| mime.to_str().ok())
        .is_some_and(|mime| {
            mime.split(';')
                .next()
                .is_some_and(|mime| mime.trim().eq_ignore_ascii_case("text/html"))
        });
    let identity = headers
        .get(header::CONTENT_ENCODING)
        .is_none_or(|encoding| encoding.as_bytes().eq_ignore_ascii_case(b"identity"));
    let mut content = Vec::new();
    if !is_head {
        if upstream
            .content_length()
            .is_some_and(|size| size > MAX_BYTES as u64)
        {
            return Err(oversize());
        }
        while let Some(bytes) = upstream
            .chunk()
            .await
            .map_err(|error| upstream_error(error, port))?
        {
            if bytes.len() > MAX_BYTES - content.len() {
                return Err(oversize());
            }
            content.extend_from_slice(&bytes);
        }
        if html && identity && !content.is_empty() {
            let text = String::from_utf8_lossy(&content);
            content = rewrite_html(&text, &prefix).into_bytes();
            headers.insert(
                header::CONTENT_TYPE,
                HeaderValue::from_static("text/html; charset=utf-8"),
            );
        }
    }
    preview_headers(&mut headers, isolated);
    let mut response = Response::new(Body::from(content));
    *response.status_mut() = status;
    *response.headers_mut() = headers;
    Ok(response)
}

fn request_headers(source: &HeaderMap) -> HeaderMap {
    let mut output = HeaderMap::new();
    for (name, value) in source {
        let key = name.as_str();
        if !hop_header(source, name)
            && !key.starts_with("x-astrion-")
            && !matches!(
                key,
                "host"
                    | "cookie"
                    | "authorization"
                    | "accept-encoding"
                    | "referer"
                    | "x-csrf-token"
            )
        {
            output.append(name.clone(), value.clone());
        }
    }
    output
}

fn response_headers(source: &HeaderMap, prefix: &str, port: u16) -> HeaderMap {
    let mut output = HeaderMap::new();
    for (name, value) in source {
        let key = name.as_str();
        if hop_header(source, name)
            || key.starts_with("access-control-")
            || matches!(
                key,
                "set-cookie"
                    | "set-cookie2"
                    | "x-frame-options"
                    | "content-security-policy"
                    | "content-security-policy-report-only"
                    | "service-worker-allowed"
                    | "refresh"
                    | "clear-site-data"
                    | "strict-transport-security"
                    | "alt-svc"
            )
        {
            continue;
        }
        if name == header::LOCATION {
            if let Some(location) = value
                .to_str()
                .ok()
                .and_then(|value| rewrite_location(value, prefix, port))
                .and_then(|value| HeaderValue::from_str(&value).ok())
            {
                output.append(name.clone(), location);
            }
        } else {
            output.append(name.clone(), value.clone());
        }
    }
    output
}

fn hop_header(headers: &HeaderMap, name: &HeaderName) -> bool {
    HOP_HEADERS.contains(&name.as_str())
        || headers
            .get_all(header::CONNECTION)
            .iter()
            .filter_map(|value| value.to_str().ok())
            .flat_map(|value| value.split(','))
            .any(|value| value.trim().eq_ignore_ascii_case(name.as_str()))
}

fn preview_headers(headers: &mut HeaderMap, isolated: bool) {
    headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    headers.insert(
        header::X_CONTENT_TYPE_OPTIONS,
        HeaderValue::from_static("nosniff"),
    );
    if isolated {
        headers.insert(
            header::REFERRER_POLICY,
            HeaderValue::from_static("no-referrer"),
        );
    } else {
        // A remotely accessed Host cannot reach its loopback origin from the
        // browser. Keep the compatibility routes, with an opaque document origin.
        headers.insert(
            header::CONTENT_SECURITY_POLICY,
            HeaderValue::from_static("sandbox allow-scripts allow-forms"),
        );
        headers.insert(
            header::REFERRER_POLICY,
            HeaderValue::from_static("same-origin"),
        );
    }
}

fn rewrite_location(location: &str, prefix: &str, port: u16) -> Option<String> {
    let relative_authority = location.starts_with("//");
    if location.starts_with('/') && !relative_authority {
        if location == prefix
            || location
                .strip_prefix(prefix)
                .is_some_and(|part| part.starts_with(['/', '?', '#']))
        {
            return Some(location.to_owned());
        }
        return Some(format!("{prefix}{location}"));
    }
    let parsed = if relative_authority {
        Url::parse(&format!("http:{location}"))
    } else {
        Url::parse(location)
    };
    if let Ok(url) = parsed
        && matches!(url.scheme(), "http" | "https")
        && matches!(
            url.host_str(),
            Some("127.0.0.1" | "0.0.0.0" | "localhost" | "[::1]")
        )
    {
        if url.port_or_known_default() != Some(port) {
            return None;
        }
        let mut rewritten = format!("{prefix}{}", url.path());
        if let Some(query) = url.query() {
            rewritten.push('?');
            rewritten.push_str(query);
        }
        if let Some(fragment) = url.fragment() {
            rewritten.push('#');
            rewritten.push_str(fragment);
        }
        return Some(rewritten);
    }
    Some(location.to_owned())
}

fn insert_base(mut html: String, base: &str) -> String {
    let base = base
        .replace('&', "&amp;")
        .replace('"', "&quot;")
        .replace('<', "&lt;")
        .replace('>', "&gt;");
    let lower = html.to_ascii_lowercase();
    let offset = lower
        .match_indices("<head")
        .find_map(|(index, _)| {
            let tail = &lower[index + "<head".len()..];
            tail.as_bytes()
                .first()
                .filter(|byte| **byte == b'>' || byte.is_ascii_whitespace())?;
            tail.find('>').map(|end| index + "<head".len() + end + 1)
        })
        .unwrap_or(0);
    html.insert_str(offset, &format!("<base href=\"{base}\">"));
    html
}

fn rewrite_html(html: &str, prefix: &str) -> String {
    let mut output = String::with_capacity(html.len());
    let mut begin = 0;
    let original = html.as_bytes();
    let lower = html.to_ascii_lowercase();
    for (index, byte) in lower.bytes().enumerate() {
        if !matches!(byte, b's' | b'h' | b'a')
            || index == 0
            || !original[index - 1].is_ascii_whitespace()
        {
            continue;
        }
        let Some(name) = ["src", "href", "action"]
            .into_iter()
            .find(|name| lower.as_bytes()[index..].starts_with(name.as_bytes()))
        else {
            continue;
        };
        let tail = html[index + name.len()..].trim_ascii_start();
        let Some(value) = tail.strip_prefix('=').map(str::trim_ascii_start) else {
            continue;
        };
        if !matches!(value.as_bytes(), [b'\'' | b'"', b'/', ..])
            || value.as_bytes().get(2) == Some(&b'/')
        {
            continue;
        }
        let offset = html.len() - value.len() + 1;
        output.push_str(&html[begin..offset]);
        output.push_str(prefix);
        begin = offset;
    }
    output.push_str(&html[begin..]);
    insert_base(output, &format!("{prefix}/"))
}

fn invalid_path() -> Failure {
    Failure::message(StatusCode::NOT_FOUND, "Invalid preview proxy path")
}

fn oversize() -> Failure {
    Failure::message(StatusCode::BAD_GATEWAY, "响应体过大，已拒绝代理")
}

fn upstream_error(error: reqwest::Error, port: u16) -> Failure {
    let message = if error.is_connect() {
        format!("无法连接到服务器（端口 {port}），它可能已停止运行")
    } else {
        let detail: String = error.without_url().to_string().chars().take(200).collect();
        format!("代理请求失败：{detail}")
    };
    Failure::message(StatusCode::BAD_GATEWAY, message)
}
