export type ParseQuality = {
  status: "passed" | "warning";
  characters: number;
  chunks: number;
  ocr_pages: number[];
  empty_pages: number[];
  warnings: string[];
  note: string;
};
export type Document = {
  file_type?: string;
  quality_report?: ParseQuality;
  id: string;
  name: string;
  size: number;
  pages: number;
  chunks: number;
  status: string;
  progress: number;
  error: string;
  vector_ready: boolean;
  active_revision: number;
  indexing_status: string;
  created_at: string;
};
export type Citation = {
  file_type?: string;
  kind?: string;
  revision?: number;
  id: number;
  chunk_id: string;
  document_id: string;
  document_name: string;
  page: number;
  heading: string;
  content: string;
  score: number;
};
export type Message = {
  model?: string;
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  mode: string;
  elapsed_ms: number;
  error?: boolean;
  warning?: string;
  retrieval_mode?: string;
  status?: string;
  approved_knowledge?: {
    id: string;
    revision: number;
    question: string;
    feedback_id: string;
  } | null;
};
export type Conversation = {
  id: string;
  title: string;
  created_at: string;
  document_ids?: string[];
};
export type Health = {
  models?: { id: string; label: string; model: string; ready: boolean }[];
  ocr?: { available: boolean; engine: string | null };
  generation_ready: boolean;
  embedding_ready: boolean;
  embedding_model?: string | null;
  model: string | null;
  mode: string;
};
