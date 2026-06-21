import { create } from "zustand";
import * as api from "../api/projects";
import type { Artifact, ClarificationQuestion, GeneralHealth, StepMetadata } from "../api/projects";
import {
  AGENT_NAMES,
  AGENT_ORDER,
  eventAgent,
  type Agent,
  type AgentKey,
  type GenEvent,
} from "../types/events";

export type Phase =
  | "idle"
  | "analyzing"
  | "clarifying"
  | "generating"
  | "completed"
  | "failed"
  | "cancelled";

export type ChatRole = "user" | "qwen" | "claude" | "system" | "brief";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  text: string;
  streaming?: boolean;
}

function freshAgents(): Record<AgentKey, Agent> {
  return AGENT_ORDER.reduce((acc, key) => {
    acc[key] = { key, name: AGENT_NAMES[key], status: "idle", action: "" };
    return acc;
  }, {} as Record<AgentKey, Agent>);
}

const TERMINAL = new Set(["job.completed", "job.failed", "job.cancelled"]);
const DEMO_ANSWERS: Record<string, string> = {
  dimensions: "100 x 100 x 20 mm",
  units: "mm",
  material: "PLA",
  intent: "concept CAD model",
};

interface StoreState {
  // session (internal — never rendered)
  projectId?: string;
  jobId?: string;
  // input + chat
  prompt: string;
  messages: ChatMessage[];
  questions: ClarificationQuestion[];
  answers: Record<string, string>;
  // generation
  phase: Phase;
  agents: Record<AgentKey, Agent>;
  events: GenEvent[];
  connection: "idle" | "connecting" | "open" | "closed";
  elapsed: number;
  error?: string;
  // viewer / artifacts
  glbUrl?: string;
  artifacts: Artifact[];
  metadata?: StepMetadata;
  // health
  health: GeneralHealth | null;
  // ui
  bottomOpen: boolean;
  drawerOpen: boolean;
  wireframe: boolean;
  bbox: boolean;

  // actions
  setPrompt: (v: string) => void;
  setBottomOpen: (v: boolean) => void;
  setDrawerOpen: (v: boolean) => void;
  toggleWireframe: () => void;
  toggleBbox: () => void;
  setAnswer: (id: string, v: string) => void;
  pollHealth: () => Promise<void>;
  generateDesign: () => Promise<void>;
  submitAnswers: () => Promise<void>;
  cancel: () => Promise<void>;
  reset: () => void;
}

let es: EventSource | null = null;
let timer: ReturnType<typeof setInterval> | null = null;
const seen = new Set<number>();

