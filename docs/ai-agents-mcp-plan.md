# RemoteRF for AI agents: CLI + MCP (Claude, ChatGPT, Gemini) — implementation plan

Status: plan only, written 2026-10-14. Nothing here is implemented yet.
Supersedes `RemoteRF-Server/docs/agent-cli-plan.md` (2026-09-28). That plan's core still holds: extend the
`remoterf` command, experiments stay ordinary local Python. Its "no MCP" stance is reversed here, and its
file paths are stale (`src/remoteRF/` is now `src/remoterf/`, `global_client/` is now `deployment/`).

Audience: an engineer or coding agent starting with no prior context. Read sections 1–4 before writing code.

---

## 1. Goal

Make RemoteRF usable from AI assistants end to end. A user should be able to tell Claude, ChatGPT or Gemini
"find me a free Pluto at ucla tomorrow at 2 pm, reserve it, and write me a receive script", and have:

1. the assistant discover devices and availability (read-only),
2. reserve or cancel only after explicit user approval,
3. learn the exact Python API for that device and produce a runnable script with the right import and token,
4. understand RemoteRF from docs that match the installed version.

Non-goals (v1): remote execution of user Python on RemoteRF hosts; a generic "call any device method"
tool; transmit (TX) through any AI tool; streaming IQ through MCP; an LLM dependency in the client.

## 2. Which AI apps, and how each connects

There are two MCP transports. A full rollout needs both.

| AI app | Connects to | Transport | Auth |
|---|---|---|---|
| Claude Code (CLI) | local `remoterf mcp` | stdio | the user's saved RemoteRF login on that machine |
| Claude Desktop | local `remoterf mcp` (also remote, via connectors) | stdio | same |
| Codex CLI (OpenAI) | local `remoterf mcp` | stdio (`~/.codex/config.toml`) | same |
| Gemini CLI | local `remoterf mcp` | stdio (`~/.gemini/settings.json`, key `mcpServers` → `command`) | same |
| Cursor / VS Code / Windsurf | local `remoterf mcp` | stdio | same |
| claude.ai web/mobile (Custom connector) | `https://global.remoterf.net/mcp` | Streamable HTTP | OAuth 2.1 (DCR supported) |
| ChatGPT (Developer mode / Apps) | `https://global.remoterf.net/mcp` | Streamable HTTP (SSE also accepted) | OAuth 2.1 + PKCE; no static bearer tokens; localhost not allowed |
| Gemini app (Gemini Spark "Custom apps") | `https://global.remoterf.net/mcp` | HTTPS MCP | OAuth; personal Google accounts only, not work/school |
| Gemini CLI (remote variant) | same URL | `httpUrl` key | OAuth or `headers` bearer |

Facts above are from public docs and guides as of Oct 2026 and churn often: plan limits for ChatGPT
(some sources say full read/write MCP only on Business/Enterprise/Edu), Gemini Spark eligibility, and the
MCP auth spec revision. **Re-verify each client's current docs in Phase 5 before announcing support.**

Sources: Anthropic custom connectors (support.claude.com/en/articles/11175166, claude.com/docs/connectors/custom/remote-mcp);
OpenAI developer mode (platform.openai.com/docs/guides/developer-mode); Gemini CLI MCP docs
(github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md); Gemini Spark custom MCP (9to5google.com/2026/06/30/gemini-spark-apps-more/);
MCP authorization spec (modelcontextprotocol.io/specification/draft/basic/authorization).

## 3. System context (what exists today)

Repos (siblings under `~/Documents/GitHub/`):

- **RemoteRF-Client**: PyPI `remoterf` (import `remoterf`), currently 2.7.0, `requires-python >=3.10`.
  One console script, `remoterf = remoterf.remoterf_cli:main`. Tests: `.venv/bin/python -m pytest tests`.
  The baseline has 10 failures and 4 errors from missing `aioquic` and the bundled-driver schema checks. Compare against that baseline, not zero.
