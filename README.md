# ayarlicazhocam

`ayarlicazhocam` is a local, database-backed productivity assistant for engineering work.

## Project layout

- `agent/`: provider adapters, prompt, and tool-call orchestrator.
- `database/`: SQLite connection, schema, seed data, and repository helpers.
- `services/`: task validation and task-domain operations.
- `tools/`: LLM-facing schemas and thin task tool adapters.
- `gradio_app.py`: Gradio application entry point.

The current implementation supports creating, listing, and updating tasks via
Groq or Google Gemini. The Gradio app exposes chat, the current task table,
database statistics, and an execution trace in one desktop-friendly view.

## What is implemented

- Task tools: `create_task`, `get_tasks`, and `update_task`.
- Groq and Gemini provider adapters behind one agent interface.
- A bounded tool-calling loop with normalized tool results and execution
  telemetry.
- SQLite persistence with schema initialization and idempotent demo seeding.
- A Gradio presentation layer with chat, task overview, statistics, trace,
  refresh, and clear-conversation controls.

Daily planning, work-session logging, progress reviews, and the custom Gemma
chat template are planned work; they are not represented as available features.

## Architecture

```text
Gradio UI (gradio_app.py)
        |
        v
Agent / Orchestrator (agent/)
        |
        v
Task tools (tools/)
        |
        v
Task service (services/)
        |
        v
Repository helpers (database/)
        |
        v
SQLite (data/ayarlicazhocam.db)
```

The UI is presentation-only. Chat requests enter the agent; task writes occur
only through tools, services, and the repository. Its read-only task display
uses the existing task tool and repository read helper to turn project IDs into
project names. No database reset control is included.

See [the current architecture document](docs/current-architecture.md) for the
implemented module boundaries and runtime flow.

## Requirements

- Python 3.11+
- A Groq or Google Gemini API key for chat

Install the dependencies in a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

On macOS or Linux, activate with `source .venv/bin/activate` instead.

## Configure the application

Copy the example configuration, then add the key for the provider you choose:

```powershell
Copy-Item .env.example .env
```

| Variable | Purpose | Default |
| --- | --- | --- |
| `MODEL_PROVIDER` | Provider to use: `groq` or `gemini` | `groq` |
| `GROQ_API_KEY` | Required when using Groq | — |
| `GEMINI_API_KEY` | Required when using Gemini | — |
| `GROQ_MODEL` | Optional Groq model override | `llama-3.3-70b-versatile` |
| `GEMINI_MODEL` | Optional Gemini model override | `gemini-1.5-flash` |
| `DATABASE_PATH` | SQLite database file | `data/ayarlicazhocam.db` |
| `GRADIO_SERVER_NAME` | Gradio bind address | `127.0.0.1` |
| `GRADIO_SERVER_PORT` | Gradio port | `7860` |

Keep `.env` private. It is ignored by Git; `.env.example` contains placeholders
only.

## Run the app

```powershell
python gradio_app.py
```

Open `http://127.0.0.1:7860` unless you changed the host or port. Startup
creates the configured SQLite database when necessary and inserts demo projects
and tasks only when the projects table is empty. It never resets existing data.

The footer shows the configured provider, model, and resolved SQLite path.

## Using the interface

- Enter a request in the chat box and press **Send** or Enter.
- Inspect the execution trace to see provider/model telemetry, each tool's
  arguments, result, status, and duration.
- Use **Refresh** to reload the task table and statistics from the current
  database state.
- Use **Clear Conversation** to remove chat and trace history; it does not
  delete or modify database records.

The task overview resolves project IDs to project names for readability. The
statistics panel reports projects, total tasks, completed, in-progress, blocked,
and overdue tasks.

## Example prompts

The assistant accepts Turkish or English. For example:

- `Görevlerimi listele`
- `Yüksek öncelikli görevleri göster`
- `Gecikmiş görevler var mı?`
- `Face Login projesi için README taslağı görevi oluştur`
- `1 numaralı görevi tamamlandı olarak işaretle`

For task creation and updates, the model calls the relevant tool and confirms
the result only after the tool reports success. A project must already exist;
task creation does not create projects implicitly.

## Data integrity and tool responses

SQLite is the source of truth for tasks. The system prompt requires a tool call
for claims about stored tasks, deadlines, or status, and the agent returns tool
errors rather than inventing results. Every tool uses this envelope:

```json
{
  "success": true,
  "data": {},
  "message": "Human-readable result",
  "error": null
}
```

Failures use `success: false` and include a stable error code such as
`TASK_NOT_FOUND`, `PROJECT_NOT_FOUND`, or `VALIDATION_ERROR`.

## Tests

Run the automated tests with:

```powershell
pytest tests -v
```

The suite covers the database layer, task-service validation, tool envelopes,
orchestrator behavior, and provider request/response normalization without
requiring live provider calls.

## Limitations and next steps

- The currently exposed tools cover tasks only; planning, review, habit, and
  work-log features have not yet been implemented.
- A configured provider key and network access are required for live chat.
- The local SQLite database is designed for a single-user local process.

## License

Licensed under the [Apache License 2.0](LICENSE).
