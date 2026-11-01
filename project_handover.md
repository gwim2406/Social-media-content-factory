# Project Handover: AI-Powered Social Media Content Production Agent

## 1. Project Overview

I am developing a locally hosted, semi-autonomous AI-agent system that researches, plans, creates, and eventually publishes social media content.

The initial target is Facebook Reels, with the architecture designed so that additional social media platforms can be integrated later.

The current research and scriptwriting workflow uses a signed-in Gemini web session in Chromium. It does not require Gemini API access or an API key.

The overall objective is to automate repetitive content-production activities while keeping a human in control of important decisions.

This is an existing project. The project files, Python modules, browser profiles, workflow demonstrations, and research outputs have already been created.

**Your responsibility is to understand the existing implementation, preserve working functionality, and continue development incrementally. Do not rebuild the project from scratch.**

---

## 2. Core Objectives

The system should eventually support the following workflow:

1. Research interesting and potentially engaging content topics.
2. Collect relevant information from the web.
3. Use a grounded AI provider to research and analyze the topic.
4. Consolidate the AI-generated ideas.
5. Present the best opportunities for human approval.
6. Generate a detailed story or video script for approved ideas.
7. Generate video content using Google Flow or other supported AI video-generation tools.
8. Review and approve the generated content.
9. Publish approved content to social media platforms.
10. Store production history, results, and relevant metadata.

The initial content category is unusual and interesting automotive stories, but the system should be designed to support different content categories without requiring major architectural changes.

---

## 3. Important Development Principles

Follow these principles throughout development:

### Human approval is mandatory

The system must not autonomously proceed through every stage without approval.

Important approval checkpoints should include:

- Content idea selection.
- Script approval.
- Video approval.
- Final publication approval.

The workflow should pause at these checkpoints and wait for an explicit human decision.

### Modular architecture

Keep the implementation modular.

Research, AI providers, content analysis, script generation, video production, publishing, and workflow orchestration should be independent components with clearly defined responsibilities.

Avoid putting the entire application into one Python file.

### Incremental development

Implement and test one stage at a time.

Before proceeding to another stage:

- Verify the current implementation.
- Test successful execution.
- Test relevant failure conditions.
- Preserve existing working functionality.
- Explain the changes made.

Do not introduce unnecessary complexity or rewrite working modules without a clear reason.

### Resource efficiency

The initial environment is resource-constrained.

Prefer resource-efficient implementations, while preserving the user's
explicitly selected browser-based Gemini workflow.

---

## 4. Existing Technical Environment

The application currently runs inside an Ubuntu virtual machine.

| Component | Current configuration |
|---|---|
| Virtualization | Oracle VirtualBox |
| Operating system | Ubuntu 24.04.5 LTS |
| Python | 3.12.3 |
| CPU | 2 vCPUs |
| RAM | Approximately 3.8 GB |
| Browser automation | Playwright with Chromium |
| Workflow engine | LangGraph |
| Workflow persistence | SQLite checkpointing |
| AI provider | Gemini web app |
| Web research | Gemini web search from the signed-in browser session |
| Development environment | VS Code / terminal |

The project is located at:

`~/PROJECTS/ai-social-agent`

Python virtual environment:

`~/PROJECTS/ai-social-agent/.venv`

Activate it using:

`source .venv/bin/activate`

Use `python -m pip` when installing Python packages.

Run Python modules from the project root using the `-m` option where appropriate.

---

## 5. Current Project Structure

The project has already been initialized with the following structure:

```text
ai-social-agent/
│
├── agents/
│   └── __init__.py
│
├── providers/
│   └── __init__.py
│
├── research/
│   └── __init__.py
│
├── browser_data/
│   ├── gemini/
│   └── chatgpt/
│
├── workspace/
│   ├── research/
│   └── approved/
│
├── .venv/
│
├── browser_manager.py
├── workflow_demo.py
└── workflow.sqlite
```

Additional Python modules already created include:

```text
research/
└── web_researcher.py

providers/
├── login_setup.py
├── inspect_interface.py
├── browser_ai.py
├── debug_chatgpt.py
└── debug_chatgpt_response.py

agents/
└── content_researcher.py
```

There may be additional files and generated research outputs. Inspect the actual repository before making assumptions about the current structure.

---

## 6. Implemented Components

### 6.1 Browser Manager