- **RemoteRF-Server**: PyPI `remoteRF-server` (lab server, `serverrf`), 1.4.12. Labs run this.
- **remoterf-vps-global**: Global broker + Global HOME (the `deployment` service, which holds Global user accounts),
  plus `ops/vpsrf` (VPS operator CLI and Slack). Branch `feat/global-device-federation` is the live branch. Tests: `.venv/bin/python -m pytest tests --ignore=tests/e2e`, currently 280 passing.
- **wireless-lab-at-ucla.gitlab.io**: public docs site, Jekyll (Ruby 2.6 image in `.gitlab-ci.yml`), published at
  `https://remoterf.net`. Client docs are in `docs/client/*.md` (`usage.md` is stale). Nav is in `_data/docs_navigation.yml`.

Hosts:
- Global VPS: `root@global.remoterf.net`, DigitalOcean, 1 vCPU / ~1 GB RAM (~600 MB used). Docker compose stack at
  `/opt/remoterf-v2-live/compose/compose.yml` (services `broker`, `deployment`, `edge`, `relay`, legacy).
  Images are pinned (`remoterf-v2-validation-global:<sha>`, `pull_policy: never`).
- rrf2 (lab "ucla"): PRODUCTION lab server. Back up before touching; never wipe; ask before account-affecting changes.

Client internals relevant here (verified 2026-10-14):
- Interactive shell `src/remoterf/core/app.py` (prompt loop `run()`, commands getdev/resdev/getres/myres/cancelres/perms/status/enroll/...).
  All logic is entangled with printing and prompts: native device rows are parsed from display strings
  (`_native_device_rows`), slot math is `_pick_slot_and_reserve` / `_auto_block_minutes`, and federated rows are `_federated_*`.
- Account backend `src/remoterf/core/grpc_acc.py` `RemoteRFAccount`: `login_user`, `get_devices`, `get_reservations`,
  `reserve_device(device_id, start_dt, end_dt)`, `cancel_reservation`, `get_perms`, `install_reserved_driver`, `logout`.
  - Native (gRPC) homes return protobuf maps of display strings. An error is an `ace` key.
  - HTTPS/Global homes (`src/remoterf/deployment/backend.py`, `selected_backend().resume()`) return dicts and raise
    `AccountBackendError(code, provenance, retry_after)` (`src/remoterf/deployment/http.py`).
