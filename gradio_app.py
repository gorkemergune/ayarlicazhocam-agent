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

import json
import os
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
    "Title",
    "Project",
    "Status",
    "Priority",
    "Due",
    "Est (min)",
]

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
        rows.append(
            [
                task.get("id"),
                task.get("title"),
                project_display,
                task.get("status"),
                task.get("priority"),
                task.get("due_date") or "-",
                task.get("estimated_minutes") if task.get("estimated_minutes") is not None else "-",
            ]
        )
    return rows


def _load_stats_markdown() -> str:
    """Compute the Database Status panel from read-only queries."""
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
        return (
            f"- **Projects:** {total_projects}\n"
            f"- **Tasks:** {total_tasks}\n"
            f"- **Completed:** {completed}\n"
            f"- **In Progress:** {in_progress}\n"
            f"- **Blocked:** {blocked}\n"
            f"- **Overdue:** {overdue_count}"
        )
    except Exception:
        return "_Database status unavailable._"


def _format_trace(result: AgentResult) -> str:
    """Render the execution trace from an AgentResult (no backend changes)."""
    try:
        log = result.log.to_dict()
        lines: list[str] = [f"**Provider:** `{result.provider}`"]
        models = sorted({r["model"] for r in log["requests"] if r.get("model")})
        if models:
            lines.append(f"**Model:** `{', '.join(models)}`")

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

        if not calls:
            lines.append("\n_No tools were called for this message._")
        for i, call in enumerate(calls):
            timing = result.log.tools[i] if i < len(result.log.tools) else None
            lines.append(f"\n**Tool Call #{i + 1}: `{call['name']}`**")
            lines.append(f"- Arguments: `{call['args']}`")
            if timing is not None:
                status = "ok" if timing.success else "failed"
                lines.append(
                    f"- Execution time: {timing.elapsed_ms:.1f} ms ({status})"
                )
            raw = results_by_id.get(call["id"])
            if raw:
                try:
                    env = json.loads(raw)
                    if env.get("success"):
                        outcome = f"success — {env.get('message', '')}"
                    else:
                        code = (env.get("error") or {}).get("code", "ERROR")
                        outcome = f"error [{code}] — {env.get('message', '')}"
                except (json.JSONDecodeError, TypeError):
                    outcome = "(unparsable result)"
                lines.append(f"- Result: {outcome}")

        lines.append("\n---")
        total_ms = log.get("total_ms")
        prov_ms = log.get("provider_latency_ms")
        lines.append(
            f"**Total latency:** {total_ms:.0f} ms"
            + (f" (provider {prov_ms:.0f} ms)" if prov_ms else "")
        )
        if log.get("total_tokens") is not None:
            lines.append(f"**Token usage:** {log['total_tokens']}")
        lines.append("**Final response returned.**")
        return "\n".join(lines)
    except Exception:
        return "_Trace unavailable._"


# --- Chat handling (Agent layer only) --------------------------------------


def _provider_unavailable_text() -> str:
    return (
        "⚠️ Model sağlayıcı yapılandırılmadı. Lütfen `.env` içinde "
        "`MODEL_PROVIDER` ve ilgili API anahtarını ayarlayın.\n\n"
        "_(Model provider is not configured. Set MODEL_PROVIDER and the API "
        "key in .env.)_"
    )


def _friendly_error_text(result: AgentResult) -> str:
    if result.error == "PROVIDER_ERROR":
        return (
            "⚠️ Model sağlayıcısına şu anda ulaşılamıyor. Lütfen daha sonra "
            "tekrar deneyin.\n\n_(The model provider is currently unavailable.)_"
        )
    return (
        "⚠️ İstek tamamlanamadı. Lütfen tekrar deneyin.\n\n"
        "_(The request could not be completed.)_"
    )


