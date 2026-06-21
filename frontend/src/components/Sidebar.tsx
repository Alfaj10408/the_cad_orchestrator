import { useStore } from "../store/useStore";

const NAV = [
  { icon: "▣", label: "Projects" },
  { icon: "🕘", label: "History" },
  { icon: "★", label: "Saved Designs" },
  { icon: "⚙", label: "Settings" },
];

export default function Sidebar() {
  const reset = useStore((s) => s.reset);
  const health = useStore((s) => s.health);
  const provider = health?.generation_provider ?? "—";

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-dot" />
        <span className="brand-name">Trelis</span>
      </div>

      <button className="new-design" onClick={reset}>
         + New Design
      </button>

      <nav className="nav">
        {NAV.map((n, i) => (
          <div key={n.label} className={`nav-item ${i === 0 ? "active" : ""}`}>
            <span className="nav-ico">{n.icon}</span>
            {n.label}
          </div>
        ))}
      </nav>

      <div className="sidebar-foot">
        <div className="muted tiny">Engine</div>
        <div className="tiny">{provider}</div>
      </div>
    </aside>
  );
}