- Python API: `remoterf.devices()` (`core/availability.py`) prints and returns rows (`handle,name,type,lab,online,state,until,next`).
  Drivers are `remoterf.drivers.adalm_pluto.adi.Pluto(token=…)`, `remoterf.drivers.usrp.uhd.usrp.MultiUSRP(token=…)`, rtl_sdr, hackrf, ti_mmwave, sim_sdr,
  and dynamic drivers via `remoterf.drivers.ensure_driver(token=…)` (code generated from the server's IDL, `IDL:get_drivers`).
  `virtual=True` gives an offline simulation. The `REMOTERF_TOKEN` env var works as a token fallback.
- Tokens:
  - Global `rfg_…@lab` works anywhere with no login.
  - Native tokens are 11 chars and need the configured lab.
  - Handles look like `Pluto_00@ucla` and need a login at that lab.
  - Every token is valid only inside its reservation window.
- Credentials are stored under `~/.config/remoterf-client/` (0600 JSON; keyring only if `REMOTERF_USE_KEYRING=1`).
- Gaps:
  - no `--json` anywhere;
  - ToS, login and register are always interactive;
  - `rpc_client` can print errors (`core/grpc_client.py` `_finish`);
  - dynamic v2 device sessions are per-process.

## 4. Architecture

```
            ┌────────────── remoterf (pip package, user's machine) ──────────────┐
 AI apps    │  remoterf.ops        print-free core: typed results, typed errors   │
 (stdio) ──▶│  remoterf mcp        MCP server (stdio) over remoterf.ops           │
 shells  ──▶│  remoterf <cmd> --json   CLI over remoterf.ops                     │
            │  remoterf guide / api / example / skill install   (docs for agents) │
            └─────────────────────────────────────────────────────────────────────┘
            ┌──────────── Global VPS: deployment service (Global HOME) ───────────┐
 ChatGPT,   │  /mcp                 MCP server (Streamable HTTP) over the HOME's  │
 claude.ai, │                       existing device/reservation services          │
 Gemini  ──▶│  OAuth 2.1 AS: /.well-known/oauth-protected-resource,               │
 (HTTPS)    │  /.well-known/oauth-authorization-server, /oauth/authorize|token|   │
            │  register, login+consent page  (accounts = existing Global users)   │
            └─────────────────────────────────────────────────────────────────────┘
 Docs: remoterf.net/llms.txt + llms-full.txt (built from the Jekyll docs)
```

Key decisions:
1. **One tool catalogue, two hosts.**
   - The local MCP server and the CLI both call `remoterf.ops`.
   - The remote MCP server calls the HOME's existing Python services directly: `GlobalHomeDevices` in
     `src/remoterf_global/deployment/devices.py`, plus the reservation and account services.
   - Tool names, descriptions and JSON shapes are shared. Define them once in a small module both sides can import, or keep a
     JSON schema file in the client package and copy it to vps-global by a test that diffs them.
2. **Official MCP Python SDK.** Use PyPI `mcp` (FastMCP API). It supports stdio and Streamable HTTP, and has resource-server auth helpers.
   - Client: an optional extra, `pip install "remoterf[mcp]"`, so the base install stays light.
   - VPS: add `mcp` to the deployment image dependencies (`dependencies/` + `.build/wheelhouse`).
3. **The remote server runs inside the existing `deployment` service**, not a new container (1 GB RAM box). Route `/mcp`,
   `/oauth/*` and `/.well-known/oauth-*` through `edge` (check the Caddy config in `caddy/` and the compose file).
4. **Device I/O never goes through MCP.**
   - Tools manage reservations and produce code.
   - The user's (or the agent's) local Python talks to the radio through the normal driver path.
   - For cloud-only assistants (ChatGPT web), the tool returns a script plus run instructions for the user.

## 5. Tool catalogue (v1)

All tools return JSON objects. Times are RFC 3339 UTC and epoch seconds. Device references use the
**handle form** `Name@lab` (for example `Pluto_00@ucla`) and never the opaque Global ids. Add MCP annotations:
`readOnlyHint` for reads, `destructiveHint` and `idempotentHint: false` for mutations.

| Tool | Kind | Input | Output |
|---|---|---|---|
| `remoterf_whoami` | read | – | account, home, labs enrolled, client version |
| `remoterf_list_devices` | read | `lab?`, `online_only=true`, `type?` | `[{handle, name, type, lab, online, state(free/busy/mine/unknown), until, next, slot_minutes, max_days_ahead}]` |
| `remoterf_find_slots` | read | `handle`, `from?`, `days=1` (≤ horizon) | `[{start, end}]` aligned exactly like the shell's `resdev` |
| `remoterf_list_reservations` | read | `mine_only=true` | `[{reservation_id, handle, start, end, mine}]` |
| `remoterf_reserve` | **mutating** | `handle`, `start`, `end` | `{reservation_id, handle, start, end, token_ref, token?}` |
| `remoterf_cancel` | **mutating** | `reservation_id` | `{cancelled: true}` |
| `remoterf_device_api` | read | `handle` | the driver's API from the server IDL: class, import line, getters/setters/calls with docs; no reservation needed |
| `remoterf_example_script` | read | `handle`, `recipe` (`connect`, `rx_once`, `rx_spectrum`; **no TX recipes**), `token_ref?` | `{filename, code, run_instructions}` |
| `remoterf_guide` | read | `topic?` | version-matched agent guide text |

Resources: `remoterf://guide`, `remoterf://devices/{handle}/api`, `remoterf://docs/{page}`.
Prompts (optional): `reserve-and-receive`, `explain-my-reservations`.

