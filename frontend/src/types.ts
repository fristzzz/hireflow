export type ScoringKind = "core" | "knockout" | "bonus";

export type ScoringDimension = {
  criterion_id: string;
  name: string;
  weight: number;
  kind?: ScoringKind;
  knockout?: boolean;
};

export type ScoringPreview = {
  source: "prompt" | "default" | "profile";
  dimensions: ScoringDimension[];
};

export type Job = {
  id: string;
  name: string;
  jd_text: string;
  prompt: string;
  resume_count: number;
  run_count: number;
  scoring_dimensions?: ScoringDimension[];
  scoring_source?: "prompt" | "default" | "profile" | "invalid";
  scoring_error?: string | null;
  created_at: string;
  updated_at: string;
};

export type Resume = {
  id: string;
  job_id: string | null;
  original_name: string;
  display_name?: string;
  mime_type: string;
  size_bytes: number;
  page_count: number | null;
  parse_status: string;
  parse_error: string | null;
  source?: "upload" | "email" | "paste";
  parsed_text_source?: "extracted" | "manual";
  parsed_text_updated_at?: string | null;
  created_at: string;
  updated_at?: string;
};

export type PoolEvaluationSummary = {
  overall_score: number | null;
  match_level: string | null;
  review_status: Evaluation["review_status"];
  gender?: "male" | "female" | null;
  age?: number | null;
};

export type PoolEvaluatedJob = {
  job_id: string;
  job_name: string;
};

export type PoolResume = {
  id: string;
  job_id: string | null;
  job_name: string | null;
  original_name: string;
  display_name: string;
  source_title?: string | null;
  mime_type: string;
  size_bytes: number;
  page_count: number | null;
  parse_status: string;
  parse_error: string | null;
  source: "upload" | "email" | "paste";
  created_at: string;
  evaluation: PoolEvaluationSummary | null;
  evaluation_status?: string | null;
  evaluated_jobs?: PoolEvaluatedJob[];
};

export type PoolPasteDuplicate = {
  id: string;
  name: string;
  source: string;
  created_at: string;
  matched_fields: string[];
};

export type PoolPasteResult = {
  created: boolean;
  resume: PoolResume | null;
  duplicates: PoolPasteDuplicate[];
};

export type JobPasteResult = {
  created: boolean;
  resume: Resume | null;
  duplicates: PoolPasteDuplicate[];
  run: Run | null;
};

export type PoolAssignResult = {
  assigned: number;
  skipped: { resume_id: string; filename: string; reason: string }[];
  run: Run | null;
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
  knockout?: boolean;
  kind?: ScoringKind;
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
    gender?: "male" | "female" | null;
    age?: number | null;
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