File: `browser_manager.py`

This is a legacy utility and is not used by the current worker workflow.

A reusable Playwright browser manager has been created.

Its responsibilities include:

- Starting a visible Chromium browser.
- Managing browser contexts.
- Creating and managing tabs.
- Navigating to websites.
- Capturing screenshots.
- Closing tabs and browser contexts.
- Handling downloads.

The browser is initially visible so that I can monitor agent activity.

The browser manager uses a persistent browser profile located under:

`browser_data/agent_profile`

The implementation also uses:

`workspace/downloads`

Inspect the existing implementation before changing its interface.

### 6.2 Web Researcher

File: `research/web_researcher.py`

This earlier Playwright/DuckDuckGo implementation is retained as legacy code;
the current worker does not use it.

A basic web research component has been implemented.

It currently:

- Accepts a search query.
- Uses DuckDuckGo HTML search.
- Extracts search-result titles.
- Extracts result URLs.
- Extracts available result snippets.
- Saves research results in JSON format.

Default output:

`workspace/research/latest_research.json`

The researcher currently collects search-result information rather than extracting and analyzing the complete contents of every article.

This limitation should be considered in future development.

Do not treat search-result snippets as independently verified facts.

### 6.3 Gemini Browser Provider

File: `providers/browser_ai.py`

The active workflow sends requests through the signed-in Gemini web interface:

- Research instructions ask Gemini to search the web and include cited links.
- Source links rendered inside Gemini's response are saved with research.
- Scriptwriting uses the same Gemini profile, without web-search instructions.
- Chromium is launched only while an AI request is being processed.

The browser profile is `browser_data/gemini`. Initialize or refresh its
manual sign-in with `python -m providers.login_setup` and choose `gemini`.
Browser automation does not bypass login, CAPTCHA, or provider restrictions.

### 6.4 Integrated Content Researcher

File: `agents/content_researcher.py`

This module integrates Gemini web search and topic analysis in one Gemini
browser request.

Its current workflow:

1. Accept a content research topic.
2. Ask Gemini to search and analyze the topic in the browser.
3. Save its response, cited source links, and any provider failure.

Run it using:

`python -m agents.content_researcher`

The program prompts for a content research topic.

Enter a short topic at this prompt, for example:

`Interesting and unusual automotive stories suitable for Facebook Reels`

Detailed AI instructions should be maintained in the Python prompt-building function rather than entered as multiple shell commands.

Output files include:

- `workspace/research/content_research_<timestamp>.json`
- `workspace/research/latest_content_research.json`

The JSON preserves the grounded response and available source citations.

### 6.5 LangGraph Workflow Demonstration

File: `workflow_demo.py`

A basic LangGraph workflow has been implemented and tested.

It demonstrates:

- Workflow execution.
- Human approval interruption.
- Workflow checkpointing.
- Resuming execution using `Command(resume=...)`.
- SQLite-based persistence.

The project has installed:

- `langgraph`
- `langgraph-checkpoint-sqlite`

The workflow demonstration uses SQLite persistence.

The existing demonstration is a proof of concept, not yet the final production workflow.

Future workflows must use unique thread IDs for individual content-production tasks. Do not reuse one fixed thread ID for unrelated tasks.

---

## 7. Current Research Output and Test Results

The research workflow makes one Gemini browser request with explicit web
search and citation instructions. The response, source links rendered inside
the response, and provider errors are persisted per task. Source snippets do
not independently verify an incident; fact-checking remains required before
script approval.

The browser integration, citation extraction, research, scriptwriting, and
approval paths have focused tests using mocked browser responses. A live run
requires a signed-in Gemini browser profile.

---

## 8. Planned Architecture

The following represents the intended overall architecture. Some components are not implemented yet.

```text
                    HUMAN USER
                         |
                         v
                  CONTROL DASHBOARD
                         |
                         v
                 WORKFLOW ORCHESTRATOR
                      LangGraph
                         |
                         v
                  CONTENT RESEARCH
                         |
              +----------+----------+
              |                     |
              v                     v
        GEMINI WEB SEARCH     CITED RESEARCH
              |                     |
              +----------+----------+
                         |
                         v
                RESEARCH CONSOLIDATOR
                         |
                         v
                RANKED CONTENT IDEAS
                         |
                         v
                  HUMAN APPROVAL
                         |
                         v
                   SCRIPT WRITER
                         |
                         v
                  SCRIPT APPROVAL
                         |
                         v
                 VIDEO GENERATION
                         |
                         v
                   VIDEO REVIEW
                         |
                         v
                 PUBLICATION APPROVAL
                         |
                         v
                 SOCIAL PUBLISHING
                         |
                         v
                  RESULT TRACKING
```