Token handling:
- Reservation tokens are bearer secrets, and cloud chat transcripts are not a safe place for them.
- `remoterf_reserve` returns `token_ref` (the reservation id) by default. The raw `token` is returned only when
  `reveal_token=true` and the server-side policy allows it.
  - Local stdio: allowed by default, because it stays on the machine.
  - Remote: return the handle form instead, which works only while the user is logged in. If the user explicitly asks, return the token with a warning.
- Generated scripts read the token from `REMOTERF_TOKEN` rather than embedding it, unless the user asks otherwise.

Text from labs (device names, error messages) is untrusted data in tool outputs. Keep it in typed fields and never
concatenate it into instructions.

## 6. Security and safety rules (apply to every phase)

- **Mutations need explicit approval.** MCP clients prompt per tool call. Additionally:
  - Local: `remoterf mcp` is read-only unless started with `--allow-reserve`, or `mcp.allow_reserve=true` is set in the client config.
  - Remote: OAuth scope `remoterf.reserve` is separate from `remoterf.read`, and the consent page names both.
- Never auto-retry a reservation or cancel. Surface `retry_after` instead.
- No TX recipes, no generic method-call tool, no certificate-verification bypass, no shell-out from tools.
- Rate limits:
  - reuse the HOME's existing per-account limits (`remoterf.devices()` is 1 call per 10 s and cached);
  - add per-token limits on `/mcp`, for example 60 reads/min and 10 mutations/hour;
  - log every MCP mutation (account, tool, args, result) to the HOME audit log.
- OAuth (remote):
  - authorization code + PKCE (S256) only;
  - short-lived access tokens (≤ 1 h) with rotating refresh tokens;
  - tokens bound to the resource `https://global.remoterf.net/mcp` (RFC 8707), and audience validated on every request;
  - **never pass the client's token through to labs.** The HOME calls labs with its own federation credentials, as it does today;
  - exact-match redirect URIs; consent screen shows client name and scopes;
  - users can list and revoke connected apps (`remoterf auth apps` and a web page).
- Accounts:
  - Remote MCP only works for **Global accounts** (`name@global`).
  - Native-lab-only users use the local stdio server.
- Examples and tests use `jdoe`, `username@lab`, `admin@example.org`. Never use real names or emails.

## 7. Phases

Each phase lists its work, files, tests (keep them light), and release/deploy steps. Ship each phase before starting the next.

### Phase 0: print-free core (`remoterf.ops`) — the foundation, largest phase
- Create `src/remoterf/ops.py` (or the package `src/remoterf/ops/`) with pure functions over `RemoteRFAccount` and the HTTPS backend:
  `whoami()`, `list_devices()`, `find_slots()`, `list_reservations()`, `reserve()`, `cancel()`, `perms()`, `status()`, `device_api()`.
  - Return dataclasses or TypedDicts with JSON-ready values (ISO strings or epoch seconds, not `datetime`).
  - Raise `RemoteRFOpsError(code, message, retryable, retry_after)` with a stable code set. Reuse the existing codes
    (`route_unavailable`, `transport`, `principal_unknown`, `contract_not_active`, `invalid_credentials`, `not_found`, …)
    and the friendly text from `_FRIENDLY_ERRORS` in `app.py`.
- Move the logic out of `app.py`, not copy it:
  - native row parsing (`_native_device_rows`, the `_PARTNER_LABEL` and `_LOCAL_LABEL` regexes);
  - federated rows;
  - `_auto_block_minutes`;
  - slot building from `_pick_slot_and_reserve`;
  - the offline filtering added in 2.7.0.

  The shell then calls `ops` and keeps only rendering and prompts. Behaviour must not change.
- Native homes: slot conflict data comes from `get_reservations`. Keep the client-local-time alignment identical to `resdev`.
- Silence side-effect prints in library paths:
  - `rpc_client` `_finish` printing;
  - `_note` calls inside `grpc_acc.py`. Move them to the shell layer, or route them through `logging`.
- Tests: unit tests for parsing, slots and error mapping using fake backends (follow the patterns in `tests/test_device_routes.py`,
  which already fakes `account` and `session`), plus the existing shell tests unchanged. Run the touched test files plus the full suite once.
