"""Gradio demo interface for ayarlicazhocam.

This is a **presentation layer only**. It contains no business logic:

    Gradio  ->  Agent (Orchestrator)  ->  Tools  ->  Services  ->  Repository  ->  SQLite

- Chat goes through the Agent layer (`Orchestrator.run`).
- Read-only display panels use the sanctioned tool and repository read
  helpers.  Chat mutations always go through the Agent layer.
- All handlers degrade gracefully; a stack trace is never shown to the user.

Run with:  ``python gradio_app.py``  (after configuring ``.env``).
"""

from __future__ import annotations

import html
import json
import os
import socket
from typing import Any

from agent import AgentResult, Orchestrator, ProviderError, get_provider
from database import (
    fetch_all,
    initialize_database,
    resolve_db_path,
    seed_database,
)
from tools import get_tasks_tool

TASK_TABLE_HEADERS = [
    "ID",
    "Task",
    "Project",
    "Status",
    "Priority",
    "Due Date",
    "Est. (min)",
]
TASK_COLUMN_WIDTHS = ["48px", "30%", "18%", "16%", "14%", "13%", "90px"]

# --- Orchestrator (lazy, cached) -------------------------------------------

_orchestrator: Orchestrator | None = None
_orchestrator_ready = False


def _get_orchestrator() -> Orchestrator | None:
    """Build the orchestrator once; return None if the provider is unconfigured."""
    global _orchestrator, _orchestrator_ready
    if _orchestrator_ready:
        return _orchestrator
    _orchestrator_ready = True
    try:
        _orchestrator = Orchestrator(get_provider())
    except ProviderError:
        _orchestrator = None
    return _orchestrator


# --- Formatting helpers ----------------------------------------------------


def _titleize(value: Any) -> str:
    """Turn a stored enum like ``in_progress`` into a readable ``In progress``."""
    if value in (None, ""):
        return "-"
    return str(value).replace("_", " ").strip().capitalize()


def _esc(value: Any) -> str:
    """HTML-escape a value for safe inline rendering."""
    return html.escape("" if value is None else str(value))


# --- Read-only display helpers ---------------------------------------------


def _load_project_names() -> dict[int, str]:
    """Return a {project_id: project_name} lookup dict (read-only)."""
    try:
        rows = fetch_all("SELECT id, name FROM projects;")
        return {row["id"]: row["name"] for row in rows}
    except Exception:
        return {}


def _load_task_rows() -> list[list[Any]]:
    """Return current tasks as table rows via the tool layer (read-only)."""
    try:
        result = get_tasks_tool()
    except Exception:
        return []
    if not result.get("success"):
        return []
    project_names = _load_project_names()
    rows: list[list[Any]] = []
    for task in result["data"]["tasks"]:
        pid = task.get("project_id")
        project_display = project_names.get(pid, "-") if pid is not None else "-"
        est = task.get("estimated_minutes")
        rows.append(
            [
                task.get("id"),
                task.get("title"),
                project_display,
                _titleize(task.get("status")),
                _titleize(task.get("priority")),
                task.get("due_date") or "-",
                est if est is not None else "-",
            ]
        )
    return rows


def _stat_cell(value: Any, label: str, color: str) -> str:
    return (
        f'<div class="stat">'
        f'<div class="stat-value {color}">{_esc(value)}</div>'
        f'<div class="stat-label">{_esc(label)}</div>'
        f"</div>"
    )


