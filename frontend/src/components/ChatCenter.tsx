import { useEffect, useRef } from "react";
import { useStore } from "../store/useStore";

const ROLE_LABEL: Record<string, string> = {
  user: "You",
  qwen: "Qwen Planner",
  claude: "Claude CAD Engineer",
  system: "System",
  brief: "Engineering Brief",
};

function Clarification() {
  const { questions, answers, setAnswer, submitAnswers, phase } = useStore((s) => ({
    questions: s.questions,
    answers: s.answers,
    setAnswer: s.setAnswer,
    submitAnswers: s.submitAnswers,
    phase: s.phase,
  }));
  if (phase !== "clarifying" || questions.length === 0) return null;

  return (
    <div className="msg msg-clarify">
      <div className="msg-role">A few quick questions</div>
      <div className="q-grid">
        {questions.map((q) => (
          <div key={q.id} className="q-card">
            <label>{q.question}</label>
            {q.options.length ? (
              <select value={answers[q.id] ?? ""} onChange={(e) => setAnswer(q.id, e.target.value)}>
                <option value="">— choose —</option>
                {q.options.map((o) => (
                  <option key={o} value={o}>{o}</option>
                ))}
              </select>
            ) : (
              <input className="text" value={answers[q.id] ?? ""} onChange={(e) => setAnswer(q.id, e.target.value)} />
            )}
          </div>
        ))}
      </div>
      <button className="primary" onClick={submitAnswers} style={{ marginTop: 12 }}>
        Continue
      </button>
    </div>
  );
}

export default function ChatCenter() {
  const messages = useStore((s) => s.messages);
  const phase = useStore((s) => s.phase);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, phase]);

  return (
    <div className="chat-thread">
      {messages.length === 0 && phase === "idle" && (
        <div className="empty-hero">
          <h1>What do you want to design?</h1>
          <p>Describe a part in plain language. Trelis plans it, writes the CAD, and builds it.</p>
        </div>
      )}
      {messages.map((m) => (
        <div key={m.id} className={`msg msg-${m.role}`}>
          <div className="msg-role">{ROLE_LABEL[m.role] ?? m.role}</div>
          <div className="msg-text">
            {m.text}
            {m.streaming && <span className="caret" />}
          </div>
        </div>
      ))}
      <Clarification />
      <div ref={endRef} />
    </div>
  );
}
