import { apiGet, apiPost } from "./client";

export interface ClarificationQuestion {
  id: string;
  question: string;
  options: string[];
  required: boolean;
}

export interface Brief {
  project_id: string;
  prompt: string;
  intent: string;
  summary: string;
  parameters?: Record<string, unknown>;
  assumptions?: string[];
  ready_to_generate?: boolean;
}

export interface AnalyzeResponse {
  project_id: string;
  intent: string;
  confidence: number;
  ready_to_generate: boolean;
  questions: ClarificationQuestion[];
  brief?: Brief | null;
}

export interface GenerationStatus {
  project_id: string;
  status: string;
  stage?: string | null;
  job_id?: string | null;
  created_at?: string | null;
}

export interface Artifact {
  relative_path: string;
  name: string;
  category: string;
  size_bytes: number;
  download_url: string;
  viewer_url?: string | null;
}

export interface ArtifactList {
  project_id: string;
  artifacts: Artifact[];
}

export function createProject() {
  return apiPost<{ project_id: string }>("/api/projects", {});
}

export function analyze(projectId: string, prompt: string) {
  return apiPost<AnalyzeResponse>(`/api/projects/${projectId}/analyze`, { prompt });
}

export function submitClarifications(
  projectId: string,
  answers: Record<string, string | string[]>
) {
  return apiPost<AnalyzeResponse>(`/api/projects/${projectId}/clarifications`, {
    answers,
  });
}

export interface GenerateResponse {
  project_id: string;
  job_id: string;
  status: string;
  stage: string;
  message: string;
}

export function generate(projectId: string, generationMode: string) {
  return apiPost<GenerateResponse>(`/api/projects/${projectId}/generate`, {
    generation_mode: generationMode,
  });
}

export function cancelJob(projectId: string, jobId: string) {
  return apiPost<Record<string, unknown>>(
    `/api/projects/${projectId}/jobs/${jobId}/cancel`
  );
}

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

export function generationStatus(projectId: string) {
  return apiGet<GenerationStatus>(`/api/projects/${projectId}/generation-status`);
}

export function listArtifacts(projectId: string) {
  return apiGet<ArtifactList>(`/api/projects/${projectId}/artifacts`);
}

export interface StepMetadata {
  available: boolean;
  dimensions_mm?: { x: number; y: number; z: number };
  bounds?: { min: number[]; max: number[] };
  solids?: number;
  occurrences?: number;
  faces?: number;
  edges?: number;
  kind?: string;
}

export function stepMetadata(projectId: string) {
  return apiGet<StepMetadata>(`/api/projects/${projectId}/metadata`);
}

export interface RunWorkerResult {
  processed_count: number;
  job_ids: string[];
}

export function runWorkerOnce() {
  return apiPost<RunWorkerResult>("/api/workers/run-once");
}

export interface BackendHealth {
  status: string;
}

export interface OrchestratorHealth {
  enabled: boolean;
  ok: boolean;
  detail?: string;
  model?: string | null;
  base_url?: string;
}

export function backendHealth() {
  return apiGet<BackendHealth>("/api/health");
}

export function orchestratorHealth() {
  return apiGet<OrchestratorHealth>("/api/health/orchestrator");
}

export interface HealthComponents {
  backend: { ok: boolean };
  qwen_planner: { enabled: boolean; ok: boolean };
  claude_code: { enabled: boolean; ok: boolean };
  cad_worker: { ok: boolean };
  viewer: { base_url: string };
}

export interface GeneralHealth {
  status: string;
  generation_provider: string;
  components: HealthComponents;
}

export function generalHealth() {
  return apiGet<GeneralHealth>("/api/health");
}

export interface ClaudeCodeHealth {
  enabled: boolean;
  installed: boolean;
  authenticated: boolean;
  binary?: string;
  version?: string | null;
  mode?: string;
  detail?: string;
}

export function claudeCodeHealth() {
  return apiGet<ClaudeCodeHealth>("/api/health/claude-code");
}