def _load_stats_html() -> str:
    """Compute the Task Statistics panel from read-only queries."""
    try:
        tasks_env = get_tasks_tool()
        overdue_env = get_tasks_tool(overdue=True)
        tasks = tasks_env["data"]["tasks"] if tasks_env.get("success") else []
        overdue = overdue_env["data"]["tasks"] if overdue_env.get("success") else []
        total_tasks = len(tasks)
        completed = sum(1 for t in tasks if t.get("status") == "completed")
        in_progress = sum(1 for t in tasks if t.get("status") == "in_progress")
        blocked = sum(1 for t in tasks if t.get("status") == "blocked")
        overdue_count = len(overdue)
        total_projects = len(_load_project_names())
        cells = [
            _stat_cell(total_tasks, "Total Tasks", "c-slate"),
            _stat_cell(completed, "Completed", "c-green"),
            _stat_cell(in_progress, "In Progress", "c-blue"),
            _stat_cell(blocked, "Blocked", "c-red"),
            _stat_cell(overdue_count, "Overdue", "c-orange"),
            _stat_cell(total_projects, "Projects", "c-slate"),
        ]
        return f'<div class="stat-grid">{"".join(cells)}</div>'
    except Exception:
        return '<div class="muted">Statistics unavailable.</div>'


def _trace_row(key: str, value_html: str) -> str:
    return (
        f'<div class="trace-row"><span class="trace-key">{_esc(key)}</span>'
        f'<span class="trace-val">{value_html}</span></div>'
    )


def _format_trace(result: AgentResult) -> str:
    """Render the execution trace as clean log entries (no raw dictionaries)."""
    try:
        log = result.log.to_dict()
        models = sorted({r["model"] for r in log["requests"] if r.get("model")})

        # Pair tool_calls (from assistant messages) with their result envelopes.
        calls: list[dict[str, Any]] = []
        results_by_id: dict[str, str] = {}
        for msg in result.messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    calls.append(
                        {
                            "id": tc.get("id"),
                            "name": tc["function"]["name"],
                            "args": tc["function"]["arguments"],
                        }
                    )
            elif msg.get("role") == "tool":
                results_by_id[msg.get("tool_call_id")] = msg.get("content", "")

        # Header: provider / model summary.
        head_bits = [f'Provider <code>{_esc(result.provider)}</code>']
        if models:
            head_bits.append(f'Model <code>{_esc(", ".join(models))}</code>')
        head = '<div class="trace-head">' + " &middot; ".join(head_bits) + "</div>"

        if not calls:
            body = '<div class="trace-empty">No tools were called for this message.</div>'
        else:
            entries: list[str] = []
            for i, call in enumerate(calls):
                timing = result.log.tools[i] if i < len(result.log.tools) else None

                # Status badge + duration from the timing record.
                if timing is not None:
                    ok = timing.success
                    badge_cls = "badge-success" if ok else "badge-error"
                    status_txt = "Success" if ok else "Error"
                    duration = f"{timing.elapsed_ms:.0f} ms"
                else:
                    badge_cls, status_txt, duration = "badge-info", "Info", "-"

                # Human-readable result from the envelope.
                result_html = "-"
                raw = results_by_id.get(call["id"])
                if raw:
                    try:
                        env = json.loads(raw)
                        if env.get("success"):
                            result_html = _esc(env.get("message", "Success"))
                        else:
                            code = (env.get("error") or {}).get("code", "ERROR")
                            badge_cls, status_txt = "badge-error", "Error"
                            result_html = (
                                f'<span class="mono">{_esc(code)}</span> '
                                f"{_esc(env.get('message', ''))}"
                            )
                    except (json.JSONDecodeError, TypeError):
                        result_html = "(unparsable result)"

                args_html = f'<span class="mono">{_esc(call["args"])}</span>'
                rows = [
                    _trace_row("Tool", f'<span class="mono">{_esc(call["name"])}</span>'),
                    _trace_row("Status", f'<span class="badge {badge_cls}">{status_txt}</span>'),
                    _trace_row("Duration", _esc(duration)),
                    _trace_row("Arguments", args_html),
                    _trace_row("Result", result_html),
                ]
                entries.append(f'<div class="trace-entry">{"".join(rows)}</div>')
            body = "".join(entries)

        # Footer: totals.
        total_ms = log.get("total_ms")
        prov_ms = log.get("provider_latency_ms")
        foot_bits = []
        if total_ms is not None:
            latency = f"{total_ms:.0f} ms"
            if prov_ms:
                latency += f" (provider {prov_ms:.0f} ms)"
            foot_bits.append(_trace_row("Total latency", _esc(latency)))
        if log.get("total_tokens") is not None:
            foot_bits.append(_trace_row("Tokens", _esc(log["total_tokens"])))
        foot = f'<div class="trace-foot">{"".join(foot_bits)}</div>' if foot_bits else ""

        return head + body + foot
    except Exception:
        return '<div class="muted">Trace unavailable.</div>'


