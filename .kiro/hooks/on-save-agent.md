# Hook: Run integration tests when agent.py is saved

**Trigger:** File save — `agent.py`  
**Action type:** Run Command  
**Command:**
```
python -m pytest tests/test_integration.py -v --tb=short 2>&1 | head -60
```
**Purpose:** Catch regressions immediately when the agent loop or tool dispatcher changes.  
Any test failure surfaces inline before you deploy.

---

# Hook: Run integration tests when any tool file is saved

**Trigger:** File save — `tools/*.py`  
**Action type:** Run Command  
**Command:**
```
python -m pytest tests/test_integration.py -v --tb=short 2>&1 | head -60
```
**Purpose:** Tool function changes (especially return type changes) often break the agent loop silently.

---

# Hook: Validate tool is registered when a new function is added to tools/

**Trigger:** File save — `tools/elastic_search.py`, `tools/slack_notify.py`, `tools/audit_writer.py`  
**Action type:** Ask Kiro  
**Prompt:**
```
A tool file was just saved. Check agent.py BEDROCK_TOOL_DEFINITIONS and _dispatch_tool().
Are all functions in tools/ registered there? If any are missing, add them with the correct
inputSchema. Also check requirements.txt — are all new imports pinned there?
```
**Purpose:** Prevents the "UnknownTool" runtime error when a new function is added to tools/
but not registered with Bedrock.

---

# Hook: Update README when demo.md changes significantly

**Trigger:** File save — `demo.md`  
**Action type:** Ask Kiro  
**Prompt:**
```
demo.md was updated. Check README.md — specifically the "Setup" table and "Environment variables"
section. Do they still match what demo.md describes? Update README.md if any steps diverged.
```
**Purpose:** Keeps README and demo.md in sync.
