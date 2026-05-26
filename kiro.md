# Kiro IDE Guide — Cloud Cost Anomaly Agent

This file explains how Kiro IDE is used in this project: what it is, why it matters, and
exactly how to showcase it in the demo video. By the end you'll understand spec-driven
development well enough to explain it on camera.

---

## What is Kiro?

Kiro is an AI-powered IDE (built on VS Code) that introduces **spec-driven development** —
a workflow where you describe what you want to build before writing any code.

The problem it solves: most AI coding tools are great at writing individual functions,
but terrible at building a *system* — because they have no persistent understanding of your
requirements, architecture, or coding standards. You end up re-explaining context in every chat.

Kiro solves this with three concepts:

| Concept | What it does |
|---|---|
| **Specs** | You describe the feature; Kiro expands it into requirements → design → implementation tasks |
| **Steering** | Persistent markdown files that Kiro reads on every interaction — your tech stack, coding standards, file structure |
| **Hooks** | Event-driven automations — e.g. "every time agent.py is saved, run the integration tests" |

---

## How Kiro fits into this project

This project was **designed using Kiro's spec workflow**. The `.kiro/` directory contains:

```
.kiro/
  steering/
    product.md         ← What this agent does and its constraints
    tech.md            ← Python 3.12, Bedrock Converse API, Elastic Cloud, Slack
    structure.md       ← File layout, naming conventions, test patterns
  specs/cost-anomaly-agent/
    requirements.md    ← 5 user stories with EARS-notation acceptance criteria
    design.md          ← Architecture diagram, component design, ES schemas
    tasks.md           ← 17 implementation tasks, all checked off
  hooks/
    on-save-agent.md   ← Run tests when agent.py or tools/ change; validate registrations
```

In the video, you show viewers:
1. The spec files → explain what they contain
2. The Kiro IDE UI → show how tasks are tracked
3. The hooks → show automation firing when you save a file

---

## Installing and opening Kiro