- Release: client minor version (2.8.0).

### Phase 1: scriptable CLI (`--json`, no prompts)
Add subcommands to `remoterf_cli.py`. Keep the existing flags working.
```
remoterf auth login [--home global|LAB] --user NAME --password-stdin [--accept-tos]
remoterf auth status --json          remoterf auth logout
remoterf devices [--lab L] [--all] --json
remoterf slots HANDLE [--days N] --json
remoterf reservations --json
remoterf reserve HANDLE --start ISO --end ISO --confirm [--reveal-token] --json
remoterf cancel RESERVATION_ID --confirm --json
remoterf api HANDLE [--json]         remoterf example HANDLE --recipe rx_once [-o file.py]
remoterf guide [TOPIC]               remoterf doctor [--json]
```
- JSON goes to stdout and human messages to stderr. No colour or spinner when stdout isn't a TTY.
- Exit codes: 0 ok, 2 usage, 3 auth needed, 4 not found, 5 conflict/unavailable, 6 permission, 7 lab unreachable,
  130 interrupted. Document them in `remoterf guide`.
- `--accept-tos` records consent the same way the interactive prompt does (`deployment/tos-agreed.json`). Show the ToS URL on stderr.
- Tests: CLI tests via `subprocess`/`main(argv)` with a fake backend. Assert JSON shape and exit codes.
- Release: 2.8.x, either together with Phase 0 or as a follow-up.

### Phase 2: docs that agents can find and trust
1. `remoterf guide`: a concise agent guide shipped as package data (`src/remoterf/data/agent_guide.md`), so it always matches the installed version.
   - Workflow: login → devices → slots → reserve → `api`/`example` → run Python → cancel.
   - Token and handle rules, the exit codes, and the safety rules.
2. `remoterf api HANDLE`: from the server IDL (`fetch_idl` / `fetch_schema_v2`), with no reservation needed.
   - Output: the import line, class, constructor, and methods with docs.
   - The import mapping is the one already in `_print_usage` / `DRIVER_IMPORTS` in `app.py`. Move it into `ops`.
3. `remoterf example HANDLE --recipe …`: receive-only recipes only, built from the IDL. They read `REMOTERF_TOKEN`, and close in `finally`.
4. `remoterf skill install [--claude|--codex|--gemini|--agents-md]` writes the guide where each tool looks:
   - Claude Code skill: `~/.claude/skills/remoterf/SKILL.md`, with `name` and `description` frontmatter;
   - `AGENTS.md` snippet for Codex and others;
   - `GEMINI.md` for Gemini CLI.

   Print exactly what was written.
5. Website (wireless-lab-at-ucla.gitlab.io):
   - Refresh `docs/client/usage.md` for 2.7+ (boxes, handles, `rfg_` tokens, Global, `remoterf.devices()`).
   - Add an "AI assistants" page with setup snippets for every app in section 2.
   - Generate `/llms.txt` (an index of doc URLs with one-line summaries) and `/llms-full.txt` (all client docs concatenated) at build time with a Jekyll template,
     for example `llms.txt` with `layout: null` iterating `site.pages` under `/docs/client/`.
   - Check it on the GitLab Pages output.
- Tests: guide renders; `api`/`example` against a fake IDL; generated example compiles (`compile()`); skill install writes to a temp HOME.

### Phase 3: local MCP server (`remoterf mcp`, stdio)
- `src/remoterf/mcp_server.py` uses FastMCP and exposes the section 5 tools and resources over `ops`. Entry point: `remoterf mcp [--allow-reserve]`.
  Optional dependency: `[project.optional-dependencies] mcp = ["mcp>=1.x"]`. Pin after checking the current SDK release. If the
  extra is missing, print an install hint.
