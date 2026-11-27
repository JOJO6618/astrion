use super::{origin, ImportResult, Preview};
use serde_json::{json, Value};
use std::borrow::Cow;
use std::collections::HashSet;
use std::time::{SystemTime, UNIX_EPOCH};

const MAX_EXPORT_BYTES: usize = 4 * 1024 * 1024;
const MAX_EXPORT_ROWS: usize = 10_000;
const MAX_CSV_COLUMNS: usize = 20;
const MAX_CSV_FIELD: usize = 65_536;

pub(super) fn preview(value: &Value) -> ImportResult<Preview> {
    let content = value["content"]
        .as_str()
        .filter(|content| content.len() <= MAX_EXPORT_BYTES)
        .ok_or("import_file_too_large")?;
    if content.contains('\0') {
        return Err("invalid_import_file");
    }
    let content = content.strip_prefix('\u{feff}').unwrap_or(content);
    match value["format"].as_str() {
        Some("password-csv") => password_csv(content),
        Some("cookie-json") => cookie_json(content),
        _ => Err("invalid_import_file"),
    }
}

fn password_csv(content: &str) -> ImportResult<Preview> {
    let mut csv = Csv(content);
    let header = csv.row()?.ok_or("invalid_import_file")?;
    let unique_columns = header.iter().collect::<HashSet<_>>();
    if header.len() > MAX_CSV_COLUMNS || unique_columns.len() != header.len() {
        return Err("invalid_import_file");
    }
    let column = |name| {
        header
            .iter()
            .position(|field| field == name)
            .ok_or("invalid_import_file")
    };
    let (url_column, user_column, password_column) =
        (column("url")?, column("username")?, column("password")?);
    let mut preview = Preview::default();
    let stats = &mut preview.stats.passwords;
    while let Some(row) = csv.row()? {
        if row.is_empty() {
            continue;
        }
        stats.read += 1;
        if stats.read > MAX_EXPORT_ROWS {
            return Err("import_file_too_many_records");
        }
        if row.len() != header.len() {
            stats.issue("invalid_record", true);
            continue;
        }
        let (url, user, password) = (&row[url_column], &row[user_column], &row[password_column]);
        let Ok(origin) = origin(url) else {
            stats.issue("invalid_record", true);
            continue;
        };
        if password.is_empty() {
            stats.issue("not_password", false);
        } else if url.chars().count() > 8_192
            || user.chars().count() > 4_096
            || password.chars().count() > 16_384
        {
            stats.issue("invalid_record", true);
        } else {
            preview.passwords.push(json!({
                "origin": origin, "username": user.as_ref(),
                "password": password.as_ref(), "sourceURL": url.as_ref(), "modified": 0
            }));
            stats.ready += 1;
        }
    }
    Ok(preview)
}

// Chrome export follows CSV quoting, including escaped quotes and literal newlines in fields.
struct Csv<'a>(&'a str);

impl<'a> Csv<'a> {
    fn row(&mut self) -> ImportResult<Option<Vec<Cow<'a, str>>>> {
        if self.0.is_empty() {
            return Ok(None);
        }
        if self.newline() {
            return Ok(Some(Vec::new()));
        }
        let mut row = Vec::new();
        loop {
            let value = self.field()?;
            if value.chars().count() > MAX_CSV_FIELD {
                return Err("invalid_import_file");
            }
            // One extra field marks a row as too wide. Keep parsing its syntax.
            if row.len() <= MAX_CSV_COLUMNS {
                row.push(value);
            }
            if let Some(tail) = self.0.strip_prefix(',') {
                self.0 = tail;
            } else if self.0.is_empty() || self.newline() {
                return Ok(Some(row));
            } else {
                return Err("invalid_import_file");
            }
        }
    }

    fn newline(&mut self) -> bool {
        let tail = self
            .0
            .strip_prefix("\r\n")
            .or_else(|| self.0.strip_prefix(['\r', '\n']));
        if let Some(tail) = tail {
            self.0 = tail;
        }
        tail.is_some()
    }

    fn field(&mut self) -> ImportResult<Cow<'a, str>> {
        let Some(quoted) = self.0.strip_prefix('"') else {
            let end = self.0.find([',', '\r', '\n']).unwrap_or(self.0.len());
            let (field, tail) = self.0.split_at(end);
            self.0 = tail;
            return Ok(Cow::Borrowed(field));
        };
        let mut quotes = quoted.match_indices('"');
        let mut escaped = false;
        while let Some((end, _)) = quotes.next() {
            if quoted.as_bytes().get(end + 1) == Some(&b'"') {
                quotes.next();
                escaped = true;
                continue;
            }
            self.0 = &quoted[end + 1..];
            let value = &quoted[..end];
            return Ok(if escaped {
                Cow::Owned(value.replace("\"\"", "\""))
            } else {
                Cow::Borrowed(value)
            });
        }
        Err("invalid_import_file")
    }
}

