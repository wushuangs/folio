"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { BookOpenCheck } from "lucide-react";
import { api } from "../knowledge/api";
import type { Publication } from "./types";

export function PublishedKnowledgeList() {
  const [items, setItems] = useState<(Publication & { feedback_id: string })[]>(
    [],
  );
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const load = () =>
      void api<(Publication & { feedback_id: string })[]>("/knowledge/approved")
        .then((rows) => {
          if (active) {
            setItems(rows);
            setError("");
          }
        })
        .catch((e) => {
          if (active)
            setError(e instanceof Error ? e.message : "无法加载正式知识");
        });
    load();
    window.addEventListener("focus", load);
    return () => {
      active = false;
      window.removeEventListener("focus", load);
    };
  }, []);
  return (
    <section className="approved-knowledge-list" aria-label="已审核问答知识">
      <h3>
        <BookOpenCheck size={17} />
        已审核问答知识
      </h3>
      <p>
        人工核对的标准答案与原文依据，发布后直接参与检索，无需向量重建。展示最近
        100 条发布记录。
      </p>
      {error && <p role="alert">{error}</p>}
      {!items.length && !error && (
        <p>暂无正式问答。可在“质量与反馈”中编辑标准答案、审核来源并发布。</p>
      )}
      {items.map((row) => (
        <details key={row.id}>
          <summary>
            {row.question}
            <span>
              v{row.revision} ·{" "}
              {row.status === "withdrawn"
                ? "已撤回"
                : row.retrieval_active
                  ? "检索中"
                  : "来源失效"}
            </span>
          </summary>
          <p className="approved-answer">{row.answer}</p>
          <p>{row.review_note}</p>
          <p>
            {row.sources
              .map((c) => `${c.document_name} · 第 ${c.page} 页`)
              .join("；")}
          </p>
        </details>
      ))}
      <Link href="/quality">进入质量与反馈管理 →</Link>
    </section>
  );
}
