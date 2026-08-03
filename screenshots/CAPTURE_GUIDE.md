# Screenshot Capture Guide

Follow this once, top to bottom, to produce all 12 screenshots in **under 5
minutes**. Nothing here is fabricated — every shot is a real frame from the
running app. Save each file into this `screenshots/` folder using the exact
name shown.

## Before you start

1. Ensure `.env` is configured and a provider key is set (see the main README).
2. Use a **fresh demo database** so IDs match this guide:
   ```powershell
   Remove-Item data\ayarlicazhocam.db -ErrorAction SilentlyContinue
   ```
3. Confirm `MODEL_PROVIDER=groq` in `.env` for shots 01–11.

## Session A — Groq (shots 01–11)

Launch the app, then open `http://127.0.0.1:7860`:

```powershell
python gradio_app.py
```

Send the prompts below **in order**. After each response settles, capture the
described frame. Task IDs assume the fresh seed (5 demo tasks → your new task
becomes **#6**).

| # | File | Exact prompt to send | Expected tool call(s) | Expected assistant response | Task-table change | Execution-trace panel | What must be visible in the frame |
|---|------|----------------------|-----------------------|-----------------------------|-------------------|-----------------------|-----------------------------------|
| 01 | `01-home.png` | *(none — capture first load)* | *(none)* | Empty chat with the placeholder prompt | 5 seeded tasks (Design the SQLite schema … Deploy the portfolio site) | `No activity yet.` | Full window: title, Assistant Chat (empty), System Overview (Provider `groq`), Task Statistics (Total Tasks 5), Recent Tasks (5 rows) |
| 02 | `02-create-task.png` | `Create a task called "Finish README" with high priority.` | `create_task` | Confirms creation of task **#6**, high priority | New row **#6 Finish README / high / todo** appears | Tool Call #1 `create_task`, arguments incl. `title`, Result: `success` | Chat turn + Execution Trace showing the `create_task` call and success |
| 03 | `03-list-tasks.png` | `Show all my tasks.` | `get_tasks` | Numbered list of all 6 tasks | Unchanged (6 rows) | Tool Call `get_tasks`, Result: `success — Found 6 task(s).` | Chat listing all 6 tasks, trace showing `get_tasks` |
| 04 | `04-update-task.png` | `Update task 6 to critical priority.` | `update_task` | Confirms task 6 is now critical | Row #6 priority changes **high → critical** | Tool Call `update_task`, arguments `{"task_id":6,"priority":"critical"}`, Result: `success` | Chat confirmation + updated table row #6 = critical |
| 05 | `05-complete-task.png` | `Mark task 6 as completed.` | `update_task` | Confirms task 6 completed | Row #6 status changes **todo → completed** | Tool Call `update_task`, arguments `{"task_id":6,"status":"completed"}`, Result: `success` | Chat confirmation + table row #6 = completed |
| 06 | `06-tool-trace.png` | *(reuse the trace from shot 05; no new prompt)* | — | — | — | Header shows Provider `groq` + Model; each tool call is a clean entry with **Tool**, **Status** (Success), **Duration**, **Arguments**, **Result**; footer shows Total latency + Tokens | The **Execution Trace** panel filling the frame |
| 07 | `07-task-table.png` | *(no new prompt)* | — | — | 6 rows, #6 = Finish README / Critical / Completed | — | The **Recent Tasks** panel, all columns (ID, Task, Project, Status, Priority, Due Date, Est. (min)) |
| 08 | `08-statistics.png` | *(no new prompt)* | — | — | — | — | The **Task Statistics** panel: Total Tasks 6, Completed 1, In Progress 0, Blocked 0, Overdue 0, Projects 3 |
| 09 | `09-hallucination-test.png` | `What tasks did I complete in January 2023? List them.` | `get_tasks` (or none) | Assistant states it has **no record** of such completions / cannot determine — **invents nothing** | Unchanged | Trace shows `get_tasks` returning real data (or no tool), no fabricated rows | Chat reply that grounds on the DB and refuses to invent Jan-2023 history |
| 10 | `10-error-handling.png` | `Update task 999999 to completed.` | `update_task` | Reports the task was **not found** — no false success | Unchanged | Tool Call `update_task`, Result: `error [TASK_NOT_FOUND]` | Chat honest failure + trace line showing the `TASK_NOT_FOUND` error envelope |
| 11 | `11-groq-provider.png` | *(reuse any successful turn above)* | — | — | — | Provider `groq`, Model `llama-3.3-70b-versatile`, non-zero latency | Trace header **and** the **System Overview** panel both showing provider `groq` with a successful call |

> Tip: shots 06, 07, 08, 11 need **no new prompt** — they are just focused
> captures of panels already on screen after shot 05, so they cost seconds.

## Session B — Gemini (shot 12)

Stop the app (`Ctrl+C`). Switch the provider and relaunch:

```powershell
# In .env set:  MODEL_PROVIDER=gemini
# (Use a Gemini model your key has quota for, e.g. GEMINI_MODEL=gemini-2.0-flash)
python gradio_app.py
```

| # | File | Exact prompt to send | Expected tool call(s) | Expected assistant response | Execution-trace panel | What must be visible |
|---|------|----------------------|-----------------------|-----------------------------|-----------------------|----------------------|
| 12 | `12-gemini-provider.png` | `Create a task called "Gemini smoke test" and then show all my tasks.` | `create_task`, `get_tasks` | Confirms creation and lists tasks | Provider **`gemini`**, model id, tool calls with timing | Trace header **and** the **System Overview** panel both showing provider `gemini` |

> **Known limitation (documented in the README):** multi-turn tool loops on
> newer Gemini models require a `thought_signature` that the legacy
> `google-generativeai` SDK cannot round-trip, so the *final* synthesized answer
> may fall back to the graceful "provider unavailable" message even though the
> `create_task` tool ran and wrote to the database. The **provider identity and
> a live tool call are still visible in the trace**, which is what shot 12
> documents. If your Gemini key has no free-tier quota for the chosen model,
> capture the System Overview/trace showing `gemini` selected; do **not** fake a success.

## Capture order for < 5 minutes

1. Launch (Groq) → **01**
2. Send prompt 02 → **02**
3. Send prompt 03 → **03**
4. Send prompt 04 → **04**
5. Send prompt 05 → **05**, then without sending anything: **06** (trace), **07** (table), **08** (stats), **11** (provider)
6. Send prompt 09 → **09**
7. Send prompt 10 → **10**
8. Switch `.env` to `gemini`, relaunch, send prompt 12 → **12**

That is 8 prompts total; shots 06/07/08/11 are free captures of the same screen.