- It must never write to stdout except MCP frames. Logging goes to stderr. Audit every library path for stray `print`.
- `remoterf mcp install --claude-code|--claude-desktop|--codex|--gemini|--cursor` writes or prints the config entry for each app:
  - Claude Code: `claude mcp add remoterf -- remoterf mcp`
  - Claude Desktop: `claude_desktop_config.json` → `mcpServers.remoterf = {command, args}`
  - Codex CLI: `~/.codex/config.toml` `[mcp_servers.remoterf]`
  - Gemini CLI: `~/.gemini/settings.json` `mcpServers.remoterf.command`
  - Cursor/VS Code: their `mcp.json`

  Use the absolute path of the current interpreter's `remoterf` script. Users often have several Pythons and envs, so never rely on bare `remoterf` being on PATH for the AI app.
- Tests: the MCP SDK's in-memory client session against the server with a fake `ops`. Check list_tools, call each read tool, and check that
  reserve is refused without `--allow-reserve`. Also run one manual smoke test with the MCP Inspector (`npx @modelcontextprotocol/inspector remoterf mcp`).
- Release: client 2.9.0.

### Phase 4: remote MCP server on Global (ChatGPT, claude.ai, Gemini app)
Work in `remoterf-vps-global`.
1. **OAuth 2.1 authorization server** in the deployment service (`src/remoterf_global/deployment/`), backed by the existing Global accounts and password check:
   - `/.well-known/oauth-protected-resource` (RFC 9728), with `authorization_servers` and `scopes_supported`.
   - `/.well-known/oauth-authorization-server` (RFC 8414).
   - `/oauth/authorize`: a minimal HTML login + consent page (server-rendered, CSRF token, no JS framework), authorization-code + PKCE S256.
   - `/oauth/token`: code exchange and refresh with rotation. Tokens are opaque and stored hashed, or are signed JWTs with `aud` = the MCP resource.
   - Client registration:
     - Dynamic Client Registration `/oauth/register` (RFC 7591), which Claude and ChatGPT use today;
     - also support **Client ID Metadata Documents**, which newer MCP auth spec revisions prefer.

     Check the current spec revision at implementation time.
   - Scopes: `remoterf.read`, `remoterf.reserve`.
   - New tables via an alembic migration. Follow the broker/deployment migration rules: back up the DB first, run
     `alembic upgrade head` with the new image before `up -d`. The pinned head lives in `src/remoterf_global/db/session.py` and `tests/test_migrations.py`.
2. **`/mcp` endpoint** (Streamable HTTP, stateless mode is fine) using the MCP SDK's resource-server auth:
   - Validate the bearer token and its audience/resource on every call.
   - Return 401 with `WWW-Authenticate: Bearer resource_metadata="…"` when the token is missing or invalid.
   - Tools call `GlobalHomeDevices.list_devices/availability/list_reservations`, the reserve/cancel services, and the device IDL fetch through the HOME's normal paths.
   - Keep tool names and JSON identical to section 5.
   - `remoterf_example_script` returns code for the user to run locally. The remote server cannot reach their machine.
3. **Edge routing**: route `/mcp`, `/oauth/`, `/.well-known/oauth-*` to the deployment service. Check memory headroom with `vpsrf stats`
   (Droplet health card) after deploying. The box has ~1 GB RAM.
4. **Connected-apps management**: list and revoke grants, through a JSON endpoint plus `remoterf auth apps` in the client.
5. Tests:
   - OAuth flow unit tests (PKCE verify, code reuse rejected, refresh rotation and replay revocation, wrong audience rejected, scope enforcement);
   - an `/mcp` tool-call test with a fake HOME.

   Keep them focused, then run the full vps-global suite once.
6. Deploy (Global VPS, as done before for this stack):
   - commit and push on `feat/global-device-federation`;
   - tar the build context (`Dockerfile dependencies scripts pyproject.toml alembic.ini src docker-entrypoint.sh .build/wheelhouse`,
     with `COPYFILE_DISABLE=1`, excluding `._*` and `__pycache__`) to `/root/remoterf-v2-build/<sha>`;
   - `docker build -t remoterf-v2-validation-global:<sha>`;
   - back up `compose.yml` to `/root/remoterf-v2-live-rollbacks/` and edit the service image line;
   - run the migration, then `docker compose -f compose/compose.yml up -d deployment`;
   - verify with `vpsrf status`.
