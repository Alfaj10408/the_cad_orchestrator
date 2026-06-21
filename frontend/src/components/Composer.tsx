import { useStore } from "../store/useStore";

const EXAMPLES = [
  "A 40 x 30 x 10 mm calibration block",
  "A 3D printable spur gear, 24 teeth",
  "A mounting bracket with two M4 holes",
];

export default function Composer() {
  const { prompt, setPrompt, generateDesign, phase } = useStore((s) => ({
    prompt: s.prompt,
    setPrompt: s.setPrompt,
    generateDesign: s.generateDesign,
    phase: s.phase,
  }));
  const busy = phase === "analyzing" || phase === "generating";

  return (
    <div className="composer">
      <div className="chips">
        {EXAMPLES.map((ex) => (
          <span key={ex} className="chip" onClick={() => setPrompt(ex)}>
            {ex}
          </span>
        ))}
      </div>
      <div className="composer-row">
        <textarea
          className="composer-input"
          placeholder="Describe the part you want to design…"
          value={prompt}
          rows={2}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) generateDesign();
          }}
        />
        <button className="generate-btn" onClick={generateDesign} disabled={busy || !prompt.trim()}>
          {busy ? <span className="spin" /> : "Generate Design"}
        </button>
      </div>
    </div>
  );
}
