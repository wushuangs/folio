export type Document = {
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
};
export type Conversation = {
  id: string;
  title: string;
  created_at: string;
  document_ids?: string[];
};
export type Health = {
  generation_ready: boolean;
  embedding_ready: boolean;
  model: string | null;
  mode: string;
};
