import { useStore } from "../store/useStore";

type BadgeState = "ok" | "off" | "bad";

function Badge({ label, state }: { label: string; state: BadgeState }) {
  return (
    <span className={`badge ${state}`}>
      <span className="dot" />
      {label}
    </span>
  );
}

export default function StatusBar() {
  const health = useStore((s) => s.health);
  const connection = useStore((s) => s.connection);
  const c = health?.components;

  const backend: BadgeState = health ? "ok" : "bad";
  const qwen: BadgeState = !c?.qwen_planner.enabled ? "off" : c.qwen_planner.ok ? "ok" : "bad";
  const claude: BadgeState = !c?.claude_code.enabled ? "off" : c.claude_code.ok ? "ok" : "bad";
  const worker: BadgeState = c?.cad_worker.ok ? "ok" : "bad";

  return (
    <header className="topbar">
      <span className="topbar-title">AI Mechanical Design Studio</span>
      <span className="spacer" />
      <Badge label="Backend" state={backend} />
      <Badge label="Qwen" state={qwen} />
      <Badge label="Claude Code" state={claude} />
      <Badge label="CAD Engine" state={worker} />
      {connection !== "idle" && (
        <span className={`badge ${connection === "open" ? "ok" : connection === "closed" ? "off" : "bad"}`}>
          <span className="dot" /> live
        </span>
      )}
    </header>
  );
}
