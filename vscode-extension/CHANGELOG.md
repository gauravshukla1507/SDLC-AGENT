# Changelog

## 1.3.0
- **Spec-driven development is the default.** Constitution → spec → *your review* → plan + tasks → *your review* →
  tests first ∥ implementation → verification → **spec coverage** (every acceptance criterion has a test) → code
  review → release. Specs follow the Spec Kit layout (`.specify/memory/constitution.md`, `specs/NNN-feature/`)
  and are committed in your repository, numbered per feature.
- **Spec and plan review cards** in the run panel: read the document, answer open questions, *Approve* or
  *Request changes* (a comment is required; the author revises and asks again). Approval comments answer the
  spec's `[NEEDS CLARIFICATION]` questions and are added to it.
- **Codebase graph for existing products (Graphify).** Before planning, the repository is mapped into a knowledge
  graph; agents get the architecture, the code related to the feature and the blast radius of their changes.
  *Stack & verification* shows the graph with *Build* / *Open graph* actions. Requires `pip install graphifyy`.
- New settings: `agenticSdlc.playbook` (`specDriven` | `classic`) and `agenticSdlc.codebaseGraph` (`auto` | `off`).
- Spec files appear in each project's *Documents*. Projects keep the playbook they started with when resumed.

## 1.2.1
- **Stories you can find:** each project in the Projects view has a *Documents* group (requirements.md with the epic,
  user stories and acceptance criteria; design.md; release notes). You're told when the stories are ready, with a
  button to open them.
- **Faithful stories:** the Requirements agent now produces an epic and user stories (US-n) with Given/When/Then
  acceptance criteria, keeps the document's IDs, rules and boundaries verbatim, adds a traceability table, and
  doesn't invent features.
- **"use brd.md" works:** a short request that only names a document (for example in *Describe a change*) now loads
  that document's content. Agents can't open files they are only told about.
- **No more stuck runs:** tasks left "in progress" by a process that ended (closed window, crash, cancel) are
  recovered on resume.

## 1.2.0
- **Build from a requirements document.** New main command *Build application from requirements document…*
  (also in the Explorer right-click menu) accepts `.md`, `.txt`, `.docx` and `.pdf`. Headings, lists and tables
  (acceptance criteria) are preserved.
- Choose where to build: extend **this workspace**, or create a **new application in a new folder**, which becomes
  a git repository; open it in a new window when done.
- *Describe a change…* writes a short request for the current repository.
- Removed the recorded demo mode. Recorded agent answers now exist only as internal test fixtures.

## 1.1.0
- **Run on your Claude subscription.** The new default runtime calls Claude Code (`claude -p`) signed in with your
  Pro, Max, Team or Enterprise plan, so no API key is needed. Agents get read-only repository tools, and the API
  key is removed from their environment.
- New commands: *Sign in to Claude (use my subscription)* and *Check setup* (runtime, sign-in probe, stack tools).
- Projects pause instead of failing when you aren't signed in or reach a usage limit, with *Sign in* and *Resume*.
- Setting `agenticSdlc.agentRuntime` (`claudeSubscription` | `apiKey`) and `agenticSdlc.claudeCodePath`.

## 1.0.0
- One extension for every technology. The stack is detected from the repository (Java, .NET, Node/TypeScript,
  Python, Go, Rust, Ruby, PHP, Salesforce, dbt, Terraform, polyglot and greenfield) and verified with its own tools.
- AI delivery company model: org chart with hiring approval, goal ancestry, tasks and heartbeats, budgets with hard
  stop, approvals (release, budget, gateway, escalation), tool gateway, routines, skills and audit log.
- New activity-bar views: Projects, Approvals, Org chart, Stack & verification. Run panel with an approvals card;
  company dashboard; replay pages; demos for Node, Python and Salesforce.

## 0.1.0
- First preview (Salesforce only).