export const useStore = create<StoreState>((set, get) => {
  function pushMsg(m: ChatMessage) {
    set((s) => ({ messages: [...s.messages, m] }));
  }

  function appendDelta(role: ChatRole, delta: string) {
    set((s) => {
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last && last.role === role && last.streaming) {
        msgs[msgs.length - 1] = { ...last, text: last.text + delta };
      } else {
        msgs.push({ id: `m${Date.now()}-${Math.random()}`, role, text: delta, streaming: true });
      }
      return { messages: msgs };
    });
  }

  function setAgent(key: AgentKey, patch: Partial<Agent>) {
    set((s) => ({ agents: { ...s.agents, [key]: { ...s.agents[key], ...patch } } }));
  }

  async function refreshArtifacts() {
    const pid = get().projectId;
    if (!pid) return;
    const list = await api.listArtifacts(pid).catch(() => null);
    if (!list) return;
    const glb = list.artifacts.find((a) => a.name.toLowerCase().endsWith(".glb"));
    // Cache-bust per job so a regenerated model reloads in the GLTF cache.
    const glbUrl = glb ? `${glb.download_url}&job=${get().jobId ?? ""}` : get().glbUrl;
    set({ artifacts: list.artifacts, glbUrl });
    const meta = await api.stepMetadata(pid).catch(() => null);
    if (meta) set({ metadata: meta });
  }

  function handleEvent(ev: GenEvent) {
    if (typeof ev.id === "number") {
      if (seen.has(ev.id)) return;
      seen.add(ev.id);
    }
    set((s) => ({ events: [...s.events, ev] }));

    const agent = eventAgent(ev);
    if (agent) {
      const action = ev.message || ev.type;
      if (ev.type.endsWith(".completed") || ev.type === "cad.execution.completed") {
        setAgent(agent, { status: "done", action });
      } else {
        setAgent(agent, { status: "active", action });
      }
    }

    // Chat surface.
    if (ev.type === "text.delta" && ev.delta) {
      appendDelta("claude", ev.delta);
    } else if (ev.source === "qwen" && ev.message) {
      pushMsg({ id: `q${ev.id}`, role: "qwen", text: ev.message });
    } else if (ev.type === "cad.execution.started" || ev.type === "cad.execution.completed") {
      pushMsg({ id: `e${ev.id}`, role: "system", text: `⚙ ${ev.message}` });
    }

    if (ev.type === "claude.started") setAgent("claude", { status: "active", action: "Generating CAD code" });
    if (ev.type === "artifact.created") refreshArtifacts();

    if (TERMINAL.has(ev.type)) {
      if (ev.type === "job.completed") {
        set({ phase: "completed" });
        for (const k of AGENT_ORDER) setAgent(k, { status: "done" });
        refreshArtifacts();
      } else if (ev.type === "job.failed") {
        set({ phase: "failed", error: ev.message || "generation failed" });
        if (agent) setAgent(agent, { status: "error" });
      } else {
        set({ phase: "cancelled" });
      }
      closeStream();
    }
  }

  function openStream(projectId: string, jobId: string) {
    closeStream();
    seen.clear();
    set({ connection: "connecting", elapsed: 0 });
    const url = `/api/projects/${projectId}/jobs/${jobId}/events`;
    es = new EventSource(url);
    es.onopen = () => set({ connection: "open" });
    es.onerror = () => {
      // EventSource auto-reconnects (sends Last-Event-ID); reflect state.
      if (get().phase === "generating") set({ connection: "connecting" });
    };
    const onMsg = (e: MessageEvent) => {
      try {
        const ev = JSON.parse(e.data) as GenEvent & { type: string };
        if (ev.type === "heartbeat") return;
        handleEvent(ev);
      } catch {
        /* ignore malformed */
      }
    };
    es.onmessage = onMsg;
    [
      "job.started", "planner.started", "planner.completed", "claude.started",
      "text.delta", "tool.started", "tool.completed", "file.created", "file.updated",
      "cad.execution.started", "cad.execution.log", "cad.execution.completed",
      "artifact.created", "job.completed", "job.failed", "job.cancelled",
    ].forEach((n) => es!.addEventListener(n, onMsg as EventListener));

    timer = setInterval(() => set((s) => ({ elapsed: s.elapsed + 1 })), 1000);
  }

  function closeStream() {
    if (es) { es.close(); es = null; }
    if (timer) { clearInterval(timer); timer = null; }
    set({ connection: "closed" });
  }

  return {
    prompt: "",
    messages: [],
    questions: [],
    answers: {},
    phase: "idle",
    agents: freshAgents(),
    events: [],
    connection: "idle",
    elapsed: 0,
    artifacts: [],
    health: null,
    bottomOpen: false,
    drawerOpen: false,
    wireframe: false,
    bbox: false,

    setPrompt: (v) => set({ prompt: v }),
    setBottomOpen: (v) => set({ bottomOpen: v }),
    setDrawerOpen: (v) => set({ drawerOpen: v }),
    toggleWireframe: () => set((s) => ({ wireframe: !s.wireframe })),
    toggleBbox: () => set((s) => ({ bbox: !s.bbox })),
    setAnswer: (id, v) => set((s) => ({ answers: { ...s.answers, [id]: v } })),

    pollHealth: async () => {
      const h = await api.generalHealth().catch(() => null);
      set({ health: h });
    },

    reset: () => {
      closeStream();
      seen.clear();
      set({
        projectId: undefined, jobId: undefined, prompt: "", messages: [],
        questions: [], answers: {}, phase: "idle", agents: freshAgents(),
        events: [], connection: "idle", elapsed: 0, error: undefined,
        glbUrl: undefined, artifacts: [], metadata: undefined, drawerOpen: false,
      });
    },

    generateDesign: async () => {
      const prompt = get().prompt.trim();
      if (!prompt) return;
      set({
        phase: "analyzing", error: undefined, messages: [], events: [],
        agents: freshAgents(), artifacts: [], glbUrl: undefined, metadata: undefined,
      });
      pushMsg({ id: `u${Date.now()}`, role: "user", text: prompt });
      try {
        const pid = (await api.createProject()).project_id;
        set({ projectId: pid });
        setAgent("qwen", { status: "active", action: "Understanding request" });
        const res = await api.analyze(pid, prompt);
        if (res.brief?.summary) {
          pushMsg({ id: `b${Date.now()}`, role: "brief", text: res.brief.summary });
        }
        if (!res.ready_to_generate && res.questions.length) {
          set({ phase: "clarifying", questions: res.questions, answers: {} });
          setAgent("qwen", { status: "active", action: "Needs clarification" });
          return; // wait for submitAnswers
        }
        await launch();
      } catch (e) {
        set({ phase: "failed", error: e instanceof Error ? e.message : String(e) });
      }
    },

    submitAnswers: async () => {
      const pid = get().projectId;
      if (!pid) return;
      set({ phase: "analyzing" });
      try {
        const ans = { ...get().answers };
        for (const q of get().questions) {
          if (!ans[q.id]) ans[q.id] = DEMO_ANSWERS[q.id] || (q.options[0] ?? "");
        }
        const res = await api.submitClarifications(pid, ans);
        set({ questions: res.ready_to_generate ? [] : res.questions });
        if (res.ready_to_generate) await launch();
        else set({ phase: "clarifying" });
      } catch (e) {
        set({ phase: "failed", error: e instanceof Error ? e.message : String(e) });
      }
    },

    cancel: async () => {
      const { projectId, jobId } = get();
      if (projectId && jobId) await api.cancelJob(projectId, jobId).catch(() => null);
      set({ phase: "cancelled" });
      closeStream();
    },
  };

  async function launch() {
    const pid = get().projectId!;
    set({ phase: "generating", questions: [] });
    const gen = await api.generate(pid, "qwen_claude_code");
    set({ jobId: gen.job_id });
    openStream(pid, gen.job_id);
  }
});