def _system_overview_html() -> str:
    """Provider / model / database summary card content."""
    provider = os.environ.get("MODEL_PROVIDER", "ollama")
    provider_key = provider.lower()
    if provider_key in ("gemini", "google"):
        model = os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash (default)"
    elif provider_key in ("ollama", "local"):
        model = os.environ.get("OLLAMA_MODEL") or "qwen2.5:7b (default)"
    else:
        model = os.environ.get("GROQ_MODEL") or "llama-3.3-70b-versatile (default)"
    try:
        db_path = resolve_db_path()
    except Exception:
        db_path = "unknown"

    rows = [
        ("Provider", provider),
        ("Model", model),
        ("Database", db_path),
    ]
    body = "".join(
        f'<div class="sys-row"><span class="sys-key">{_esc(k)}</span>'
        f'<span class="sys-val mono">{_esc(v)}</span></div>'
        for k, v in rows
    )
    return body


# --- Chat handling (Agent layer only) --------------------------------------


def _provider_unavailable_text() -> str:
    return (
        "The model provider is not configured. Set MODEL_PROVIDER and the "
        "matching API key in your .env file, then restart the application."
    )


def _friendly_error_text(result: AgentResult) -> str:
    if result.error == "PROVIDER_ERROR":
        return (
            "The model provider is currently unavailable. This is often a rate "
            "limit on free tiers. Please wait a moment and try again."
        )
    return "The request could not be completed. Please try again."


def _friendly_error_text_generic() -> str:
    return "An unexpected error occurred. Please try again."


def respond(message: str, history: list[dict[str, Any]] | None) -> tuple[str, str, bool]:
    """Run one turn through the Agent. Returns (assistant_text, trace_html, is_error).

    Never raises: any failure becomes a friendly message.
    """
    if not message or not message.strip():
        return "Please enter a message.", "", True

    orchestrator = _get_orchestrator()
    if orchestrator is None:
        return _provider_unavailable_text(), "", True

    try:
        result = orchestrator.run(message, history=history or [])
    except Exception:
        return _friendly_error_text_generic(), "", True

    trace = _format_trace(result)
    if not result.success:
        return _friendly_error_text(result), trace, True
    return (result.final_message or "(empty response)"), trace, False


# --- UI --------------------------------------------------------------------

_EMPTY_TRACE_HTML = '<div class="trace-empty">No activity yet.</div>'

