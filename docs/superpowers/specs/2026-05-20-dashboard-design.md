# Agent-Hub Live Dashboard

**Status:** Design
**Date:** 2026-05-20
**Author:** brainstorming session

## Problem

Agent-hub is driven entirely from Telegram today. That works for filing tasks and approving gates, but it's a bad surface for *watching* — the same chat where you compose commands also scrolls past every agent's reply, tool call, and handoff. You can't glance and absorb "what's happening right now."

Specifically, there's no way to:

- See at a glance which tasks are running, who owns them, and how long they've been alive.
- See which design gates are waiting for `/approve` without scrolling chat history.
- Watch the live stream of agent activity (tool calls, handoffs, comments) as it happens, decoupled from the chat where you're typing.
- Drill into a single task's full timeline without `/task <id>` + reading a flat text reply.

## Goals

1. A single-page **live activity monitor** at `http://localhost:8765` that shows everything the agents are doing in real time.
2. Read-only — no control surface. Telegram stays the only place commands are issued.
3. Bundled into agent-hub itself — no separate process, no separate deployment, no external infrastructure.
4. Updates the moment the orchestrator writes to SQLite — no polling lag.
5. Visual style: cinematic dark "Mission Console" — telemetry HUD, monospace numerals, sweeping scanlines, agent-color glow, animated bars. Looks alive.

## Non-goals

- Authentication / authorization (localhost only, single user).
- Control surface — approving gates, filing tasks, sending messages. All Telegram.
- LAN or internet exposure.
- Historical task browsing / archaeology (separate cycle if wanted).
- Multi-workspace simultaneous view (current workspace only).
- Charts / sparklines / time-series graphing.
- Sound effects.
- Theme toggle (dark by design).

## Architecture

```
agent-hub bot process (Python, asyncio)
├── Orchestrator + Telegram bot (existing)
├── SQLite (existing)
└── Dashboard subsystem (NEW)
    ├── DashboardBroker    — in-process pub/sub
    ├── DashboardServer    — aiohttp app on 127.0.0.1:8765
    └── static/index.html  — single self-contained page
```

Three layers:

1. **HTTP server** — `aiohttp` app, four routes (`/`, `/api/state`, `/api/events`, `/api/task/<id>`), bound to `127.0.0.1:8765` only. Runs as a task in the bot's event loop.
2. **State broker** — in-process pub/sub. Repos call `broker.publish(...)` after each commit; HTTP subscribers drain a per-connection async queue.
3. **Frontend** — one HTML file with inlined CSS + JS. No build step, no node_modules, no asset routes.

## Data model

The broker emits four event kinds:

| Kind | Emitted by | Payload |
|---|---|---|
| `task_changed` | `TaskRepository.create` / `update` after commit | Full updated task row as dict + `workspace` |
| `task_event` | `TaskRepository.comment` after commit; `HandoffQueue.enqueue` after commit | Event row + parent task + `workspace` |
| `gate_changed` | `GateRepository.request` / `resolve` / `notify` after commit | Gate row + parent task + `workspace` |
| `workspace_changed` | `Database.set_active_workspace` after commit | New workspace path; tells clients to re-fetch `/api/state` |

A **state snapshot** (returned by `/api/state`) is a single JSON document:

```json
{
  "workspace": "C:\\dev\\your-project",
  "stats": {"running": 2, "pending": 1, "done_24h": 7, "queue": 0},
  "active_tasks": [
    {"id": 7, "title": "...", "status": "running", "owner": "quant", "elapsed_seconds": 271}
  ],
  "pending_gates": [
    {"id": 4, "task_id": 6, "kind": "design", "requested_at": "..."}
  ],
  "recent_events": [
    {"id": 142, "ts": "...", "task_id": 7, "actor": "fullstack", "kind": "tool_use", "summary": "Edit ea_stops.mq5"}
  ]
}
```

The snapshot is computed by reading the same tables the broker watches (`tasks`, `gates`, `task_events`), filtered to `workspace == Database.get_active_workspace()`.

## Broker

`agent_hub/dashboard/broker.py` defines a singleton:

```python
class DashboardBroker:
    async def subscribe() -> AsyncIterator[Event]: ...
    async def publish(event: Event) -> None: ...
    async def snapshot() -> StateSnapshot: ...
```

