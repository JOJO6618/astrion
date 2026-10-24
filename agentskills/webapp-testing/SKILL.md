---
name: webapp-testing
description: Toolkit for interacting with and testing local web applications using Playwright. Supports verifying frontend functionality, debugging UI behavior, capturing browser screenshots, and viewing browser logs.
license: Complete terms in LICENSE.txt
---

# Web Application Testing

Use Astrion's configured Playwright MCP tools when available: inspect the page snapshot, act on observed references, and inspect console/network evidence. Write native Python Playwright scripts when reusable automated checks are useful or MCP lacks the needed capability. Respect workspace policy when visual acceptance is reserved for the user; do not substitute automated screenshots for that acceptance.

Resolve helper paths relative to this loaded SKILL.md. Examples use `python` as a verified project interpreter and `scripts/...` as skill-relative paths; pass the resolved script path when cwd is the target application. Probe Playwright and its browser runtime before standalone scripts, and install only what the task needs. Keep logs/screenshots in a task-specific workspace directory.

**Helper Scripts Available**:
- `scripts/with_server.py` - Manages server lifecycle (supports multiple servers)

**Always run scripts with `--help` first** to see usage. DO NOT read the source until you try running the script first and find that a customized solution is abslutely necessary. These scripts can be very large and thus pollute your context window. They exist to be called directly as black-box scripts rather than ingested into your context window.

## Decision Tree: Choosing Your Approach

```
User task → Is it static HTML?
    ├─ Yes → Read HTML file directly to identify selectors
    │         ├─ Success → Write Playwright script using selectors
    │         └─ Fails/Incomplete → Treat as dynamic (below)
    │
    └─ No (dynamic webapp) → Is the server already running?
        ├─ No → Run: python scripts/with_server.py --help
        │        Then use the helper + write simplified Playwright script
        │
        └─ Yes → Reconnaissance-then-action:
            1. Navigate and wait for networkidle
            2. Take screenshot or inspect DOM
            3. Identify selectors from rendered state
            4. Execute actions with discovered selectors
```

## Example: Using with_server.py

Reuse an existing server. The helper is for a bounded test's own servers; use a persistent terminal for a preview that must remain available to the user. After starting a preview, include the actual complete localhost URL in the reply for Astrion's preview panel. Do not stop or replace an existing application process.

To start test-owned servers, run the resolved helper with `--help` first:

**Single server:**
```bash
python scripts/with_server.py --server "npm run dev" --port 5173 -- python your_automation.py
```

**Multiple servers (e.g., backend + frontend):**
```bash
python scripts/with_server.py \
  --server "cd backend && python server.py" --port 3000 \
  --server "cd frontend && npm run dev" --port 5173 \
  -- python your_automation.py
```

To create an automation script, include only Playwright logic (servers are managed automatically):
```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True) # Always launch chromium in headless mode
    page = browser.new_page()
    page.goto('http://localhost:5173') # Use the actual server URL
    page.get_by_role('main').wait_for(state='visible') # Replace with a real readiness condition
    # Prefer web-first assertions for the specific UI state under test
    browser.close()
```

## Reconnaissance-Then-Action Pattern

1. **Inspect rendered DOM**:
   ```python
   # Create output/test-browser beforehand; capture only when permitted
   page.screenshot(path='output/test-browser/inspect.png', full_page=True)
   content = page.content()
   page.get_by_role('button').all()
   ```

2. **Identify selectors** from inspection results

3. **Execute actions** using discovered selectors

## Common Pitfall

Do not treat `networkidle` as universal readiness: polling, analytics and streaming may keep a page active. Wait for the actual target element, response or application state with a bounded timeout. A static HTML file is evidence for structure, not proof that dynamic behavior works.

## Best Practices

- **Use bundled scripts as black boxes** - To accomplish a task, consider whether one of the scripts available in `scripts/` can help. These scripts handle common, complex workflows reliably without cluttering the context window. Use `--help` to see usage, then invoke directly. 
- Use `sync_playwright()` for synchronous scripts
- Always close the browser when done
- Prefer accessible roles/names and stable test IDs over fragile positional CSS selectors.
- Prefer observable conditions and web-first assertions over fixed timeouts.
- Report tested routes/actions, observed failures and untested behavior; attach actual evidence with workspace file links.

## Reference Files

- **examples/** - Examples showing common patterns:
  - `element_discovery.py` - Discovering buttons, links, and inputs on a page
  - `static_html_automation.py` - Using file:// URLs for local HTML
  - `console_logging.py` - Capturing console logs during automation