The diagram represents the intended workflow, not a claim that every component already exists.

---

## 9. Immediate Next Development Stage

The next priority is:

# Stage 7 — Research Consolidation and Human Approval (Implemented)

The current system produces one Google Search-grounded Gemini analysis. The
consolidator ranks ideas from that response and preserves provider citations.

The consolidation stage has been implemented.

### 9.1 Research Consolidator

The consolidator should:

- Read the existing research JSON.
- Extract distinct ideas from the grounded AI response.
- Merge duplicate ideas without losing useful details.
- Evaluate content ideas using transparent criteria.
- Produce a ranked list of content opportunities.
- Preserve supporting source references.
- Explicitly identify missing evidence and verification requirements.

Potential ranking criteria:

- Opening-hook strength.
- Curiosity.
- Emotional appeal.
- Visual storytelling potential.
- Audience relevance.
- Repeatability.
- Research quality.
- Production feasibility.

Scores should be editorial assessments, not claims of guaranteed virality.

### 9.2 Optional Additional Analysis

The current workflow deliberately avoids a second provider call to reduce
resource use and complexity. Reconsider additional analysis only if its
expected editorial value justifies the extra API cost and latency.

### 9.3 Human Approval

Once consolidation is complete, the workflow should pause for user approval.

Approvals are available in the worker terminal and local web dashboard.

The user should be able to:

- Review individual content ideas.
- See their rankings and reasoning.
- Inspect supporting sources.
- Approve selected ideas.
- Reject ideas.
- Request changes.
- Save approved ideas for subsequent stages.

Only approved ideas should enter the next production stage.

### 9.4 LangGraph Integration

Use the existing LangGraph implementation rather than introducing another workflow engine.

The workflow should:

- Persist state using SQLite.
- Support interruptions at approval checkpoints.
- Resume from saved checkpoints.
- Use unique task/thread IDs.
- Handle failed provider calls without losing successful research.
- Preserve task history.

---

## 10. Development Stages

### Stage 8 — Scriptwriting (Initial Implementation)

The workflow now sends idea-approved material to a separate scriptwriter agent.
Its role and procedure are maintained in:

- `agent_specs/scriptwriter.md`
- `agent_skills/reel_scriptwriting/SKILL.md`

The agent generates one script per selected idea sequentially through the
Gemini browser session, saves individual Markdown drafts and `scripts.json`,
and pauses for human script selection. It uses the supplied research as
context but does not independently verify facts or collect deeper evidence.
Unsupported claims must be flagged for verification.

### Stage 9 — Script Approval (Initial Implementation)

Scripts are shown in the interactive worker and require explicit selection.
Approved scripts are saved in `approved_scripts.json`. Editing/resubmission
and revision loops remain future work.

### Stage 10 — Video Generation

### Stage 10 — Video Generation

Integrate the Google Flow browser workflow.

The system should:

- Convert approved scripts into scene prompts.
- Generate video clips.
- Track generated files.
- Handle clip sequencing and continuity.
- Preserve the original source images and references where necessary.
- Support manual review.

Video-generation automation should respect the service's normal usage limits and access restrictions.

### Stage 11 — Video Approval

Present the final assembled video for human approval.

No video should be published without explicit approval.

### Stage 12 — Social Media Publishing

Implement platform-specific publishing modules.

Facebook should be the initial target.

Publishing should use supported official interfaces or authorized integrations wherever possible.

Avoid unsafe workarounds and do not attempt to bypass platform restrictions.

### Stage 13 — Monitoring and Analytics

Store:

- Published post IDs.
- Publication timestamps.
- Content metadata.
- Available engagement metrics.
- Production history.
- Workflow errors.
- Performance summaries.

Use the collected information to improve future editorial decisions.

---

## 11. Security and Isolation Requirements

Security is extremely important.

The agents initially run inside a separate Ubuntu VirtualBox VM.

The VM is intended to isolate the automation system from the host computer.

