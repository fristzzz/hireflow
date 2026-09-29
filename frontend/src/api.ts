import type { Evaluation, EvaluationPage, Job, JobPasteResult, PoolAssignResult, PoolPasteResult, PoolResume, Resume, Run, RunPage, ScoringKind, ScoringPreview, TrashPage, TrashType } from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {
      // 非 JSON 错误保持通用提示。
    }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function requestText(url: string): Promise<string> {
  const response = await fetch(url);
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {
      // 非 JSON 错误保持通用提示。
    }
    throw new Error(message);
  }
  return response.text();
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => request<{ status: string; model_configured: boolean; text_model: string; vision_model: string; vision_enabled: boolean }>("/api/health"),
  previewScoring: (prompt: string) => request<ScoringPreview>("/api/scoring-preview", json({ prompt })),
  jobs: () => request<Job[]>("/api/jobs"),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  createJob: (payload: Pick<Job, "name" | "jd_text" | "prompt"> & { scoring_profile?: { name: string; weight: number; kind: ScoringKind }[] }) =>
    request<Job>("/api/jobs", json(payload)),
  updateJob: (id: string, payload: Partial<Pick<Job, "name" | "jd_text" | "prompt">> & { scoring_profile?: { name: string; weight: number; kind: ScoringKind }[] }) =>
    request<Job>(`/api/jobs/${id}`, { ...json(payload), method: "PATCH" }),
  deleteJob: (id: string) => request<void>(`/api/jobs/${id}`, { method: "DELETE" }),
  restoreJob: (id: string) => request<{ restored: boolean }>(`/api/trash/job/${id}/restore`, { method: "POST" }),
  resumes: (jobId: string) => request<Resume[]>(`/api/jobs/${jobId}/resumes`),
  poolResumes: (scope: "pool" | "all" = "pool") => request<PoolResume[]>(`/api/pool/resumes?scope=${scope}`),
  assignPool: (payload: { resume_ids: string[]; job_id: string; evaluate: boolean }) =>
    request<PoolAssignResult>("/api/pool/assign", json(payload)),
  pastePoolResume: (payload: { text: string; name?: string; confirm?: boolean }) =>
    request<PoolPasteResult>("/api/pool/resumes/paste", json(payload)),
  pasteJobResume: (jobId: string, payload: { text: string; name?: string; confirm?: boolean }) =>
    request<JobPasteResult>(`/api/jobs/${jobId}/resumes/paste`, json(payload)),
  resumeText: (resumeId: string) => requestText(`/api/resumes/${resumeId}/text`),
  updateResume: (resumeId: string, payload: { display_name?: string; parsed_text?: string }) =>
    request<Resume>(`/api/resumes/${resumeId}`, { ...json(payload), method: "PATCH" }),
  reparseResume: (resumeId: string) => request<Resume>(`/api/resumes/${resumeId}/reparse`, { method: "POST" }),
  deleteResume: (resumeId: string) => request<void>(`/api/resumes/${resumeId}`, { method: "DELETE" }),
  upload: async (jobId: string, files: File[]) => {
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    return request<{ uploaded: Resume[]; rejected: { filename: string; reason: string; recoverable_resume_id?: string | null }[] }>(
      `/api/jobs/${jobId}/resumes`,
      { method: "POST", body: form },
    );
  },
  runs: (jobId: string, params = new URLSearchParams()) => request<RunPage>(`/api/jobs/${jobId}/runs?${params.toString()}`),
  run: (id: string) => request<Run>(`/api/runs/${id}`),
  createRun: (jobId: string, resumeIds: string[]) => request<Run>(`/api/jobs/${jobId}/runs`, json({ resume_ids: resumeIds })),
  cancelRun: (id: string) => request<Run>(`/api/runs/${id}/cancel`, { method: "POST" }),
  retryRun: (id: string) => request<Run>(`/api/runs/${id}/retry-failed`, { method: "POST" }),
  deleteRun: (id: string) => request<void>(`/api/runs/${id}`, { method: "DELETE" }),
  evaluations: (runId: string) => request<Evaluation[]>(`/api/runs/${runId}/evaluations`),
  jobEvaluations: (jobId: string, params: URLSearchParams) =>
    request<EvaluationPage>(`/api/jobs/${jobId}/evaluations?${params.toString()}`),
  review: (id: string, status: Evaluation["review_status"], note: string) =>
    request<Evaluation>(`/api/evaluations/${id}/review`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, note }),
    }),
  batchReview: (evaluationIds: string[], status: Evaluation["review_status"]) =>
    request<{ updated_count: number }>("/api/evaluations/review", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ evaluation_ids: evaluationIds, status }),
    }),
  rescore: (evaluationIds: string[], jobId?: string) => request<Run>("/api/evaluations/rescore", json({ evaluation_ids: evaluationIds, job_id: jobId })),
  deleteEvaluation: (id: string) => request<void>(`/api/evaluations/${id}`, { method: "DELETE" }),
  batchDeleteEvaluations: (evaluationIds: string[]) =>
    request<{ updated_count: number }>("/api/evaluations/delete", json({ evaluation_ids: evaluationIds })),
  trash: (params: URLSearchParams) => request<TrashPage>(`/api/trash?${params.toString()}`),
  restoreTrash: (itemType: TrashType, id: string) =>
    request<{ restored: boolean }>(`/api/trash/${itemType}/${id}/restore`, { method: "POST" }),
  purgeTrash: (itemType: TrashType, id: string) =>
    request<void>(`/api/trash/${itemType}/${id}`, { method: "DELETE" }),
  emptyTrash: () => request<{ updated_count: number }>("/api/trash", { method: "DELETE" }),
};
