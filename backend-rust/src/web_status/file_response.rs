use super::{Result, WebError};
use axum::{
    body::{Body, Bytes},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
};
use std::{fs::File, io::SeekFrom, time::SystemTime};
use tokio::io::{AsyncReadExt, AsyncSeekExt};
use tokio_util::io::ReaderStream;

pub(super) enum Payload {
    File(File),
    Bytes(Bytes),
}

pub(super) struct Representation {
    pub payload: Payload,
    pub size: u64,
    pub name: String,
    pub mime: String,
    pub attachment: bool,
    pub modified: Option<SystemTime>,
    pub tag: Option<String>,
}

fn bytes_range(value: &str, size: u64) -> Option<(u64, u64)> {
    let (unit, spec) = value.split_once('=')?;
    if !unit.eq_ignore_ascii_case("bytes") || size == 0 {
        return None;
    }
    let (first, last) = spec.trim().split_once('-')?;
    let number = |s: &str| {
        s.bytes()
            .all(|b| b.is_ascii_digit())
            .then(|| s.parse::<u64>().ok())
            .flatten()
    };
    if first.is_empty() {
        let count = number(last)?;
        return (count > 0).then_some((size.saturating_sub(count), size));
    }
    let start = number(first)?;
    let end = if last.is_empty() {
        size
    } else {
        number(last)?.saturating_add(1).min(size)
    };
    (start < end).then_some((start, end))
}

fn disposition(name: &str, attachment: bool) -> String {
    let kind = if attachment { "attachment" } else { "inline" };
    let ascii: String = name
        .chars()
        .filter(|c| c.is_ascii() && !c.is_ascii_control())
        .collect();
    let escaped = ascii.replace('\\', "\\\\").replace('"', "\\\"");
    let mut result = format!("{kind}; filename=\"{escaped}\"");
    if ascii != name {
        let encoded =
            percent_encoding::utf8_percent_encode(name, percent_encoding::NON_ALPHANUMERIC);
        result.push_str(&format!("; filename*=UTF-8''{encoded}"));
    }
    result
}

impl Representation {
    pub async fn respond(self, request: &HeaderMap) -> Result<Response> {
        let get = |name| request.get(name).and_then(|v| v.to_str().ok());
        let mut builder = Response::builder()
            .header(header::CONTENT_TYPE, self.mime)
            .header(
                header::CONTENT_DISPOSITION,
                disposition(&self.name, self.attachment),
            )
            .header(header::X_CONTENT_TYPE_OPTIONS, "nosniff")
            .header(
                header::CONTENT_SECURITY_POLICY,
                "sandbox; default-src 'none'",
            );
        if let Some(tag) = &self.tag {
            builder = builder.header(header::ETAG, tag);
        }
        let modified = self.modified.map(httpdate::fmt_http_date);
        if let Some(date) = &modified {
            builder = builder.header(header::LAST_MODIFIED, date);
        }
        let date_matches = |value: &str| {
            modified
                .as_deref()
                .and_then(|s| httpdate::parse_http_date(s).ok())
                .zip(httpdate::parse_http_date(value).ok())
                .is_some_and(|(modified, since)| modified <= since)
        };
        let unmodified = match get(header::IF_NONE_MATCH) {
            Some(tags) => tags.split(',').map(str::trim).any(|tag| {
                tag == "*" || Some(tag.strip_prefix("W/").unwrap_or(tag)) == self.tag.as_deref()
            }),
            None => get(header::IF_MODIFIED_SINCE).is_some_and(date_matches),
        };
        if unmodified {
            return builder
                .status(StatusCode::NOT_MODIFIED)
                .body(Body::empty())
                .map_err(invalid_response);
        }
        let allow_range = get(header::IF_RANGE)
            .is_none_or(|val| Some(val) == self.tag.as_deref() || date_matches(val));
        let (start, end) =
            if let Some(value) = get(header::RANGE).filter(|_| allow_range && self.size > 0) {
                let Some(range) = bytes_range(value, self.size) else {
                    return Ok((
                        StatusCode::RANGE_NOT_SATISFIABLE,
                        [(header::CONTENT_RANGE, format!("bytes */{}", self.size))],
                    )
                        .into_response());
                };
                builder = builder
                    .status(StatusCode::PARTIAL_CONTENT)
                    .header(header::ACCEPT_RANGES, "bytes")
                    .header(
                        header::CONTENT_RANGE,
                        format!("bytes {}-{}/{}", range.0, range.1 - 1, self.size),
                    );
                range
            } else {
                (0, self.size)
            };
        let body = match self.payload {
            Payload::Bytes(bytes) => Body::from(bytes.slice(start as usize..end as usize)),
            Payload::File(file) => {
                let mut file = tokio::fs::File::from_std(file);
                if start > 0 {
                    file.seek(SeekFrom::Start(start))
                        .await
                        .map_err(invalid_response)?;
                }
                Body::from_stream(ReaderStream::new(file.take(end - start)))
            }
        };
        builder
            .header(header::CONTENT_LENGTH, end - start)
            .body(body)
            .map_err(invalid_response)
    }
}

fn invalid_response(_: impl std::fmt::Display) -> WebError {
    WebError(StatusCode::INTERNAL_SERVER_ERROR, "文件响应失败")
}