Maintain the following restrictions:

1. Do not configure VirtualBox shared folders.
2. Do not enable shared clipboard or drag-and-drop.
3. Do not mount host folders into the VM.
4. Do not allow agents to modify host operating-system settings.
5. Keep browser profiles and authentication sessions inside the VM.
6. Do not store passwords directly in Python source files.
7. Do not print authentication tokens or session cookies in logs.
8. Do not commit browser profiles or sensitive data to Git.
9. Do not execute arbitrary shell commands or Python code generated by AI providers.
10. Treat external web content as untrusted input.
11. Validate AI-generated structured data before using it.
12. Require explicit approval before important external actions such as publishing.

The VM provides an isolation boundary, but it should not be treated as a perfect security guarantee.

### Git ignore requirements

Ensure that the repository excludes sensitive and generated data.

At minimum:

```gitignore
.venv/
__pycache__/
browser_data/
workspace/
*.sqlite
*.db
.env
```

Review the existing `.gitignore` before changing it.

Do not accidentally commit browser authentication sessions, downloaded media, or generated research containing private information.

---

## 12. Browser Automation

The current research and scriptwriting worker launches one visible Chromium
session at a time through the Gemini browser profile. Keep browser calls
sequential, close sessions after each request, and respect Gemini's normal
usage restrictions. Do not implement CAPTCHA bypasses or attempt to evade
provider controls. The old DuckDuckGo browser researcher and ChatGPT browser
provider are not part of the current worker flow.

---

## 13. Local Dashboard

The local dashboard is implemented and binds to loopback only. It provides:

- Current workflow and agent status.
- Grounded research citations and errors.
- Consolidated content ideas.
- Approval controls.
- Script previews.
- Task history.

Video generation and publishing are not implemented. Do not expose the
unauthenticated dashboard to another machine or the public internet.

---

## 14. Coding and Collaboration Instructions for Copilot

Before making changes, inspect the repository and understand the existing code.

Follow these rules:

1. Do not assume the project structure is exactly as described here. Verify the actual files.
2. Inspect existing implementations before replacing or modifying them.
3. Preserve working code and existing interfaces unless changes are justified.
4. Avoid unnecessary dependencies.
5. Use Python type hints where useful.
6. Add clear error handling and useful logging.
7. Separate configuration from implementation.
8. Avoid hardcoded credentials, authentication tokens, and sensitive values.
9. Use relative project paths derived from a reliable project root.
10. Keep generated files organized under `workspace/`.
11. Do not overwrite historical research outputs.
12. Use unique identifiers for workflow tasks.
13. Add tests for important functionality.
14. Keep the code compatible with Python 3.12.
15. Account for the VM's limited RAM and CPU resources.

### Important working preference

I prefer to develop this project step by step.

Do not generate the entire application in one response.

For each stage:

1. Explain the proposed implementation.
2. Identify which existing files need modification.
3. Identify any new files required.
4. Implement the stage.
5. Explain how to execute it.
6. Provide commands to test it.
7. Wait for me to run the tests and share the results.
8. Fix any issues before moving to the next stage.

Avoid making several unrelated architectural changes at once.

---

## 15. First Task for Copilot

Start by performing a repository assessment.

Do not immediately modify any files.

Please:

1. Inspect the complete project directory structure.
2. Read the existing Python modules.
3. Review the current LangGraph workflow demonstration.
4. Review the latest content research implementation.
5. Examine the JSON output format produced by the research workflow.
6. Identify existing code that can be reused.
7. Identify incomplete components and technical weaknesses.
8. Check whether the project already has a `.gitignore` and requirements file.
9. Report any inconsistencies between this handover document and the actual repository.

Then propose a detailed implementation plan for:

**Stage 7 — Research Consolidation and Human Approval.**

Do not start Stage 7 implementation until you have presented the assessment and proposed plan.

---

## Final Project Direction

The objective is not simply to create a script that uses AI to generate social media posts.

The objective is to build a reliable, modular, human-supervised AI content-production system that can independently perform research, compare AI opinions, prepare content, generate media, and eventually publish approved content.

Prioritize reliability, maintainability, security, traceability, and human control over maximum automation.

Treat this as an ongoing software engineering project rather than a one-off automation script.

---

## Scheduled and Queued Worker (Initial Implementation)

