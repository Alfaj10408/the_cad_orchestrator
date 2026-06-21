import { useEffect } from "react";
import Sidebar from "./Sidebar";
import StatusBar from "./StatusBar";
import AgentRail from "./AgentRail";
import ChatCenter from "./ChatCenter";
import Composer from "./Composer";
import ViewerRight from "./ViewerRight";
import DiagnosticsBottom from "./DiagnosticsBottom";
import { useStore } from "../store/useStore";

export default function AppShell() {
  const pollHealth = useStore((s) => s.pollHealth);
  const phase = useStore((s) => s.phase);
  const cancel = useStore((s) => s.cancel);

  useEffect(() => {
    pollHealth();
    const h = setInterval(pollHealth, 10000);
    return () => clearInterval(h);
  }, [pollHealth]);

  return (
    <div className="shell">
      <Sidebar />
      <div className="main">
        <StatusBar />
        <div className="work">
          <div className="center">
            <AgentRail />
            <ChatCenter />
            {phase === "generating" && (
              <div className="cancel-row">
                <button className="ghost" onClick={cancel}>✕ Cancel generation</button>
              </div>
            )}
            <Composer />
          </div>
          <ViewerRight />
        </div>
        <DiagnosticsBottom />
      </div>
    </div>
  );
}
