# providers — Rust provider management

Python source: `server/providers.py` + `modules/provider_manager.py`.
Contract: `docs/providers_api.md`, `docs/agents_details/settings_and_providers.md`.

## Router

```rust
use astrion_headless::providers::{ProviderConfig, router};

let api = host_api.merge(router(ProviderConfig::host(
    &paths.resources, // repo root; reads <root>/config/providers_catalog.json
    &paths.deploy,    // writes custom_models.json here
    data,             // <DATA_DIR>: providers.json, modelsdev_cache.json, codex_*
)));
let app = auth.protect_api(api);
```

- `ProviderConfig::new(repo_config, data_dir, deploy_config)` for explicit paths.
- `ProviderConfig::host(repo_root, deploy_config, data_dir)` joins `config/`.
- `ProviderConfig::with_client(client)` injects a transport for tests. Production
  leaves it unset so reqwest reads `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY`/`NO_PROXY`
  for every probe (matching httpx's per-client `trust_env`).
- Mount **inside** `WebAuth::protect_api`. That supplies the authenticated Host
  browser session and CSRF validation for `POST`/`PUT`/`DELETE`. The router also
  rejects requests without a `BrowserSession` extension (`403 admin_required`)
  so it cannot be exposed unauthenticated.

Routes:

| Method | Path |
| --- | --- |
| GET | `/api/providers/catalog` |
| GET | `/api/providers` |
| POST | `/api/providers/connect` |
| POST | `/api/providers/{provider_id}/refresh` |
| POST | `/api/providers/refresh-all` |
| DELETE | `/api/providers/{provider_id}` |
| GET/POST | `/api/custom-models` |
| PUT/DELETE | `/api/custom-models/{model_name}` |

## Preserved contracts

- `providers.json` v1 is migrated in place (display index, base name, name,
  requested id, `version=2`); old keys stay valid as internal connection IDs.
- New connection IDs are `<catalog_id>-<8 hex>` / `custom-<slug>-<8 hex>` and
  never reused. Display indices are allocated per catalog group and survivors
  are never renumbered.
- Credentials are written atomically with mode 0600; responses only expose
  `api_key_masked`. `not-required` placeholders never surface as credentials.
- `/models` parsing keeps `context_length`, `default_context_window`,
  `max_context_window`, `max_input_tokens`, `max_output_tokens`. An upstream
  explicit `context_length` is also recorded as `max_context_window`; a bare
  `context_window` remains legacy-only. This matches the shared
  `test/2026-11-24_Rust后端/catalog_model_limits.json` fixture used by the model
  catalog.
- Discovery filters non-chat model IDs, applies catalog `static_models` only
  when online discovery fails/returns empty, and preserves the previous model
  list when a refresh fails.
- `refresh-all` runs enabled provider connections sequentially, is throttled to
  one real run per process hour, and reports `skipped/reason` (`throttled`
  before starting, `in_progress` while running).
- After a successful `connect` or single-provider `refresh` (including each item
  of `refresh-all`), the models.dev cache is refreshed best-effort, matching
  Python: `<DATA_DIR>/modelsdev_cache.json` is skipped when it is `version=3`
  with a strict `fetched_at` (`YYYY-MM-DDTHH:MM:SSZ`) younger than 6 hours;
  otherwise `https://models.dev/api.json` is fetched, slimmed to the catalog's
  `modelsdev_key` set (`npm`, `reasoning`, `modalities_input`, `context`,
  `max_input`, `max_output`, cleaned `cost`), and atomically replaced with mode
  0600. Request/payload/write failures keep the previous cache and never change
  the connect/refresh response. Use `ProviderConfig::without_modelsdev_refresh()`
  for isolated tests; `with_modelsdev_url` points it at a loopback fixture.
- Connected Codex accounts can refresh their own model directory. The refresh
  obtains/rotates the OAuth access token (5-minute skew, atomic 0600 rewrite of
  the same credential file), sends the Codex CLI headers and
  `client_version`, honors ETag/304 (never reusing an ETag across versions),
  and writes `<DATA_DIR>/codex_models.json` (legacy account) or
  `<DATA_DIR>/codex_models/<id>.json` atomically. Failures keep the previous
  cache and surface as `error`/`fallback`, never as a silent success.
- `refresh-all` appends every `ready` Codex account after the provider rows as
  `{kind:"codex", id, success, models_count, error}`. `credential_missing` /
  `disconnected` accounts are skipped; a failed account keeps its previous list
  and reports `success=false` with `models_count=null`.
- Connected accounts can generate Responses in Agent, Chat, scheduled tasks and reviews. The request uses the selected connection's credential and account identity. The model catalog and the account list both discover older credential files when the account index is absent.
- `codex_router(ProviderConfig)` exposes `GET /api/codex/accounts`,
  `GET` and `POST /api/codex/settings`, and
  `POST /api/codex/models/refresh?account_id=` for **Host Web** assembly.
  Accounts use the Python public fields, legacy-first/created-at/id order, and
  per-account model-cache summary; missing credentials short-circuit to
  `{model_count,total_models}`. Index connection IDs are restricted to ASCII
  alphanumerics plus `-`/`_` before any credential/cache path is joined.
  Python `server/headless_app.py` does **not** register `codex_auth_bp`, so do
  not mount this router in headless mode; headless still refreshes Codex
  internally through `/api/providers/refresh-all`.

## Behavior gaps (not migrated yet)

1. **Codex login/logout and account lifecycle are not migrated.** `catalog`
   projects `codex_auth.json`, `codex_accounts.json`, `codex_accounts/*.json`
   and the per-account model cache; model refresh (including token rotation) is
   implemented here. Login start/poll/cancel, logout and usage/reset-credits
   remain in the Python Codex API. The settings endpoint and OAuth client use Python's environment-first proxy precedence. Saving settings updates the legacy model cache under the same lock as its refresh. Account
   index migration/discovery is recomputed per request and not persisted by
   Rust. The legacy `~/.codex/models_cache.json` fallback used by Python model
   refresh is not read (no CLI cache dependency in this module).
2. **Error detail text differs.** Error codes and prefixes match
   (`connect_error:`, `timeout`, `request_error:`, `auth_failed (http_401)`,
   `http_500`, `empty_models`, `bad_payload`), but the exception text comes from
   reqwest rather than httpx.
3. **SOCKS proxies.** reqwest is compiled without its `socks` feature, so
   `ALL_PROXY=socks5://…` is not honored; HTTP(S) proxies are.
4. **Header value stringification.** Python `str()` of JSON scalars matches
   (`True`/`False`/`None`/numbers/strings); arrays/objects would differ.
5. **No cross-process lock.** Like the Python manager, correctness relies on the
   single Host writer. Reads and commits reload the file, so external writes are
   picked up, but two servers on one data directory are unsupported.
6. **Rate limiting.** `server/security.py` `@rate_limited` is not ported; Host
   mode exempts authenticated business APIs anyway.
7. **Refresh status code.** `provider_not_found` on refresh returns 400 (the
   Python view's actual behavior); `docs/providers_api.md` says 404.
8. **Admin identity.** The Host browser session is the administrator, so no
   separate role lookup is performed. A future Docker/multi-user mount must add
   an admin gate before mounting.
