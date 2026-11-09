//! Assemble the shared AI implementation without adding a Node runtime to the app.
fn module(source: &str, exports: &str, imports: &str) -> String {
    let body = source
        .replace("\r\n", "\n")
        .lines()
        .filter(|line| !line.starts_with("import ") && !line.starts_with("export {"))
        .collect::<Vec<_>>()
        .join("\n")
        .replace("export ", "");
    format!("(()=>{{{imports}\n{body}\nreturn {{{exports}}};}})()")
}
pub fn script() -> String {
    let vendor =
        include_str!("../../../../desktop-electron/src/browser/ai/vendor/playwright-injected.js");
    let injected = vendor
        .split_once("export default ")
        .expect("shared injected module")
        .1
        .trim()
        .trim_end_matches(';');
    let world = module(
        include_str!("../../../../desktop-electron/src/browser/ai/world.js"),
        "BOOT_SCRIPT,WORLD_NAME",
        &format!("const injectedSource={injected};"),
    );
    let keys = module(
        include_str!("../../../../desktop-electron/src/browser/ai/keys.js"),
        "keyEvents,charEvents,modifierBits",
        "const process = { platform: 'win32' };",
    )
    .replace("ControlOrMeta: 'Meta'", "ControlOrMeta: 'Control'");
    let snapshots = module(
        include_str!("../../../../desktop-electron/src/browser/ai/snapshot-text.js"),
        "chooseSnapshot,truncate,ACTION_SNAPSHOT_LIMIT,EXPLICIT_SNAPSHOT_LIMIT",
        "",
    );
    let site = module(
        include_str!("../../../../desktop-electron/src/browser/ai/site.js"),
        "siteOf",
        "",
    );
    let driver = module(
        include_str!("../../../../desktop-electron/src/browser/ai/driver.js"),
        "TabDriver,BrowserToolError,withTimeout",
        "const {BOOT_SCRIPT,WORLD_NAME}=world;const {keyEvents,charEvents,modifierBits}=keys;",
    );
    let controller = module(include_str!("../../../../desktop-electron/src/browser/ai/controller.js"), "BrowserAI,aiOwner", "const {TabDriver,BrowserToolError,withTimeout}=driver;const {chooseSnapshot,truncate,ACTION_SNAPSHOT_LIMIT,EXPLICIT_SNAPSHOT_LIMIT}=snapshots;const {siteOf}=site;const {path,fs,profileKey,manualNavigationURL,browserBounds,passwordOrigin}=platform;");
    format!("(()=>{{const world={world};const keys={keys};const snapshots={snapshots};const site={site};const driver={driver};{}\nconst shared={controller};\n{}\n}})();", include_str!("ai-platform.js"), include_str!("ai-runtime.js"))
}
#[cfg(test)]
mod tests {
    #[test]
    fn shared_modules_are_embedded() {
        let script = super::script();
        assert!(script.contains("class BrowserAI"));
        assert!(script.contains("class TabDriver"));
        assert!(!script.contains("import path from"));
        assert!(script.contains("__astrionAI"));
    }
}
