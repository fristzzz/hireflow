export type Job = {
  id: string;
  name: string;
  jd_text: string;
  prompt: string;
  resume_count: number;
  run_count: number;
  created_at: string;
  updated_at: string;
};

export type Resume = {
  id: string;
  job_id: string;
  original_name: string;
  display_name?: string;
  mime_type: string;
  size_bytes: number;
  page_count: number | null;
  parse_status: string;
  parse_error: string | null;
  parsed_text_source?: "extracted" | "manual";
  parsed_text_updated_at?: string | null;
  created_at: string;
  updated_at?: string;
};

export type Run = {
  id: string;
  job_id: string;
  status: string;
  total_count: number;
  completed_count: number;
  failed_count: number;
  text_model: string;
  vision_model: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type Evidence = { text: string; location: string };
export type Criterion = {
  criterion_id?: string;
  criterion: string;
  weight?: number;
  score?: number | null;
  weighted_score?: number;
  status: "met" | "partial" | "not_met" | "unknown";
  evidence: Evidence[];
};
export type BackgroundHighlight = {
  kind: "company" | "school";
  name: string;
  bonus_points: number;
  evidence: Evidence[];
};

export type EvaluationCard = {
  scoring_version?: string;
  candidate: {
    name: string;
    email?: string | null;
    phone?: string | null;
    current_role?: string | null;
    years_experience?: number | null;
  };
  overall_score: number;
  match_level: "high" | "medium" | "low";
  confidence: number;
  summary: string;
  criteria: Criterion[];
  background_highlights?: BackgroundHighlight[];
  background_bonus?: number;
  strengths: string[];
  gaps: string[];
  risk_flags: string[];
  interview_questions: string[];
};

export type Evaluation = {
  id: string;
  run_id: string;
  resume_id: string;
  resume_name: string;
  status: string;
  result: EvaluationCard | null;
  error: string | null;
  attempts: number;
  review_status: "pending" | "advance" | "reject" | "hold";
  review_note: string;
  screened_at: string;
  updated_at: string;
};

export type EvaluationPage = {
  items: Evaluation[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
};

export type RunPage = {
  items: Run[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
};

export type TrashType = "job" | "resume" | "run" | "evaluation";

export type TrashItem = {
  item_type: TrashType;
  id: string;
  name: string;
  job_id: string;
  job_name: string;
  deleted_at: string;
  purge_at: string;
};

export type TrashPage = {
  items: TrashItem[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
};
