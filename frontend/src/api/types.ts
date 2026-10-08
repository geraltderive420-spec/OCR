export interface DocSummary {
  id: number;
  filename: string;
  status: string;
  progress: number;
  page_count: number;
  size_bytes: number;
  options: Record<string, unknown> & {
    llm_stats?: { proposees: number; appliquees: number; rejetees: number; ignoree: boolean };
    second_stats?: { relus: number; remplaces: number; desaccords: number; ignoree: boolean };
    review?: boolean;
  };
  error: string | null;
  created_at: string;
  finished_at: string | null;
  started_at: string | null;
  updated_at: string | null;
  batch_id: number | null;
  batch_name: string | null;
  cancel_requested: boolean;
  has_export: boolean;
}

export interface DocDetail extends DocSummary {
  pages: { index: number; status: string; mean_confidence: number | null }[];
  pending_corrections: number;
  to_check: number;
  queue_position: number | null;
}

export interface DocPage {
  items: DocSummary[];
  total: number;
  page: number;
  page_size: number;
  counts: Record<string, number>;
}

export interface BatchSummary {
  id: number;
  name: string;
  options: Record<string, unknown>;
  created_at: string;
  total: number;
  counts: Record<"queued" | "running" | "review" | "done" | "failed" | "cancelled", number>;
  pages: number;
  progress: number;
  active: number;
  eta_seconds: number | null;
  exportable: number;
}

export interface BatchDetail extends BatchSummary {
  documents: DocSummary[];
}

export interface Correction {
  id: number;
  source: string;
  before: string;
  after: string;
  status: "proposed" | "accepted" | "rejected" | "auto_applied" | "reverted";
  model: string | null;
}

export interface BlockData {
  id: number;
  order: number;
  label: string;
  bbox: [number, number, number, number];
  html: string;
  html_ocr: string;
  confidence: number | null;
  direction: "ltr" | "rtl";
  to_check: boolean;
  error: boolean;
  has_crop: boolean;
  corrections: Correction[];
}

export interface PageData {
  index: number;
  width: number;
  height: number;
  status: string;
  image_url: string | null;
  blocks: BlockData[];
}

export interface Options {
  preprocess: string[];
  llm_modes: string[];
  ollama_available: boolean;
  ollama_models: string[];
  default_model: string;
  max_upload_mb: number;
  max_pages: number;
  fonts: string[];
  default_font: string;
  default_preprocess: string;
  accepted: string[];
}

export interface ServiceStatus {
  worker_alive: boolean;
  worker_state: string;
  engine: string;
  queue: number;
  my_active: number;
}

export const ACTIVE_STATUSES = ["queued", "waiting_gpu", "preprocessing", "ocr", "correcting", "exporting"];
