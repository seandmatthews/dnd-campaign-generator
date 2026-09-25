# AGENTS.md

Repo-wide working rules for agents. Per-project docs live in the subprojects
(`agentic-simulator/AGENTS.md`, `dnd-mapping/AGENTS.md`, `studio/README.md`).

## Kill dev processes when work concludes

Before ending a session, STOP every dev process you started or found running:
the studio server (`python run.py` in `studio/`, default port 8765), the FMG
static server (port 8642), any node watcher or engine subprocess. A studio
left running keeps serving STALE Python code after your edits (static JS
reloads, Python does not), squats the port, and inherits into the next
session as a confusing pre-existing process.

- Find listeners: `netstat -ano | grep LISTENING | grep -E ":(8765|8766|8642)"`
- Kill the tree (Git Bash): `taskkill //F //T //PID <pid>` — `//T` so child
  processes (engine subprocesses, reload workers) die with it
- Verify before signing off: re-run the netstat check — the ports must be free.
