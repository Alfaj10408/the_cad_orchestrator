import { useStore } from "../store/useStore";
import { AGENT_ORDER } from "../types/events";

const ICON: Record<string, string> = {
  qwen: "🧠",
  claude: "✦",
  executor: "⚙",
  validator: "🔎",
};

export default function AgentRail() {
  const agents = useStore((s) => s.agents);
  return (
    <div className="agent-rail">
      {AGENT_ORDER.map((k) => {
        const a = agents[k];
        return (
          <div key={k} className={`agent-chip ${a.status}`}>
            <span className="agent-ico">{ICON[k]}</span>
            <div className="agent-body">
              <div className="agent-name">{a.name}</div>
              <div className="agent-action">
                {a.status === "active" && <span className="spin sm" />}
                {a.action || (a.status === "done" ? "done" : "idle")}
              </div>
            </div>
            <span className={`agent-dot ${a.status}`} />
          </div>
        );
      })}
    </div>
  );
}
