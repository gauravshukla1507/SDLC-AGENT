# Changelog

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
