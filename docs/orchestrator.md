# Qwen Orchestrator Integration

Local free orchestrator AI that plans the workflow. **Claude stays the CAD
worker; Qwen never emits CAD code.** Off by default — the backend falls back to
the deterministic rule-based path whenever the orchestrator is disabled or the
server is unreachable.

## Roles

- **Qwen (orchestrator):** analyze prompts, clarification questions, engineering
  brief, pipeline routing, structured worker prompts for Claude, output
  inspection, repair/retry decisions.
- **Claude (worker):** `services/llm_cad_generator.py` → build123d → STEP.
  Output still passes `check_code_safety()` + `BANNED` token checks.

## Components

| File | Role |
|------|------|
| `backend/app/ai/llm/config.py` | env config |
| `backend/app/ai/llm/client.py` | OpenAI-compatible client: `health`, `chat`, `chat_json` (guided_json + retry) |
| `backend/app/ai/orchestrator.py` | `analyze`, `worker_prompt`, `decide_repair`, `inspect_report`, `decide_next` |
| `backend/app/ai/cad_agent.py` | `build_worker_prompt(brief)` → (text, source) |
| `backend/app/ai/repair_agent.py` | `decide(...)` repair-vs-abort |
| `backend/app/ai/report_agent.py` | `inspect(...)` → `reports/findings.json` |
| `backend/app/ai/router.py` | `decide_next(brief)` intent→pipeline, MVP1-only active |
| `backend/app/schemas/orchestrator.py` | structured outputs + guided-JSON schemas |
| `backend/app/ai/prompts/*_system.txt` | system prompts |

## Environment

| Var | Default | Meaning |
|-----|---------|---------|
| `ORCHESTRATOR_ENABLED` | `0` | master switch |
| `ORCH_BASE_URL` | `http://127.0.0.1:8001/v1` | vLLM OpenAI endpoint |
| `ORCH_MODEL` | `qwen-orchestrator` | served model id |
| `ORCH_TEMPERATURE` | `0.2` | sampling temp |
| `ORCH_MAX_TOKENS` | `1024` | per-call budget |
| `ORCH_JSON_RETRIES` | `2` | guided-JSON parse retries |
| `MAX_CAD_REPAIRS` | `2` | bounded repair passes (llm mode) |

## Serving the model

vLLM already installed in the `vllmbknd` conda env (vllm 0.5.4). No pip install.

```bash
./scripts/serve_qwen.sh          # persistent; Qwen2.5-14B-Instruct-AWQ on :8001
```

First run downloads ~9.4GB AWQ weights to `~/.cache/huggingface`.

VRAM (validated 2026-06-14): weights 9.4GB; `--gpu-memory-utilization 0.35`
≈ 28GB reserved (weights + KV). This vllm 0.5.4 build lacks `--max-model-len`,
so context = model default 32768; `--enforce-eager` + `--max-num-seqs 8` bound
memory. Lower `ORCH_GPU_UTIL` if VRAM is tight.

## Endpoints

- `GET  /api/health/orchestrator` — liveness (reports disabled vs up/down).
- `POST /api/projects/{id}/orchestrate/plan` → `NextAction` (pipeline routing).
- Analyze flow (`POST /api/projects/{id}/analyze`) auto-uses the orchestrator
  when enabled; otherwise rule-based.

## Pipeline flow (llm mode)

`ORCHESTRATION` (build worker prompt) → `CAD_SOURCE_GENERATION` (Claude) →
`STEP_EXPORT` (+ bounded repair loop via `repair_agent`) → `STL_GLB_EXPORT` →
`CAD_INSPECTION` (+ `report_agent` findings) → `SNAPSHOT_GENERATION` →
`REPORT_GENERATION` → `COMPLETED`.

## Fallback guarantees

- Flag off → behavior identical to pre-integration.
- Flag on + server down/invalid → each agent catches `OrchestratorError` and
  uses the deterministic path; analyze writes `reports/orchestrator_error.txt`.

## Tests

```bash
python tests/test_orchestrator.py   # offline, stubbed client (also pytest-compatible)
```
