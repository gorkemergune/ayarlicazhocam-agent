# AGENTS.md

## Project Overview

**AyarlaHocam v2** is a personal AI productivity assistant built on top of the existing AyarlaHocam tool-calling agent.

The previous model was fine-tuned on:
- Software engineering concepts
- The user's personal projects
- The user's technical background and portfolio

The new version must preserve that identity while adding task management, daily planning, progress tracking, accountability, and review capabilities.

The assistant is not a generic chatbot. It should act as the user's:

- Personal task manager
- Daily planner
- Project supervisor
- Progress reviewer
- Accountability assistant
- Engineering-aware personal assistant

## Core Product Goal

Turn the existing fine-tuned assistant into a persistent, database-backed productivity system that can:

1. Read the user's tasks, projects, habits, deadlines, and daily plans.
2. Create or update records only through approved tools.
3. Build realistic daily plans from existing priorities and available time.
4. Detect overdue, blocked, or repeatedly postponed work.
5. Review progress using only stored data.
6. Use its fine-tuned knowledge about the user's projects to give context-aware suggestions.
7. Never invent task status, deadlines, progress, or completed work.

## Product Identity

The assistant should sound like a direct but supportive technical mentor.

Preferred behavior:
- Clear and practical
- Honest about incomplete work
- Focused on execution
- Aware of engineering workload
- Able to break large tasks into smaller actions
- Willing to challenge unrealistic plans
- Avoids motivational filler
- Does not claim database actions succeeded without a successful tool result

The assistant may say things such as:
- "Bugünkü planın kapasiteni aşıyor; iki görevi yarına taşıyalım."
- "Bu proje üç gündür ilerlemiyor. Blokajı netleştirelim."
- "README tamamlanmadan deploy görevini bitmiş saymıyorum."
- "Bu görev için tahmini süre ile gerçek süre arasında fark oluştu."

## Main Domains

### 1. Tasks
The assistant manages actionable work items.

Each task should support:
- Title
- Description
- Project
- Priority
- Status
- Due date
- Estimated duration
- Actual duration
- Energy level
- Tags
- Blocker
- Created date
- Completed date

### 2. Projects
Projects group related tasks and milestones.

Examples:
- AyarlaHocam v2
- Face Login
- MERAK benchmark
- University assignments
- Portfolio website
- Internship work

### 3. Daily Plans
A daily plan is generated from:
- Due dates
- Priorities
- Estimated duration
- Available study/work hours
- Energy level
- Unfinished tasks
- Fixed events

### 4. Progress Reviews
The assistant should support:
- Daily review
- Weekly review
- Project review
- Overdue task review
- Postponement analysis
- Estimated vs actual duration comparison

### 5. Habits
Habits are optional recurring actions such as:
- Exercise
- Reading
- English practice
- Coding practice
- Water intake
- Sleep routine

Habits must remain separate from one-time tasks.

## Recommended Architecture

```text
ayarlicazhocam-v2/
├── app.py
├── agent/
│   ├── orchestrator.py
│   ├── prompts.py
│   ├── model_client.py
│   └── response_parser.py
├── tools/
│   ├── task_tools.py
│   ├── project_tools.py
│   ├── planning_tools.py
│   ├── review_tools.py
│   └── schemas.py
├── database/
│   ├── connection.py
│   ├── models.py
│   ├── migrations.py
│   └── seed.py
├── services/
│   ├── planner.py
│   ├── prioritizer.py
│   ├── review_engine.py
│   └── date_utils.py
├── templates/
│   └── chat_template.jinja
├── tests/
│   ├── test_tasks.py
│   ├── test_planner.py
│   ├── test_reviews.py
│   ├── test_tool_loop.py
│   └── test_hallucination_cases.py
├── data/
│   └── ayarlicazhocam.db
├── screenshots/
├── requirements.txt
├── README.md
├── TASKS.md
├── ROADMAP.md
└── AGENTS.md
```

## Initial Database Schema

### projects

```sql
CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    deadline TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### tasks

```sql
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'todo',
    priority TEXT NOT NULL DEFAULT 'medium',
    due_date TEXT,
    estimated_minutes INTEGER,
    actual_minutes INTEGER,
    energy_level TEXT,
    blocker TEXT,
    postponed_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
```

### daily_plans

```sql
CREATE TABLE daily_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date TEXT NOT NULL UNIQUE,
    available_minutes INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### daily_plan_items

