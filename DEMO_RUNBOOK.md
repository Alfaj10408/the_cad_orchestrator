# Trelis Text-to-CAD — Demo Runbook

End-to-end demo of `qwen_claude_code`: Qwen plans → local Claude Code CLI
generates → backend CAD worker executes → live SSE stream → artifacts.
No `ANTHROPIC_API_KEY` required (uses the authenticated Claude Code subscription).

## 0. Prereqs (one-time, already satisfied on this host)
- Claude Code CLI installed + authenticated: `claude auth status --text` → shows an account.
- vLLM available in the `vllmbknd` conda env.

## 1. Start Qwen orchestrator (vLLM, port 8001)
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
./scripts/serve_qwen.sh            # persistent; first run downloads ~9.4GB AWQ weights
# health: curl -s http://127.0.0.1:8001/v1/models
```

## 2. Start backend (orchestrator + Claude Code enabled, port 8010)
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
ORCHESTRATOR_ENABLED=1 \
CLAUDE_CODE_ENABLED=1 \
GENERATION_PROVIDER=qwen_claude_code \
ORCH_BASE_URL=http://127.0.0.1:8001/v1 \
ORCH_MODEL=qwen-orchestrator \
LD_PRELOAD=/root/anaconda3/envs/cadskills/lib/libexpat.so.1 \
/root/anaconda3/envs/cadskills/bin/uvicorn app.main:app \
  --app-dir backend --host 0.0.0.0 --port 8010
# Do NOT set ANTHROPIC_API_KEY for qwen_claude_code.
```
Verify:
```bash
curl -s http://localhost:8010/api/health
curl -s http://localhost:8010/api/health/claude-code
curl -s http://localhost:8010/api/health/orchestrator
```

## 3. Start frontend (Vite, port 5174)
```bash
cd /root/all_project_models/alfaj/text-to-cad-product/frontend
npm run dev
```

## 4. Browser
Open: **http://127.0.0.1:5174**

## 5. Demo script
1. Mode dropdown → **qwen_claude_code**.
2. Prompt (suggested): **`make a 40 x 30 x 10 mm rectangular calibration block`**
   (or click "⚡ Run Full Demo Flow" which uses deterministic mode).
3. Analyze → Submit clarifications if asked → **Generate CAD**.
4. Watch the live console stream events; artifacts auto-load on completion.

## 6. Expected status badges (top bar)
- **BACKEND** — green
- **QWEN PLANNER** — green (ON)
- **CLAUDE CODE** — green (authenticated)
- **CAD WORKER** — green
- **VIEWER** — green

## 7. Expected live console sequence
```
[System] Generation started (qwen_claude_code)
[Qwen] Qwen is preparing the CAD specification
[Qwen] Plan ready (orchestrator)
[Claude Code] Claude Code started (model claude-sonnet-4-6)
[Claude Code] Writing ... (streamed text deltas)
[Claude Code] Write: generate.py        (file.created)
[CAD Worker] Executing build123d source -> STEP/STL/GLB
[CAD Worker] CAD execution succeeded
[Artifact] model.step / model.stl / model.glb / snapshot.png
[System] Generation completed
```

## 8. Expected artifacts
Under `storage/projects/<project_id>/`:
- `cad/model.step` (~16 KB), `cad/model.stl`, `cad/model.glb`
- `cad/snapshot.png`
- `source/model.py` (the build123d source Claude wrote)
Claude's raw working copy: `runs/<project_id>/claude-workspace/output/generate.py`.

## 9. Quick CLI smoke (no browser)
```bash
B=http://localhost:8010/api
PID=$(curl -s -X POST $B/projects -H 'Content-Type: application/json' -d '{}' | python3 -c "import sys,json;print(json.load(sys.stdin)['project_id'])")
curl -s -X POST $B/projects/$PID/analyze -H 'Content-Type: application/json' -d '{"prompt":"make a 40 x 30 x 10 mm rectangular calibration block"}' >/dev/null
JOB=$(curl -s -X POST $B/projects/$PID/generate -H 'Content-Type: application/json' -d '{"generation_mode":"qwen_claude_code"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['job_id'])")
curl -sN $B/projects/$PID/jobs/$JOB/events      # live normalized SSE
curl -s $B/projects/$PID/artifacts              # after completion
```

## Known limitations
- One `qwen_claude_code` run takes a few minutes — dominated by global Claude
  Code SessionStart hooks/plugins (model API time is only seconds).
- `deterministic` mode is instant and offline; `anthropic_api` mode still needs
  its own `ANTHROPIC_API_KEY` (kept separate, not used in this demo).
- Services run as background processes, not systemd — restart after reboot.
- Cancel button stops the Claude process; partial workspace files may remain.
- `index.html` browser tab title still reads "Text-to-CAD MVP" (cosmetic).
