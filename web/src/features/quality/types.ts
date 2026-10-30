import type { Citation } from "../knowledge/types";
export type Feedback = {
  id: string;
  message_id: string;
  rating: "helpful" | "problem";
  category: string;
  comment: string;
  status: string;
  resolution: string;
  updated_at: string;
  snapshot: {
    question: string;
    answer: string;
    citations: Citation[];
    warning: string;
    retrieval_mode: string;
  };
  publication?: Publication | null;
  publication_sources?: Record<
    string,
    {
      items: (Citation & { is_current?: boolean; source_issue?: string })[];
      warning: string;
    }
  >;
  verification: {
    checked_at?: string;
    question?: string;
    answer_mode?: string;
    included_approved?: boolean;
    answer?: string;
    citations?: Citation[];
    warning?: string;
    retrieval_mode?: string;
    note?: string;
  };
};
export const categories: Record<string, string> = {
  incorrect: "回答错误",
  citation: "来源不符",
  missing: "资料缺失",
  other: "其他问题",
};
export const statuses: Record<string, string> = {
  open: "待处理",
  in_progress: "处理中",
  resolved: "已解决",
};

export type Publication = {
  id: string;
  question: string;
  answer: string;
  sources: Citation[];
  revision: number;
  status: "published" | "withdrawn";
  retrieval_active: boolean;
  review_note: string;
  updated_at: string;
};