- Each subscriber has a per-connection `asyncio.Queue(maxsize=100)`.
- `publish()` enqueues to every subscriber. If a queue is full, the subscriber is dropped — the browser will reconnect and re-fetch `/api/state`.
- The broker has no persistence. Events not delivered to live subscribers are lost; SQLite is the source of truth.

**Injection pattern:** the broker is a process-wide module-level singleton in `agent_hub/dashboard/broker.py`. Set during `__main__.py` startup; repositories call `get_broker()` and skip publishing if `None`. This keeps repos test-friendly (no broker needed in tests) and the dependency one-way (repos import the broker module; broker doesn't import repos).

## HTTP server

`agent_hub/dashboard/server.py` defines:

```python
class DashboardServer:
    def __init__(self, broker: DashboardBroker, db: Database, port: int): ...
    async def start() -> None: ...
    async def stop() -> None: ...
```

**Routes:**

| Route | Behavior |
|---|---|
| `GET /` | Returns the single-page HTML, read once at startup. `Cache-Control: no-store`. |
| `GET /api/state` | Returns `await broker.snapshot()` as JSON. Filtered to active workspace. |
| `GET /api/events` | SSE stream. Subscribes to broker, drains queue, formats `data: <json>\n\n`. Sends `: ping\n\n` every 15s. Disconnect cleans up subscriber. Filters by active workspace. |
| `GET /api/task/<id>` | Returns the full event timeline for one task (used by inline-expand). Filtered by active workspace. |

**Bind:** `127.0.0.1` only. Localhost was the explicit scope.

**Configurable port:** `DASHBOARD_PORT` env var, default `8765`. Setting it to `0` disables the dashboard entirely (server isn't started).

**Lifecycle:** `__main__.py` starts the server alongside the orchestrator. If `bind` fails (port in use), log a warning and continue — the bot still works through Telegram.

## Frontend

`agent_hub/dashboard/static/index.html` — one file, ~600 lines, no build step.

**Bootstrap:**

```js
1. fetch('/api/state') → render initial DOM
2. new EventSource('/api/events')
3. on each message: parse, apply delta to DOM
4. on error: backoff 0.5s → 2s → 8s, then reconnect + re-fetch /api/state
```

**DOM structure:**

```
<header>     brand · workspace · connection status
<hud-strip>  4 cells: running · pending · done 24h · cost (hover)
<active-panel>   pulsing-dot task list
<pending-panel>  amber-highlighted gates waiting on /approve
<stream-panel>   live event feed, capped at 50 rows
```

**Visual style — Mission Console:**

| Token | Hex | Use |
|---|---|---|
| `--bg-deep` | `#060a14` | Page background |
| `--bg-mid` | `#0a1020` | Top of gradient |
| `--cyan` | `#00c8ff` | Brand accent, running status, grid lines |
| `--cyan-glow` | `#00ffae` | Active-task highlights, "good" HUD cells |
| `--amber` | `#ffd84d` | Pending decision, warn HUD cells |
| `--magenta` | `#ff5d6c` | Blocked / failed states |
| `--text` | `#d8e3f5` | Body text |
| `--text-dim` | `#8aa0c5` | Metadata, timestamps |
| `--grid` | `rgba(0,200,255,0.06)` | Background grid lines |

Typography: `"SF Mono", "Cascadia Code", "JetBrains Mono", ui-monospace, monospace`. Numbers use `font-feature-settings: 'tnum'` for stable column alignment. Unicode glyphs only (`›› · ●`) — no icon fonts.

**Five running animations:**

1. **Sweeping scanline** — full-page 4px-tall band, transparent → cyan tint 6% → transparent, `translateY(-100%)` → `translateY(100%)` over 6s, infinite, `pointer-events: none`.
2. **Pulsing active dots** — 1.4× scale + opacity dip on a 1s cycle; status-colored `box-shadow` glow.
3. **Bar fills** — 60px-wide track per active task; bar slides indefinitely. Per-task `animation-delay` so they don't sync.
4. **Streaming feed rows** — new events insert at top with 0.4s slide-in + fade. Cap at 50; oldest fades out.
5. **Number count-up** — HUD cell values cross-fade between old and new on change, 0.4s.

**Interactive behavior:**

- **Inline expand on click:** clicking an active task row morphs it into a card showing the task's full event timeline (fetched via `GET /api/task/<id>`). Click again to collapse. Only one task expanded at a time. CSS `max-height` + opacity transitions.
- **Cost hover:** the cost HUD cell renders as a `$` icon by default. Hover → animates open to reveal "TODAY $X.XX · TASK #N $X.XX". Mouseout → collapses back. Cost values aggregated from `tasks.cost_usd_total`.
- **Reconnect indicator:** small dot next to brand title — green pulse on connect, red on dropped SSE.

**Accessibility:**

- Status colors paired with glyphs (●, ◆, ▲) so colorblind users can distinguish states.
- `prefers-reduced-motion: reduce` disables scanline, bar fills, and pulsing dots — static layout remains.
- Tab order: header → HUD cells → active tasks (clickable) → pending → stream rows.

## File plan

**New files:**

| File | Responsibility |
|---|---|
| `agent_hub/dashboard/__init__.py` | Package marker |
| `agent_hub/dashboard/broker.py` | `DashboardBroker`, snapshot builder, module-level singleton helpers |
| `agent_hub/dashboard/events.py` | `Event` dataclasses + JSON serializers |
| `agent_hub/dashboard/server.py` | aiohttp app, 4 routes, SSE handler, bind/lifecycle |
| `agent_hub/dashboard/static/index.html` | Single self-contained page |

**Modified files:**

| File | Change |
|---|---|
| `agent_hub/__main__.py` | Start `DashboardServer` alongside the orchestrator; stop on shutdown |
| `agent_hub/config.py` | Add `dashboard_port: int` (default 8765, `0` disables) |
| `agent_hub/tasks/repository.py` | After each commit in `create`/`update`/`comment`, call `get_broker().publish(...)` if broker set |
| `agent_hub/tasks/gates.py` | Same — publish on `request`/`resolve`/`notify` |
| `agent_hub/tasks/handoff_queue.py` | Publish on `enqueue` |
| `agent_hub/db.py` | Publish `workspace_changed` from `set_active_workspace` after commit |
| `requirements.txt` | Add `aiohttp>=3.9` |
| `.env.example` | Add `DASHBOARD_PORT=8765` |
| `README.md` | One paragraph mention of `http://localhost:8765` |

## Testing

| Test file | Coverage |
|---|---|
| `tests/test_dashboard_broker.py` | Subscribe → publish → receive. Multiple subscribers each get every event. Queue overflow drops the slow subscriber. Snapshot reads return the expected shape. |
| `tests/test_dashboard_server.py` | `aiohttp` test client: `GET /` returns 200 + HTML. `GET /api/state` returns expected JSON shape. `GET /api/events` opens SSE; publishing an event delivers it to the connected client. Workspace filter excludes events outside active workspace. |
| `tests/test_dashboard_repo_publish.py` | `TaskRepository.create()` triggers `task_changed`. `GateRepository.resolve()` triggers `gate_changed`. `comment()` and `HandoffQueue.enqueue()` trigger `task_event`. |
| `tests/test_dashboard_lifecycle.py` | `DashboardServer.start()` binds to the right port; `stop()` releases it. Port conflict → server logs warning + doesn't crash. `DASHBOARD_PORT=0` skips startup entirely. |

No frontend unit tests — the HTML is a single file served as-is. Behavior is verified by manual sanity (open the page while bot runs).

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Broker queue overflow on a slow browser → memory leak | Per-subscriber `maxsize=100`; on full, drop subscriber; browser reconnects + re-snapshots |
| SSE dropped by Windows proxy / sleeping tab | 15s keepalive pings; client backoff + reconnect; visible red dot on disconnect |
| Port 8765 already in use | Log warning, continue without dashboard; bot stays functional through Telegram |
| Broker import / publish breaks repos in tests | `get_broker()` returns `None` when unset → repos skip publish |
| Frontend JS error breaks the page | Single file, plain DOM, no build — easy to debug in browser devtools. SSE reconnect is the only stateful part |
| Workspace switch in Telegram desyncs dashboard | The HTTP layer reads active workspace per request; the next `/api/state` after switching reflects new workspace. SSE clients re-fetch on a workspace-change event (new event kind `workspace_changed`) |

## Out of scope for MVP

- Auth / authorization
- LAN / remote access
- Control surface (approve, file, message)
- Historical browsing / archaeology
- Multi-workspace simultaneous view
- Charts, sparklines, graphs
- Sound effects
- Theme toggle
- Frontend unit tests
- Persisting subscriber state across server restarts
