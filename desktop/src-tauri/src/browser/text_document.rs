//! 纯文本类响应（text/plain、application/json、脚本源码等）在 WebView2 里被渲染成
//! 「只有一个 <pre>、没有任何样式」的空文档：html/body 背景透明、默认文字色是黑色。
//! 标签视图的默认底色是应用主题的 --surface-base（深色主题为深灰），于是深色模式下
//! 变成「深底黑字」完全不可读。这里给这类文档补一份跟随应用主题的样式；
//! 普通网页（text/html）不受任何影响。
//! 判定与样式和 macOS 壳 desktop-electron/src/browser/text-documents.js 保持一致。

const STYLE_ID: &str = "__astrion_text_document_theme";
/// Chromium 会自己渲染的文档类型：不注入。
const DOCUMENT_TYPES: &str = r#"["text/html","application/xhtml+xml","image/svg+xml","application/pdf"]"#;

pub fn is_color(value: &str) -> bool {
    value.len() == 7 && value.starts_with('#') && value[1..].chars().all(|c| c.is_ascii_hexdigit())
}

fn is_dark(color: &str) -> bool {
    let value = u32::from_str_radix(color.trim_start_matches('#'), 16).unwrap_or(0xfaf9f5);
    let (r, g, b) = ((value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff);
    // 括号是必须的：不包住整个表达式时 Rust 会把 `b as f64 < 128.0` 当成泛型参数
    // （`<` 被当作泛型起始），Windows 侧编译直接失败。
    (0.299 * r as f64 + 0.587 * g as f64 + 0.114 * b as f64) < 128.0
}

/// 在页面里执行的注入脚本：只对文本渲染的文档生效。
pub fn script(surface: &str, text: &str) -> String {
    let scheme = if is_dark(surface) { "dark" } else { "light" };
    let css = serde_json::json!(format!(
        "html{{background:{surface} !important;color-scheme:{scheme}}}body{{color:{text} !important}}"
    ))
    .to_string();
    let id = serde_json::json!(STYLE_ID).to_string();
    format!(
        "(() => {{ try {{
    const type = String(document.contentType || '').toLowerCase();
    if (!type || {DOCUMENT_TYPES}.includes(type)) return false;
    const body = document.body;
    if (!body) return false;
    // Chromium 的文本查看器把响应包成单个 <pre>（JSON 还会多一个提示节点）。
    const children = Array.prototype.slice.call(body.children);
    if (!children.length || !children.some(child => child.tagName === 'PRE')) return false;
    let style = document.getElementById({id});
    if (!style) {{
      style = document.createElement('style');
      style.id = {id};
      (document.head || document.documentElement).appendChild(style);
    }}
    style.textContent = {css};
    return true;
  }} catch (error) {{ return false; }} }})();"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_theme_colors() {
        assert!(is_color("#1a1a1a"));
        assert!(is_color("#FFFFFF"));
        assert!(!is_color("#1a1a"));
        assert!(!is_color("1a1a1a"));
        assert!(!is_color("#1a1a1g"));
    }

    #[test]
    fn derives_color_scheme_from_surface() {
        assert!(is_dark("#1a1a1a"));
        assert!(is_dark("#141413"));
        assert!(!is_dark("#faf9f5"));
        assert!(!is_dark("#ffffff"));
    }

    #[test]
    fn script_skips_html_and_carries_theme_colors() {
        let script = script("#1a1a1a", "#ffffff");
        assert!(script.contains("text/html"));
        assert!(script.contains("#1a1a1a"));
        assert!(script.contains("#ffffff"));
        assert!(script.contains("color-scheme:dark"));
        assert!(script.contains(STYLE_ID));
        assert!(!script.contains("surfaceColor"));
    }
}