The application now has a persistent worker entry point:

```bash
python -m orchestration.cli enqueue --topic "Unusual automotive stories"
python -m orchestration.cli worker --once
```

For recurring research, create a schedule and keep one worker process running:

```bash
python -m orchestration.cli schedule --topic "Unusual automotive stories" --every-hours 24
python -m orchestration.cli worker --poll-seconds 30
```

Schedules first run after their configured interval. Add `--run-now` to make
the first run due immediately. The worker also picks up manually enqueued work
when it next polls. Stop the worker with Ctrl+C.

Each task runs Gemini web research and analysis in Chromium,
then consolidates the results and pauses for idea approval. After you select
ideas, the scriptwriter generates one script per selected idea and pauses
again for script approval. The workflow does not automatically start video
generation or publishing.

Use the task ID printed by `enqueue` or `worker` to review and approve:

```bash
python -m orchestration.cli list
python -m orchestration.cli show <task-id>
python -m orchestration.cli approve <task-id> --ideas 1,3
# At the later script checkpoint:
python -m orchestration.cli approve <task-id> --scripts 1
# Or explicitly reject the task:
python -m orchestration.cli approve <task-id> --reject
```

For a task whose ideas were approved before the scriptwriter was added, start
the new stage from its saved approvals:

```bash
python -m orchestration.cli write-scripts <task-id>
```

In an interactive worker terminal, both approval stages display their
artifacts and prompt for ranks, `all`, or `reject`. Script drafts are saved to
`workspace/tasks/<task-id>/scripts/` and `scripts.json`; selected scripts are
saved to `approved_scripts.json`. Script generation currently uses Gemini in
Chromium and does not perform new fact-checking; the script instructions
require unsupported claims to be flagged for verification.

When the worker is running in an interactive terminal, it now prints the full
ranked idea details and prompts for idea numbers (for example `1,3`), `all`, or
`reject`. Existing tasks already waiting for approval are prompted when the
worker starts again. If you press Ctrl+C while idle, the worker exits cleanly;
if interrupted at the approval prompt, the approval remains saved and can be
handled by restarting the worker or using the separate `approve` command.

The approval selection resumes the task's LangGraph checkpoint. Queue state is
stored in `workspace/agent_queue.sqlite`, workflow checkpoints in
`workspace/agent_workflow.sqlite`, and each task's inputs and outputs under
`workspace/tasks/<task-id>/`.

The content researcher loads its role from
`agent_specs/content_researcher.md` and reusable research procedures from
`agent_skills/`. These Markdown files inform the AI prompt; Python modules
remain responsible for execution, scheduling, persistence, and approval
enforcement.

Run only one worker process at a time because queue state is shared. Failed
tasks are recorded and are not retried automatically. Inspect the saved
provider error and enqueue a new task when ready.

### Local Web Dashboard

Start the dashboard with:

```bash
python -m orchestration.cli dashboard
```

Open `http://127.0.0.1:8765` on the VM. The dashboard binds to loopback only;
do not expose it directly to another machine or the public internet.

- **Start a task** asks for a topic, queues a manual task independently of the
  schedule, and starts or wakes the worker.
- **Stop worker** requests a safe stop. The current browser request is allowed
  to finish; the worker does not claim another task afterward. The dashboard
  clears the active roster indicators and current-task, idea, and script panels;
  task history and saved workflow data are preserved.
- The overview shows the agent roster, current topic and stage, and recent task
  history. Separate tabs show candidate and approved ideas, and current and
  approved scripts.
- Idea and script approvals are queued in SQLite and applied by the worker.
- After a successful idea or script approval submission, the dashboard returns
  to the Overview tab.
- The command-line worker and dashboard share the same lock, queue, and stop
  request. Prefer operating the worker through the dashboard while it is open.

The worker and dashboard control data are stored under `workspace/` in
`agent_queue.sqlite` and `agent_workflow.sqlite`. UI actions do not directly
execute workflows inside HTTP request handlers; the worker consumes them from
the durable command queue.

### Gemini Browser Setup

No Gemini API key is needed. The worker uses the saved browser profile at
`browser_data/gemini`. If it is not signed in, initialize it interactively:

```bash
python -m providers.login_setup
```

Choose `gemini`, sign in manually in the opened Chromium window, then return to
the terminal and press Enter. Do not share credentials with the agent.