_CUSTOM_CSS = """
.app-header { padding: 2px 4px 0; }
.app-header h1 { font-size: 1.5rem; font-weight: 700; margin: 0; letter-spacing: -0.02em; }
.app-header p { color: #64748b; margin: 4px 0 0; font-size: 0.95rem; }
.card-title { font-size: 0.75rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.07em; color: #475569; margin: 0 0 4px; }
.muted { color: #94a3b8; font-size: 0.88rem; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.8rem; }
/* Task statistics */
.stat-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.stat { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px;
  padding: 12px 8px; text-align: center; }
.stat-value { font-size: 1.4rem; font-weight: 700; line-height: 1; }
.stat-label { font-size: 0.7rem; color: #64748b; margin-top: 6px;
  text-transform: uppercase; letter-spacing: 0.04em; }
.c-green { color: #16a34a; } .c-blue { color: #2563EB; } .c-red { color: #dc2626; }
.c-orange { color: #ea580c; } .c-slate { color: #0f172a; }
/* System overview */
.sys-row { display: flex; justify-content: space-between; gap: 12px;
  padding: 6px 0; font-size: 0.85rem; border-bottom: 1px solid #f1f5f9; }
.sys-row:last-child { border-bottom: none; }
.sys-key { color: #64748b; } .sys-val { color: #0f172a; word-break: break-all;
  text-align: right; }
/* Execution trace */
.trace-head { font-size: 0.82rem; color: #475569; margin-bottom: 10px; }
.trace-head code { background: #eff6ff; color: #1d4ed8; padding: 1px 6px;
  border-radius: 5px; }
.trace-entry { border: 1px solid #e2e8f0; border-radius: 10px; padding: 10px 12px;
  margin-bottom: 8px; background: #ffffff; }
.trace-row { display: flex; justify-content: space-between; gap: 12px;
  padding: 3px 0; font-size: 0.83rem; }
.trace-key { color: #64748b; flex: 0 0 auto; }
.trace-val { color: #0f172a; text-align: right; word-break: break-word; }
.trace-foot { margin-top: 6px; padding-top: 6px; border-top: 1px dashed #e2e8f0; }
.trace-empty { color: #94a3b8; font-size: 0.85rem; padding: 4px 0; }
.badge { font-size: 0.72rem; font-weight: 600; padding: 2px 9px; border-radius: 999px; }
.badge-success { background: #dcfce7; color: #15803d; }
.badge-error { background: #fee2e2; color: #b91c1c; }
.badge-info { background: #dbeafe; color: #1d4ed8; }
"""


def _build_theme(gr: Any) -> Any:
    """A restrained, blue-based professional theme built on Gradio's Soft base."""
    return gr.themes.Soft(
        primary_hue=gr.themes.colors.blue,
        secondary_hue=gr.themes.colors.blue,
        neutral_hue=gr.themes.colors.slate,
        font=[
            gr.themes.GoogleFont("Inter"),
            "system-ui",
            "-apple-system",
            "sans-serif",
        ],
    ).set(
        body_background_fill="#f8fafc",
        block_background_fill="#ffffff",
        block_border_width="1px",
        block_radius="12px",
        block_shadow="0 1px 2px 0 rgba(15, 23, 42, 0.04)",
        button_primary_background_fill="#2563EB",
        button_primary_background_fill_hover="#3B82F6",
        button_primary_text_color="#ffffff",
        input_border_color="#cbd5e1",
    )


def _card_title(gr: Any, text: str) -> None:
    gr.Markdown(f'<div class="card-title">{text}</div>')


def build_ui():
    """Construct and return the Gradio Blocks app."""
    import gradio as gr

    # Note: in Gradio 6 the theme and css are applied at launch() (see main()).
    with gr.Blocks(title="ayarlicazhocam") as demo:
        gr.Markdown(
            '<div class="app-header"><h1>ayarlicazhocam</h1>'
            "<p>Personal AI Productivity Assistant</p></div>"
        )

        with gr.Row(equal_height=False):
            # LEFT: conversation
            with gr.Column(scale=3):
                with gr.Group():
                    _card_title(gr, "Assistant Chat")
                    chatbot = gr.Chatbot(
                        height=520,
                        show_label=False,
                        placeholder=(
                            "Ask about your tasks, create work, or review "
                            "progress in natural language."
                        ),
                    )
                    with gr.Row():
                        msg_box = gr.Textbox(
                            placeholder="Message the assistant...",
                            show_label=False,
                            scale=8,
                            autofocus=True,
                        )
                        send_btn = gr.Button("Send", variant="primary", scale=1)
                    clear_btn = gr.Button(
                        "Clear conversation", variant="secondary", size="sm"
                    )

            # RIGHT: dashboard
            with gr.Column(scale=2):
                with gr.Group():
                    _card_title(gr, "System Overview")
                    system_md = gr.Markdown(_system_overview_html())

                with gr.Group():
                    _card_title(gr, "Task Statistics")
                    stats_md = gr.Markdown(_load_stats_html())

                with gr.Group():
                    _card_title(gr, "Recent Tasks")
                    task_table = gr.Dataframe(
                        headers=TASK_TABLE_HEADERS,
                        value=_load_task_rows(),
                        column_widths=TASK_COLUMN_WIDTHS,
                        interactive=False,
                        wrap=True,
                    )
                    refresh_btn = gr.Button(
                        "Refresh", variant="secondary", size="sm"
                    )

                with gr.Group():
                    _card_title(gr, "Execution Trace")
                    trace_md = gr.Markdown(_EMPTY_TRACE_HTML)

        # --- Event handlers ------------------------------------------------

        def on_send(message, history):
            history = history or []
            assistant, trace, is_error = respond(message, history)
            if is_error:
                gr.Warning(assistant.splitlines()[0])
            new_history = history + [
                {"role": "user", "content": message},
                {"role": "assistant", "content": assistant},
            ]
            # Auto-refresh task table + stats after every turn (tools may write).
            return (
                new_history,
                trace or _EMPTY_TRACE_HTML,
                _load_task_rows(),
                _load_stats_html(),
                "",
            )

        def on_clear():
            # Keep this return order aligned with ``send_outputs``.
            return [], _EMPTY_TRACE_HTML, _load_task_rows(), _load_stats_html(), ""

        def on_refresh():
            return _load_task_rows(), _load_stats_html()

        send_outputs = [chatbot, trace_md, task_table, stats_md, msg_box]
        send_btn.click(on_send, [msg_box, chatbot], send_outputs)
        msg_box.submit(on_send, [msg_box, chatbot], send_outputs)
        clear_btn.click(on_clear, None, send_outputs)
        refresh_btn.click(on_refresh, None, [task_table, stats_md])

    return demo


