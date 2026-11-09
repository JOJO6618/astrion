//! Reuse the Electron adapter's field grouping and DOM revalidation verbatim.
const SOURCE: &str =
    include_str!("../../../../desktop-electron/src/browser/passwords/page-preload.cjs");
pub const WORLD: &str = "astrion-password-isolated-v1";
pub const BINDING: &str = "__astrionPasswordSignal";
pub fn script() -> Result<String, String> {
    let mut source = SOURCE.replace("\r\n", "\n");
    for (from,to) in [
        ("const { ipcRenderer } = require('electron');\nif (process.isMainFrame) {", "(() => { if (globalThis.__astrionAdapterInstalled) return; globalThis.__astrionAdapterInstalled = true; const ipcRenderer = { send: (_channel, value) => __astrionPasswordSignal(JSON.stringify(value)) };"),
        ("ipcRenderer.on('browser:password-fill', (_event, value) => {", "globalThis.__astrionFill = value => {"),
        ("fill(group.username, value.username); fill(group.password, value.password);\n  });", "fill(group.username, value.username); fill(group.password, value.password);\n  };"),
    ] {
        if source.matches(from).count() != 1 { return Err("browser_password_adapter_source_changed".into()); }
        source = source.replacen(from,to,1);
    }
    let end = source
        .rfind('}')
        .ok_or("browser_password_adapter_source_changed")?;
    source.replace_range(end.., "})();");
    Ok(source)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shared_adapter_transport_is_isolated() {
        let code = script().unwrap();
        assert!(!code.contains("require("));
        assert!(!code.contains("ipcRenderer.on"));
        assert!(code.contains("const live = fields(group.container)"));
        assert!(code.contains("event.isTrusted"));
    }
}
