export interface DocSummary {
  id: number;
  filename: string;
  status: string;
  progress: number;
  page_count: number;
  size_bytes: number;
  options: Record<string, unknown> & { llm_stats?: { proposees: number; appliquees: number; rejetees: number; ignoree: boolean } };
  error: string | null;
  created_at: string;
  finished_at: string | null;
  has_export: boolean;
}

export interface DocDetail extends DocSummary {
  pages: { index: number; status: string; mean_confidence: number | null }[];
  pending_corrections: number;
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
}

export const ACTIVE_STATUSES = ["queued", "waiting_gpu", "preprocessing", "ocr", "correcting", "exporting"];
