import { lazy, Suspense, useState } from "react";
import { useStore } from "../store/useStore";

const GLBViewer = lazy(() => import("./GLBViewer"));

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function ArtifactDrawer() {
  const { artifacts, drawerOpen, setDrawerOpen } = useStore((s) => ({
    artifacts: s.artifacts,
    drawerOpen: s.drawerOpen,
    setDrawerOpen: s.setDrawerOpen,
  }));
  const downloadable = artifacts.filter(
    (a) => ["cad", "mesh", "snapshot", "source", "report"].includes(a.category)
  );
  return (
    <div className={`drawer ${drawerOpen ? "open" : ""}`}>
      <div className="drawer-head">
        <strong>Downloads</strong>
        <button className="ghost" onClick={() => setDrawerOpen(false)}>✕</button>
      </div>
      <div className="drawer-body">
        {downloadable.length === 0 && <div className="muted tiny">No files yet.</div>}
        {downloadable.map((a) => (
          <a key={a.relative_path} className="drawer-item" href={a.download_url} download target="_blank" rel="noreferrer">
            <span className="di-cat">{a.category}</span>
            <span className="di-name">{a.name}</span>
            <span className="di-size">{fmtSize(a.size_bytes)}</span>
          </a>
        ))}
      </div>
    </div>
  );
}

export default function ViewerRight() {
  const { glbUrl, wireframe, bbox, toggleWireframe, toggleBbox, metadata, setDrawerOpen, phase } =
    useStore((s) => ({
      glbUrl: s.glbUrl,
      wireframe: s.wireframe,
      bbox: s.bbox,
      toggleWireframe: s.toggleWireframe,
      toggleBbox: s.toggleBbox,
      metadata: s.metadata,
      setDrawerOpen: s.setDrawerOpen,
      phase: s.phase,
    }));
  const [nonce, setNonce] = useState(0);
  const dims = metadata?.dimensions_mm;

  return (
    <section className="viewer-right">
      <div className="viewer-toolbar">
        <span className="vt-title">Live CAD Preview</span>
        <span className="spacer" />
        <button className={`vt-btn ${wireframe ? "on" : ""}`} onClick={toggleWireframe}>⊞ Wireframe</button>
        <button className={`vt-btn ${bbox ? "on" : ""}`} onClick={toggleBbox}>⬚ Bounds</button>
        <button className="vt-btn" onClick={() => setNonce((n) => n + 1)}>⤢ Fit</button>
        <button className="vt-btn" onClick={() => setDrawerOpen(true)}>▤ Downloads</button>
      </div>

      <div className="viewer-canvas">
        {glbUrl ? (
          <Suspense fallback={<div className="viewer-empty">Loading 3D…</div>}>
            <GLBViewer key={`${glbUrl}-${nonce}`} url={glbUrl} wireframe={wireframe} bbox={bbox} />
          </Suspense>
        ) : (
          <div className="viewer-empty">
            {phase === "generating" ? "Model will appear here when ready…" : "Your 3D model will appear here"}
          </div>
        )}
      </div>

      <div className="viewer-meta">
        <div className="meta-block">
          <div className="section-label">Dimensions</div>
          {dims ? (
            <div className="dims">
              {dims.x} × {dims.y} × {dims.z} <span className="muted">mm</span>
            </div>
          ) : (
            <div className="muted tiny">—</div>
          )}
          {metadata?.available && (
            <div className="meta-stats">
              <span>solids {metadata.solids ?? "—"}</span>
              <span>faces {metadata.faces ?? "—"}</span>
              <span>edges {metadata.edges ?? "—"}</span>
            </div>
          )}
        </div>
        <div className="meta-block">
          <div className="section-label">Part hierarchy</div>
          {metadata?.available ? (
            <div className="hierarchy">
              <div className="hier-root">◈ Model ({metadata.kind ?? "part"})</div>
              {Array.from({ length: metadata.solids ?? 0 }).map((_, i) => (
                <div key={i} className="hier-leaf">└ solid {i + 1}</div>
              ))}
            </div>
          ) : (
            <div className="muted tiny">—</div>
          )}
        </div>
      </div>

      <ArtifactDrawer />
    </section>
  );
}