7. Live verification, each with a test Global account (create one and delete it afterwards):
   - **claude.ai**: Settings → Connectors → Add custom connector → `https://global.remoterf.net/mcp`. Sign in, list devices, and reserve then cancel a slot on a **non-production test device**.
   - **ChatGPT**: Settings → Connectors → Advanced → Developer mode → add the URL. Run the same checks. Note which plans allow write tools.
   - **Gemini app**: Settings & help → Connected apps → Custom apps for Spark → add the URL (needs a personal Google account). Run the same checks.
   - **Gemini CLI remote**: `httpUrl` entry.
   - Record the results in this file.

### Phase 5: polish and launch
- Re-verify each client's current MCP docs (plans, auth spec revision, transport), and update section 2 and the website page.
- Optional: submit to the Claude connector directory, the ChatGPT app directory and Gemini. Each has review requirements; read them first.
- Add `/remoterf stats` (vpsrf) counters for MCP usage: calls per day and connected apps.

### Deferred, only with evidence of need (do not build in v1)
- A bounded receive-only "quick look" tool on the remote server (HOME → lab `device_rpc`, small sample count, returns summary
  statistics or a spectrum PNG). It needs per-device safety limits and a server-side reservation it opens and closes itself.
- Long-lived personal access tokens for unattended agents (needs a Global server design for scope and expiry).
- Any TX capability.

## 8. Acceptance criteria (v1 = Phases 0–4)
- **Shell:** `remoterf -l` behaves exactly as in 2.7.0. Existing tests pass at the 2.7.0 baseline.
- **CLI:** every new command works with no TTY and no prompts, prints valid JSON with `--json`, and returns the documented exit codes.
- **Mutations:**
  - nothing reserves without `--confirm` (CLI), `--allow-reserve` (local MCP) or the `remoterf.reserve` scope (remote);
  - no mutation is retried automatically.
- **API and examples:** `remoterf api` and `remoterf_device_api` work without a reservation. Example scripts compile and contain the correct import.
- **Local MCP:** works in Claude Code, Claude Desktop, Codex CLI and Gemini CLI, configured via `remoterf mcp install`.
- **Remote MCP:**
  - connects and completes OAuth from claude.ai, ChatGPT developer mode and the Gemini app (Spark);
  - tokens are audience-bound;
  - the consent page shows scopes;
  - grants are revocable.
- **Docs:** `remoterf.net/llms.txt` and `/llms-full.txt` are live; the client docs are current; the "AI assistants" page has setup snippets for every app.
- **Tokens:** no reservation token appears in remote tool output unless the user explicitly asked for it.

## 9. Working rules for whoever implements this
- Smallest correct change per step. Follow the surrounding code style.
- Commits:
  - concise messages, no AI attribution lines;
  - commit and push only when the owner asks, or when a release step requires it (the owner has asked for full releases and deploys of finished work).
- Releases:
  - client: bump `pyproject.toml`, `.venv/bin/python -m pip wheel . --no-deps --no-build-isolation -w dist`, `twine check`, `twine upload --non-interactive`;
  - server: the same pattern;
  - PyPI's index can lag a few minutes after upload.
- Use cheaper subagents (Sonnet, low/medium effort) for parallel, mechanical work, and keep verification light unless told otherwise.
- rrf2 is production: no restarts during active reservations without the owner's OK; back up before DB changes.
- Use example names like `jdoe` and `username@lab`; never real people's names or emails.

## 10. Open decisions for the owner
1. Remote token disclosure: return the `rfg_` token in cloud chats when asked, or only the handle form plus a local `remoterf reveal ID`?
2. Should write tools (reserve and cancel) ship in the first remote release, or should the remote server be read-only first?
3. Directory listings (Claude, ChatGPT, Gemini): pursue now or after usage data?
4. Native-lab-only accounts (no Global account): local MCP only (proposed), or also issue OAuth through each lab later?