```sql
CREATE TABLE daily_plan_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    daily_plan_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    planned_minutes INTEGER,
    result TEXT,
    FOREIGN KEY (daily_plan_id) REFERENCES daily_plans(id),
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    UNIQUE(daily_plan_id, task_id)
);
```

### work_logs

```sql
CREATE TABLE work_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    log_date TEXT NOT NULL,
    minutes_spent INTEGER NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);
```

## MVP Tool Set

Keep the first version limited and reliable.

### `get_tasks`
Read tasks using filters such as:
- status
- project
- priority
- due date
- overdue only

### `create_task`
Create a new task after the user explicitly asks for it.

### `update_task`
Update status, deadline, priority, estimate, blocker, or description.

### `build_daily_plan`
Generate a plan from database tasks and user-provided available time.

### `log_work_session`
Record time spent on a task.

### `review_progress`
Calculate progress using database records.

Do not create many overlapping tools in the MVP.

## Tool-Calling Rules

1. Use tools for every claim involving stored tasks, projects, plans, deadlines, logs, or progress.
2. Never invent a task, deadline, completion status, work duration, or progress percentage.
3. Never report a write operation as successful unless the tool returns success.
4. Do not silently modify records.
5. Ask for confirmation before destructive operations such as deletion or bulk status changes.
6. When a task name is ambiguous, return matching candidates instead of guessing.
7. Use ISO dates internally: `YYYY-MM-DD`.
8. Preserve the user's original task title unless a normalized title is explicitly needed.
9. A generated daily plan is a proposal until it is saved.
10. Fine-tuned model knowledge may provide context, but database state is the source of truth.

## Planning Logic

The planner should score tasks using:

- Urgency
- Priority
- Deadline proximity
- Estimated duration
- Project importance
- Postponement count
- Current blocker state
- User energy level
- Available time

Suggested baseline score:

```text
score =
    priority_weight
    + urgency_weight
    + postponement_weight
    + project_weight
    - blocker_penalty
```

The planner must:
- Avoid exceeding available minutes
- Reserve buffer time
- Prefer finishing small overdue tasks when useful
- Avoid scheduling blocked tasks as primary work
- Split tasks longer than 120 minutes into smaller sessions
- Mark assumptions clearly

## Review Logic

A daily or weekly review should calculate:

- Completed task count
- Planned task count
- Completion rate
- Total focused minutes
- Overdue tasks
- Postponed tasks
- Blocked tasks
- Estimated vs actual time
- Project-level progress

The assistant should distinguish:
- No data
- Zero progress
- Incomplete logs

These are not the same state.

## Hallucination Test Cases

The following cases must be covered:

1. User asks about a task that does not exist.
2. User claims a task is completed, but no update tool was called.
3. User asks for last week's productivity with no logs.
4. Two tasks have similar names.
5. The requested date range is invalid.
6. A task is overdue but marked completed.
7. A daily plan exceeds available time.
8. A blocked task is selected as the day's main task.
9. The model tries to use fine-tuned memory as current database state.
10. A tool returns an error or empty result.

## Coding Standards

- Python 3.11+
- Type hints for public functions
- Pydantic or dataclasses for structured inputs
- Parameterized SQL queries only
- No SQL inside UI callbacks
- Separate model, tool, service, and database layers
- Clear error messages
- Deterministic tool result structure
- Unit tests for business logic
- No API keys committed to the repository
- `.env.example` must be provided
- Keep functions small and focused

## Tool Response Format

Every tool should return a predictable dictionary:

```json
{
  "success": true,
  "data": {},
  "message": "Human-readable result",
  "error": null
}
```

On failure:

```json
{
  "success": false,
  "data": null,
  "message": "Operation failed",
  "error": {
    "code": "TASK_NOT_FOUND",
    "details": "No task matched the provided identifier."
  }
}
```

## UI Requirements

The Gradio interface should include:

- Chat panel
- Current daily plan panel
- Task overview panel
- Tool-call trace
- Tool result log
- Clear conversation button
- Database seed/reset option for demo mode

The trace should display:
- Tool name
- Arguments
- Result
- Execution status
- Execution duration

## Definition of Done for MVP

The MVP is complete when:

- Tasks can be created, listed, and updated through tools.
- A realistic daily plan can be produced from stored tasks.
- Work sessions can be logged.
- Progress can be reviewed from database data.
- Tool traces are visible in Gradio.
- Hallucination test cases pass.
- The custom Jinja chat template works with normal and tool-call conversations.
- README contains setup, architecture, examples, screenshots, and demo instructions.
