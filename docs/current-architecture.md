# Current Architecture

> **Status: Gradio UI complete.** This document describes the implemented
> repository, not the future-state product specification in `AGENTS.md`.
> The active application entry point is `gradio_app.py`.

## System boundaries

The application keeps a one-way layered dependency flow:

```text
Gradio presentation -> Agent -> Tools -> TaskService -> Repository -> SQLite
```

- `gradio_app.py` is presentation-only. It renders Gradio components, invokes
  `Orchestrator.run()` for chat, and refreshes read-only display data. It has no
  task validation or mutation logic.
- `agent/` owns provider abstraction, message conversion, the bounded tool-call
  loop, and per-request execution telemetry.
- `tools/` exposes the model-facing task schemas and converts service outcomes
  to deterministic tool envelopes.
- `services/` owns task validation, filtering, and task-domain operations.
- `database/` owns SQLite connection management, schema setup, seed data, and
  parameterized repository helpers.

The model never reads or writes SQLite directly. SQLite remains the source of
truth for tasks, projects, and progress data.

## Implemented entry point and startup

`python gradio_app.py` performs the following safe startup sequence:

1. `initialize_database()` creates tables and indexes if they do not already
   exist.
2. `seed_database()` adds demo data only when the `projects` table is empty.
3. `build_ui()` creates the Gradio Blocks interface.
4. Gradio starts with its built-in `Soft` theme plus `GRADIO_SERVER_NAME` and
   `GRADIO_SERVER_PORT` (default `127.0.0.1:7860`).

Neither startup nor the UI includes a database reset action. Existing data is
not deleted by the application.

## Gradio UI

The completed UI contains two columns:

- **Conversation column:** a message-format `Chatbot`, a text input, **Send**,
  and **Clear Conversation**.
- **Observability column:** accordions for **Execution Trace**, **Current
  Tasks**, and **Database Status**, plus a **Refresh** button.

### Chat flow

1. `on_send` passes the user's text and current conversation history to
   `respond`.
2. `respond` obtains the cached `Orchestrator` and calls `Orchestrator.run`.
3. The returned assistant text and formatted `AgentResult` trace are appended
   to the chat and trace components.
4. After every chat turn, the task table and statistics are reloaded because a
   tool call may have changed task data.

Provider errors and unexpected runtime failures become user-facing friendly
messages. Stack traces are not rendered in the UI.

### Read-only dashboard data

- `_load_task_rows()` obtains task records through `get_tasks_tool()`.
- `_load_project_names()` uses the existing repository `fetch_all` read helper
  to map task `project_id` values to project names for the table.
- `_load_stats_markdown()` derives display counts from tool results and the
  project-name lookup. It reports projects, total tasks, completed,
  in-progress, blocked, and overdue tasks.
- **Refresh** calls `on_refresh`, which reloads only the task table and
  statistics.

These display reads do not add business rules, perform writes, or bypass the
task service for task data.

### Clear and footer behavior

`on_clear` returns the exact five outputs used by `send_outputs`, in the same
order: chat history, trace, task rows, statistics, and message-box value. It
clears conversation state while reloading the two read-only panels; it never
alters the database.

The footer calls `resolve_db_path()` and displays the configured provider,
model, and resolved SQLite database path on separate, clearly labelled lines.

## Chat Template Layer

The fine-tuned Gemma model uses a standalone serialization layer that is kept
outside the runtime backend:

```text
message dictionaries
        |
        v
templates/chat_template.jinja
        |
        v
Tokenizer.apply_chat_template()
        |
        v
Gemma prompt
```

`templates/chat_template.jinja` follows Gemma's
`<start_of_turn>…<end_of_turn>` convention. It accepts `system`, `user`,
`assistant`, and `tool` messages. Because Gemma prompts have user/model turns,
an initial system message is embedded in the first user turn rather than emitted
as an unsupported standalone turn.

Assistant function requests are serialized as JSON inside `<tool_call>` blocks.
Tool results remain distinguishable from assistant text as JSON inside
`<tool_response>` blocks, grouped within a user turn when there are consecutive
tool results. The optional `tools` argument is rendered in a `<tools>` block.

The layer adds no application business logic and does not call the database,
agent, providers, tools, or Gradio UI. Consumers load a Gemma tokenizer,
assign the template string to `tokenizer.chat_template`, and call
`tokenizer.apply_chat_template(messages, ...)`. The offline integration tests
use a local fast tokenizer to exercise that exact Transformers API without
requiring access to a gated Gemma checkpoint.

## Agent and providers

`agent/orchestrator.py` implements a provider-independent, bounded tool loop.
It uses the canonical OpenAI-style message shape internally, dispatches tools
through one registry, feeds each complete tool envelope back to the provider,
and stops after `MAX_TOOL_DEPTH` rounds.

`agent/providers.py` supplies `GroqProvider` and `GeminiProvider`. Each
normalizes provider replies into `ProviderResponse` and surfaces failures as
`ProviderError`. Provider SDK imports are lazy; unit tests can inject clients
without live API keys.

For every run, `ExecutionLog` records provider requests, model names when
reported, tool order, tool success state, tool duration, total latency, and
available token usage. The Gradio trace is a presentation of this result.

## Task tools and service

The currently registered model tools are:

| Tool | Operation |
| --- | --- |
| `create_task` | Create an explicitly requested task for an existing project. |
| `get_tasks` | Read tasks with optional status, priority, project, and overdue filters. |
| `update_task` | Update supplied fields for an existing task. |

`TaskService` is the authoritative home for task validation and persistence
rules. It validates IDs, enum values, ISO dates, and non-negative durations;
it also prevents implicit project creation. Tools catch domain failures and
return the standard envelope instead of raising.

```json
{
  "success": true,
  "data": {},
  "message": "Human-readable result",
  "error": null
}
```

## Persistence

The SQLite schema has five tables: `projects`, `tasks`, `daily_plans`,
`daily_plan_items`, and `work_logs`. The task-related UI and tools currently
operate on the implemented task domain; daily-plan and work-log user workflows
are not yet exposed.

`database/repository.py` provides the generic parameterized `execute`,
`execute_many`, `fetch_one`, and `fetch_all` helpers. SQL error translation,
foreign-key enforcement, and connection lifecycle are owned by `database/`,
not the UI or agent.

## Testing and remaining work

Run `pytest tests -v` to execute database, service, tool, orchestrator, and
provider tests. These tests use temporary databases or injected provider clients
and do not require a live API key.

The remaining roadmap work is intentionally outside the completed UI scope:
daily planning, work-session logging, progress reviews, habits, broader
hallucination scenarios, and deployment documentation.