def _load_env() -> None:
    """Load ``.env`` from the project root before any env var is read.

    Anchoring to the project directory (not the CWD) means the app finds its
    ``.env`` — provider, model, database path — no matter which directory it is
    launched from. Never fails.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv is a declared dependency
        return
    project_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(project_env):
        load_dotenv(project_env)
    else:  # fall back to the default search (CWD and parents)
        load_dotenv()


def _configured_port(default: int = 7860) -> int:
    """Parse ``GRADIO_SERVER_PORT`` defensively, falling back to ``default``."""
    raw = os.environ.get("GRADIO_SERVER_PORT", "")
    try:
        port = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return port if 1 <= port <= 65535 else default


def _find_free_port(host: str, start_port: int, max_tries: int = 100) -> int:
    """Return the first bindable port at or after ``start_port``.

    Scans ``start_port .. start_port + max_tries - 1`` and returns the first
    port that can be bound on ``host``. If none is free (unlikely), asks the OS
    for an ephemeral port. This is what lets the app recover automatically when
    the configured port is already in use, so the user never sees the Gradio
    "Cannot find empty port" ``OSError``.
    """
    # For binding tests, "0.0.0.0"/"" means "all interfaces".
    bind_host = "" if host in ("0.0.0.0", "") else host
    for port in range(start_port, start_port + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((bind_host, port))
                return port
            except OSError:
                continue
    # Last resort: let the OS pick any free port.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((bind_host, 0))
        return probe.getsockname()[1]


def main() -> None:
    """Initialize the database (idempotent), seed if empty, and launch the UI.

    Automatically falls back to the next free port if the configured one is
    occupied, so a leftover server never blocks a fresh launch.
    """
    import gradio as gr

    _load_env()  # ensure .env is read before we look at GRADIO_SERVER_PORT
    initialize_database()
    seed_database()

    host = os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1")
    configured_port = _configured_port()
    port = _find_free_port(host, configured_port)
    if port != configured_port:
        print(
            f"[ayarlicazhocam] Port {configured_port} is in use; "
            f"starting on the next free port {port} instead."
        )
    print(f"[ayarlicazhocam] Launching UI at http://{host}:{port}")

    demo = build_ui()
    demo.launch(
        server_name=host,
        server_port=port,
        theme=_build_theme(gr),
        css=_CUSTOM_CSS,
    )


if __name__ == "__main__":
    main()
