// Mirrors backend app/schemas/events.py GenEvent — the single typed event contract.
export interface GenEvent {
  id: number;
  project_id: string;
  job_id: string;
  timestamp: string;
  source: string;
  type: string;
  stage?: string | null;
  message?: string | null;
  delta?: string | null;
  data?: Record<string, unknown> | null;
}

export type AgentKey = "qwen" | "claude" | "executor" | "validator";
export type AgentStatus = "idle" | "active" | "done" | "error";

export interface Agent {
  key: AgentKey;
  name: string;
  status: AgentStatus;
  action: string;
}

export const AGENT_ORDER: AgentKey[] = ["qwen", "claude", "executor", "validator"];

export const AGENT_NAMES: Record<AgentKey, string> = {
  qwen: "Qwen Planner",
  claude: "Claude CAD Engineer",
  executor: "CAD Executor",
  validator: "Validator",
};

// Map an event to the agent it belongs to (or null for pure system events).
export function eventAgent(ev: GenEvent): AgentKey | null {
  if (ev.source === "qwen") return "qwen";
  if (ev.source === "claude_code") return "claude";
  if (ev.source === "cad_worker") {
    if ((ev.message || "").toLowerCase().includes("repair")) return "validator";
    return "executor";
  }
  return null;
}
