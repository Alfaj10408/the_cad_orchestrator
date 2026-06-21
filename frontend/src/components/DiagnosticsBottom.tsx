import { useState } from "react";
import { useStore } from "../store/useStore";

type Tab = "logs" | "claude" | "qwen" | "diag";

export default function DiagnosticsBottom() {
  const { events, bottomOpen, setBottomOpen, connection, elapsed, phase, error } = useStore((s) => ({
    events: s.events,
    bottomOpen: s.bottomOpen,
    setBottomOpen: s.setBottomOpen,
    connection: s.connection,
    elapsed: s.elapsed,
    phase: s.phase,
    error: s.error,
  }));
  const [tab, setTab] = useState<Tab>("logs");

  const filtered = events.filter((e) => {
    if (tab === "claude") return e.source === "claude_code";
    if (tab === "qwen") return e.source === "qwen";
    if (tab === "diag") return e.source === "system" || e.source === "cad_worker";
    return true;
  });

  const connClass = connection === "open" ? "ok" : connection === "closed" ? "off" : "bad";

  return (
    <div className={`diagnostics ${bottomOpen ? "open" : ""}`}>
      <div className="diag-bar" onClick={() => setBottomOpen(!bottomOpen)}>
        <span className="diag-caret">{bottomOpen ? "⌄" : "⌃"}</span>
        <strong>Diagnostics</strong>
        <span className="spacer" />
        {error && <span className="pill failed">{error.slice(0, 60)}</span>}
        <span className="badge"><span className="dot" /> {phase}</span>
        <span className={`badge ${connClass}`}><span className="dot" /> {connection}</span>
        <span className="badge">⏱ {elapsed}s</span>
      </div>
      {bottomOpen && (
        <>
          <div className="diag-tabs">
            {(["logs", "claude", "qwen", "diag"] as Tab[]).map((t) => (
              <button key={t} className={`diag-tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
                {t === "diag" ? "Diagnostics" : t === "claude" ? "Claude" : t === "qwen" ? "Qwen" : "Logs"}
              </button>
            ))}
          </div>
          <div className="diag-body">
            {filtered.length === 0 ? (
              <div className="muted tiny">No events.</div>
            ) : (
              filtered.map((e) => (
                <div key={e.id} className={`diag-line src-${e.source}`}>
                  <span className="diag-ts">{e.timestamp.slice(11, 19)}</span>
                  <span className="diag-type">{e.type}</span>
                  <span className="diag-msg">{e.delta || e.message || ""}</span>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}
