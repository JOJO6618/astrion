"""Inspect installed Rust APIs; never reads browser profiles or credentials."""
from pathlib import Path
import re

registry = Path.home() / ".cargo" / "registry" / "src"
queries = {
    "tauri-2.11.6/src/webview/mod.rs": ["pub fn controller", "pub fn environment", "pub fn with_webview"],
    "webview2-com-0.38.2/src/lib.rs": ["pub use", "pub mod"],
    "webview2-com-0.38.2/src/callback.rs": ["struct .*CompletedHandler", "struct .*EventHandler", "pub fn create"],
    "wry-0.55.1/src/webview2/mod.rs": ["WebMessageReceivedEventHandler", "add_WebMessageReceived", "CreateCoreWebView2EnvironmentWithOptions", "SetParentWindow", "SetBounds"],
}
for relative, patterns in queries.items():
    matches = list(registry.glob("*/" + relative))
    print("\nSOURCE", relative, "FOUND", len(matches))
    if not matches:
        continue
    lines = matches[0].read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if any(re.search(pattern, line) for pattern in patterns):
            print("\n" + "\n".join(f"{j + 1}: {lines[j]}" for j in range(max(0, i - 3), min(len(lines), i + 9))))
print("\nSDK SOURCES")
for path in registry.glob("*/webview2-com-sys-*/src/bindings.rs"):
    print(path, path.stat().st_size)