### Download
- Download from [kiro.dev](https://kiro.dev) — free public preview
- Available for macOS, Windows, Linux
- Based on VS Code — if you know VS Code, you already know Kiro

### Open this project
```bash
kiro .
# OR: File > Open Folder → select the project root
```

### First-time setup
1. Click the **ghost icon** (Kiro icon) in the left activity bar
2. Click **"Generate Steering Docs"** — Kiro scans your project and creates `.kiro/steering/` automatically
   - For this project, the steering files are already written (see `.kiro/steering/`)
3. Kiro reads all three steering files on every interaction — you never re-explain your stack

---

## The Spec Workflow (what to show in the video)

### Step 1 — Show the spec files

Open `.kiro/specs/cost-anomaly-agent/requirements.md` in Kiro.

**What to say on camera:**
> "Instead of jumping straight into code, I started by writing this spec. These are real user
> stories with acceptance criteria — the same format a product manager would write. Kiro reads
> this and understands *what* I'm building before touching a single line of code."

Key things to highlight in `requirements.md`:
- US-1: Daily detection — threshold configurable via env var
- US-2: Deploy correlation — the ±12h window, deploy metadata fields
- US-3: Actionable Slack alert — the "cause + fix + dollar estimate" requirement

### Step 2 — Show the design

Open `.kiro/specs/cost-anomaly-agent/design.md`.

**What to say on camera:**
> "From the requirements, Kiro helped produce this design document — the architecture diagram,
> the Bedrock message flow showing exactly what Claude will call and in what order, the ES index
> schemas. This became the blueprint for every file in the project."

Key things to highlight in `design.md`:
- The Bedrock message flow (user/assistant turn sequence)
- The 3 Elasticsearch indices and their schemas
- The security design (least-privilege IAM, no credentials in code)

### Step 3 — Show the tasks

Open `.kiro/specs/cost-anomaly-agent/tasks.md`.

**What to say on camera:**
> "Kiro breaks the design into concrete implementation tasks. 17 tasks across 5 phases —
> each with a clear outcome. You can click any task and Kiro will execute it, generating
> the code while staying consistent with your steering files and all the other tasks it already did.
>
> All 17 are checked off because we already built this. Let me show you what that code looks like."

### Step 4 — Show the generated code

Open `agent.py`. Point out:
- Type hints on every function signature ← enforced by `tech.md` steering
- Google-style docstrings ← enforced by `tech.md` steering
- Import location for tools ← matches `structure.md` steering conventions
- The Bedrock `toolUse` nested format ← matches the design doc

**What to say on camera:**
> "Notice the consistency — type hints everywhere, Google docstrings, specific exception handling.
> Kiro enforced these because I defined them in my tech.md steering file. Every file in this
> project follows the same standards automatically. That's the power of steering."

---

## Hooks (what to show in the video)

### Showing a hook fire live

1. Open `agent.py` in Kiro
2. Make a trivial change (add a comment)
3. Save the file (`Cmd+S` / `Ctrl+S`)
4. Watch the test panel — Kiro automatically runs `pytest tests/test_integration.py`

**What to say on camera:**
> "I saved agent.py and the tests are running automatically. I didn't type pytest.
> I didn't switch to a terminal. Kiro hooked into the file save event and ran the tests for me.
> If I'd broken something, I'd see the failure inline right here — before I pushed to main."

### The three hooks in this project

| Hook file | Trigger | What fires |
|---|---|---|
| `on-save-agent.md` | Save `agent.py` | `pytest tests/test_integration.py -v` |
| `on-save-agent.md` | Save `tools/*.py` | `pytest tests/test_integration.py -v` |
| `on-save-agent.md` | Save `tools/*.py` | Ask Kiro: "Are all tool functions registered in agent.py?" |

---

## Steering Files (what to show in the video)

Open `.kiro/steering/tech.md` in Kiro.

**What to say on camera:**
> "This is a steering file. It's plain markdown, and Kiro reads it on every single interaction.
> Look — I've defined the Bedrock model ID, the exact tool call format Bedrock expects,
> the ES client pattern. I wrote this once. Now every piece of code Kiro touches in this
> project follows these conventions automatically.
>
> This is why the code is consistent. Not because I reviewed every function — because Kiro
> always had this context."

Key things to show in `tech.md`:
- The Bedrock model ID note (cross-region inference profile)
- The tool call format (`{"toolUse": {...}}` vs the flat format other docs show)
- The code quality rules at the bottom

---

## Demo sequence for the video (Kiro-specific segments)

### Segment A: "Why spec-driven?" (30 seconds, during architecture section)

Show the `.kiro/specs/` directory structure in the file tree.

> "Before I wrote a line of code, I wrote this spec. Requirements first, then design,
> then tasks. Kiro read all of this and generated code that matched the architecture —
> not just the immediate function, the whole system."

### Segment B: Walk through spec → code (2 minutes, dedicated segment)

1. Open `requirements.md` → point at US-2 (deploy correlation)
2. Open `design.md` → show the Bedrock message flow
3. Open `tasks.md` → show tasks are all checked off
4. Open `agent.py` → "This is what those tasks produced"
5. Open `tools/elastic_search.py` → "This is what the design document specified"

### Segment C: Show a hook fire (1 minute)

1. Open `agent.py`
2. Add a comment on line 1: `# demo hook trigger`
3. Save
4. Show pytest running in the test panel
5. Remove the comment, save again — tests still pass

> "This is the development loop Kiro enables. Write spec → generate code → save file →
> tests run automatically → you know immediately if something broke."

---

## Key talking points (use in the video)

**On Kiro vs Cursor/GitHub Copilot:**
> "Copilot helps you write faster. Kiro helps you build better. The difference is specs
> and steering — persistent context that makes every generated function consistent with
> your architecture, not just technically correct in isolation."

**On spec-driven vs vibe-coding:**
> "Vibe coding gets you a prototype in 10 minutes. Spec-driven development gets you a
> production system. The spec forces you to think about edge cases, error handling, and
> security *before* the code exists. These acceptance criteria are what became the tests."

**On steering files:**
> "Steering files are what separates a 'one-shot' Kiro project from a long-term Kiro project.
> Without steering, you re-explain your stack every chat. With steering, Kiro always knows
> you're using Bedrock Converse — not the Messages API, not LangChain — and writes accordingly."

**On hooks:**
> "Hooks are the senior engineer on your team who catches things you miss. I don't have to
> remember to run tests when I change the tool dispatcher. The hook does it. I don't have to
> check if a new function got registered in Bedrock config. The hook checks for me."

---

## Resources

- [Kiro documentation](https://kiro.dev/docs/) — specs, steering, hooks all covered
- [Kiro first project guide](https://kiro.dev/docs/getting-started/first-project/) — step-by-step onboarding
- [Kiro hook examples](https://kiro.dev/docs/hooks/examples/) — security scanning, test coverage, i18n
- [Kiro steering reference](https://kiro.dev/docs/steering/) — inclusion modes (always/conditional/manual)
- [Introducing Kiro blog post](https://kiro.dev/blog/introducing-kiro/) — the story and design philosophy