fn cookie_json(content: &str) -> ImportResult<Preview> {
    let bundle: Value = serde_json::from_str(content).map_err(|_| "invalid_import_file")?;
    if bundle["format"].as_str() != Some("astrion-cookie-export")
        || bundle["version"].as_u64() != Some(1)
    {
        return Err("invalid_import_file");
    }
    let rows = bundle["cookies"].as_array().ok_or("invalid_import_file")?;
    if rows.len() > MAX_EXPORT_ROWS {
        return Err("import_file_too_many_records");
    }
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64();
    let mut preview = Preview::default();
    let stats = &mut preview.stats.cookies;
    stats.read = rows.len();
    for row in rows {
        match exported_cookie(row) {
            Ok((_, expires)) if expires != -1.0 && expires <= now => stats.issue("expired", false),
            Ok((cookie, _)) => {
                preview.cookies.push(cookie);
                stats.ready += 1;
            }
            Err(reason) => stats.issue(reason, true),
        }
    }
    Ok(preview)
}

fn exported_cookie(row: &Value) -> ImportResult<(Value, f64)> {
    let field = |key, max| {
        row[key]
            .as_str()
            .filter(|value| {
                value.chars().count() <= max && !value.chars().any(|ch| ch.is_ascii_control())
            })
            .ok_or("invalid_record")
    };
    let flag = |key| row[key].as_bool().ok_or("invalid_record");
    let domain = field("domain", 253)?;
    let name = field("name", 4_096)?;
    let value = field("value", 16_384)?;
    let path = field("path", 4_096)?;
    let secure = flag("secure")?;
    let http_only = flag("httpOnly")?;
    let host_only = flag("hostOnly")?;
    let session = flag("session")?;
    let host = domain.strip_prefix('.').unwrap_or(domain);
    if host.is_empty()
        || host.starts_with('.')
        || (host_only && domain.starts_with('.'))
        || host.contains(['/', ':', '@', '?', '#', '\\'])
        || !path.starts_with('/')
        || origin(&format!("https://{host}"))? != format!("https://{}", host.to_lowercase())
    {
        return Err("invalid_record");
    }
    let same_site = match row["sameSite"].as_str() {
        Some("unspecified") => -1,
        Some("no_restriction") if secure => 0,
        Some("lax") => 1,
        Some("strict") => 2,
        _ => return Err("invalid_record"),
    };
    let expires = if session {
        -1.0
    } else {
        row["expirationDate"]
            .as_f64()
            .filter(|value| value.is_finite() && *value > 0.0)
            .ok_or("invalid_record")?
    };
    let domain = if host_only {
        host.to_owned()
    } else {
        format!(".{host}")
    };
    let mut cookie = json!({
        "domain": domain, "name": name, "value": value, "path": path,
        "secure": secure, "httpOnly": http_only, "expires": expires, "sameSite": same_site
    });
    if let Some(partition) = row
        .get("partitionKey")
        .filter(|partition| !partition.is_null())
    {
        let top_level_site = partition["topLevelSite"]
            .as_str()
            .ok_or("invalid_record")?
            .trim_end_matches('/');
        let cross_site_ancestor = partition["hasCrossSiteAncestor"]
            .as_bool()
            .ok_or("invalid_record")?;
        if !secure || origin(top_level_site)? != top_level_site {
            return Err("invalid_record");
        }
        cookie["partitionKey"] = json!({
            "topLevelSite": top_level_site,
            "hasCrossSiteAncestor": cross_site_ancestor
        });
    }
    Ok((cookie, expires))
}