def respond(message: str, history: list[dict[str, Any]] | None) -> tuple[str, str, bool]:
    """Run one turn through the Agent. Returns (assistant_text, trace_md, is_error).

    Never raises: any failure becomes a friendly message.
    """
    if not message or not message.strip():
        return "Lütfen bir mesaj yazın. _(Please enter a message.)_", "", True

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
    return (result.final_message or "_(empty response)_"), trace, False


def _friendly_error_text_generic() -> str:
    return (
        "⚠️ Beklenmeyen bir hata oluştu. Lütfen tekrar deneyin.\n\n"
        "_(An unexpected error occurred.)_"
    )


def _footer_markdown() -> str:
    provider = os.environ.get("MODEL_PROVIDER", "groq")
    if provider.lower() in ("gemini", "google"):
        model = os.environ.get("GEMINI_MODEL") or "gemini-1.5-flash (default)"
    else:
        model = os.environ.get("GROQ_MODEL") or "llama-3.3-70b-versatile (default)"
    try:
        db_path = resolve_db_path()
    except Exception:
        db_path = "?"
    return (
        f"**Provider:** `{provider}` &nbsp;·&nbsp; **Model:** `{model}`  \n"
        f"**SQLite database:** `{db_path}`"
    )


# --- UI --------------------------------------------------------------------


def build_ui():
    """Construct and return the Gradio Blocks app."""
    import gradio as gr

    with gr.Blocks(title="ayarlicazhocam") as demo:
        gr.Markdown("# 🎯 ayarlicazhocam")
        gr.Markdown(
            "Personal AI Productivity Assistant — "
            "task management through natural language."
        )

        with gr.Row():
            # LEFT: conversation
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(
                    height=460,
                    label="Chat",
                    placeholder=(
                        "Merhaba! Görevlerini yönetmek, plan yapmak veya "
                        "ilerlemeyi sorgulamak için bir mesaj yaz."
                    ),
                )
                with gr.Row():
                    msg_box = gr.Textbox(
                        placeholder="Bir görev ekle, planla, ilerlemeyi sor...",
                        show_label=False,
                        scale=8,
                        autofocus=True,
                    )
                    send_btn = gr.Button("Send", variant="primary", scale=1)
                clear_btn = gr.Button("🗑️ Clear Conversation", variant="secondary")

            # RIGHT: observability
            with gr.Column(scale=2):
                with gr.Accordion("🔍 Execution Trace", open=True):
                    trace_md = gr.Markdown("_No activity yet._")
                with gr.Accordion("📋 Current Tasks", open=True):
                    task_table = gr.Dataframe(
                        headers=TASK_TABLE_HEADERS,
                        value=_load_task_rows(),
                        interactive=False,
                        wrap=True,
                    )
                with gr.Accordion("📊 Database Status", open=True):
                    stats_md = gr.Markdown(_load_stats_markdown())
                refresh_btn = gr.Button("🔄 Refresh", variant="secondary", size="sm")

        gr.Markdown("---")
        gr.Markdown(_footer_markdown())

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
            return new_history, trace, _load_task_rows(), _load_stats_markdown(), ""

        def on_clear():
            # Keep this return order aligned with ``send_outputs``.
            return [], "_No activity yet._", _load_task_rows(), _load_stats_markdown(), ""

        def on_refresh():
            return _load_task_rows(), _load_stats_markdown()

        send_outputs = [chatbot, trace_md, task_table, stats_md, msg_box]
        send_btn.click(on_send, [msg_box, chatbot], send_outputs)
        msg_box.submit(on_send, [msg_box, chatbot], send_outputs)
        clear_btn.click(on_clear, None, send_outputs)
        refresh_btn.click(on_refresh, None, [task_table, stats_md])

    return demo


def main() -> None:
    """Initialize the database (idempotent), seed if empty, and launch the UI."""
    import gradio as gr

    initialize_database()
    seed_database()
    demo = build_ui()
    demo.launch(
        server_name=os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=int(os.environ.get("GRADIO_SERVER_PORT", "7860")),
        theme=gr.themes.Soft(),
    )


if __name__ == "__main__":
    main()
