"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { BookOpenCheck } from "lucide-react";
import { api } from "../knowledge/api";
import { Select } from "../workspace/select";
import type { Publication } from "./types";

type Row = Publication & { feedback_id: string };
export function PublishedKnowledgeList() {
  const [items, setItems] = useState<Row[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    try {
      const rows = await api<Row[]>("/knowledge/approved");
      setItems(rows);
      setSelected([]);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法加载正式知识");
    }
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => void load(), 0);
    const focus = () => void load();
    window.addEventListener("focus", focus);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("focus", focus);
    };
  }, [load]);
  const visible = items.filter(
    (row) =>
      [row.question, row.answer]
        .join(" ")
        .toLowerCase()
        .includes(search.toLowerCase()) &&
      (filter === "all" ||
        (filter === "active"
          ? row.retrieval_active
          : filter === "stale"
            ? row.status === "published" && !row.retrieval_active
            : row.status === "withdrawn")),
  );
  const selectable = visible.filter((row) => row.status === "published");
  async function withdraw() {
    if (!reason.trim() || !selected.length) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<{ updated: number }>(
        "/knowledge/approved/batch-withdraw",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            items: items
              .filter((row) => selected.includes(row.id))
              .map((row) => ({ id: row.id, revision: row.revision })),
            reason,
          }),
        },
      );
      setNotice(`已撤回 ${result.updated} 条知识，相关反馈恢复为处理中。`);
      setReason("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "批量撤回失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="approved-knowledge-list" aria-label="已审核问答知识">
      <h3>
        <BookOpenCheck size={17} />
        已审核问答知识
      </h3>
      <p>
        经人工审核的标准答案与原文来源。展示最近 100
        条，可筛选来源失效知识并批量撤回。
      </p>
      <div className="knowledge-maintenance-toolbar">
        <input
          aria-label="搜索审核知识"
          placeholder="搜索标准问题或答案"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setSelected([]);
          }}
        />
        <Select
          label="知识生效状态"
          value={filter}
          onChange={(value) => {
            setFilter(value);
            setSelected([]);
          }}
          options={[
            { value: "all", label: "全部知识" },
            { value: "active", label: "检索中" },
            { value: "stale", label: "来源失效" },
            { value: "withdrawn", label: "已撤回" },
          ]}
        />
        <button
          className="text-button"
          disabled={busy}
          onClick={() => void load()}
        >
          刷新
        </button>
      </div>
      {!!selectable.length && (
        <label className="knowledge-select-all">
          <input
            type="checkbox"
            disabled={busy}
            checked={selectable.every((row) => selected.includes(row.id))}
            onChange={(e) =>
              setSelected(
                e.target.checked ? selectable.map((row) => row.id) : [],
              )
            }
          />
          选择当前筛选下的已发布知识
        </label>
      )}
      {!!selected.length && (
        <div className="knowledge-batch-bar">
          <span>已选 {selected.length} 条</span>
          <input
            aria-label="批量撤回原因"
            placeholder="填写撤回原因（保留历史，可重新审核发布）"
            maxLength={2000}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <button
            disabled={busy || !reason.trim()}
            onClick={() => void withdraw()}
          >
            {busy ? "处理中…" : "批量撤回"}
          </button>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {!visible.length && !error && <p>暂无符合条件的知识。</p>}
      {visible.map((row) => (
        <div className="knowledge-maintenance-row" key={row.id}>
          <input
            type="checkbox"
            aria-label={`选择知识 ${row.question}`}
            disabled={busy || row.status !== "published"}
            checked={selected.includes(row.id)}
            onChange={(e) =>
              setSelected(
                e.target.checked
                  ? [...selected, row.id]
                  : selected.filter((id) => id !== row.id),
              )
            }
          />
          <details>
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
                .map((c) => `${c.document_name} · 位置 ${c.page}`)
                .join("；")}
            </p>
            <Link href={`/quality?feedback=${row.feedback_id}`}>
              核对并编辑此知识 →
            </Link>
          </details>
        </div>
      ))}
    </section>
  );
}
