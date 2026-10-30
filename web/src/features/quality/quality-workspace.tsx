"use client";
import { useCallback, useEffect, useState } from "react";
import { ClipboardCheck, MessageSquareText, RefreshCw } from "lucide-react";
import { KnowledgePublisher } from "./knowledge-publisher";
import { Select } from "../workspace/select";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../knowledge/api";
import { categories, statuses, type Feedback } from "./types";
import "./quality.css";

export function QualityWorkspace() {
  const [rows, setRows] = useState<Feedback[]>([]),
    [total, setTotal] = useState(0),
    [offset, setOffset] = useState(0);
  const [rating, setRating] = useState("problem"),
    [status, setStatus] = useState("all");
  const [selected, setSelected] = useState<Feedback | null>(null),
    [resolution, setResolution] = useState(""),
    [closing, setClosing] = useState(false);
  const [verifyQuestion, setVerifyQuestion] = useState("");
  const [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      try {
        const data = await api<{ items: Feedback[]; total: number }>(
          `/feedback?rating=${rating}&status=${status}&offset=${offset}&limit=20`,
          { signal },
        );
        setRows(data.items);
        setTotal(data.total);
        setError("");
      } catch (e) {
        if (!signal?.aborted)
          setError(e instanceof Error ? e.message : "加载失败");
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [rating, status, offset],
  );
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => void load(controller.signal), 0);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [load]);
  function choose(row: Feedback) {
    setSelected(row);
    setVerifyQuestion(row.verification.question || row.snapshot.question);
    setResolution("");
    setClosing(false);
    setError("");
  }
  async function closeFeedback() {
    if (
      !selected ||
      !resolution.trim() ||
      selected.status === "resolved" ||
      selected.publication?.status === "published"
    )
      return;
    setBusy(true);
    try {
      const row = await api<Feedback>(`/feedback/${selected.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status: "resolved",
          resolution: resolution.trim(),
        }),
      });
      choose(row);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "关闭反馈失败");
    } finally {
      setBusy(false);
    }
  }
  async function verify(mode: "local" | "live") {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<Feedback["verification"]>(
        `/feedback/${selected.id}/verify`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode, question: verifyQuestion }),
        },
      );
      const updated = await api<Feedback>(`/feedback/${selected.id}`);
      setSelected({ ...updated, verification: result });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "验证失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="quality-page">
      <div className="quality-toolbar quality-filters">
        <div className="quality-filter">
          <span>反馈类型</span>
          <Select
            label="反馈类型"
            disabled={busy}
            value={rating}
            options={[
              { value: "problem", label: "有问题" },
              { value: "helpful", label: "有帮助" },
              { value: "all", label: "全部反馈" },
            ]}
            onChange={(value) => {
              setRating(value);
              setOffset(0);
            }}
          />
        </div>
        <div className="quality-filter">
          <span>处理状态</span>
          <Select
            label="处理状态"
            disabled={busy}
            value={status}
            options={[
              { value: "all", label: "全部状态" },
              ...Object.entries(statuses).map(([value, label]) => ({
                value,
                label,
              })),
            ]}
            onChange={(value) => {
              setStatus(value);
              setOffset(0);
            }}
          />
        </div>
        <button
          className="quality-refresh"
          disabled={loading || busy}
          onClick={() => void load()}
        >
          <RefreshCw size={14} className={loading ? "quality-spinning" : ""} />
          刷新
        </button>
      </div>
      {error && (
        <p className="quality-error" role="alert">
          {error}
        </p>
      )}
      <div className="quality-columns">
        <section aria-label="反馈列表" className="quality-list">
          <div className="quality-section-heading">
            <MessageSquareText size={15} />
            <h3>反馈记录</h3>
            <span>{total}</span>
          </div>
          {loading ? (
            <p>正在加载…</p>
          ) : rows.length ? (
            rows.map((row) => (
              <button
                disabled={busy}
                key={row.id}
                className={`quality-record ${selected?.id === row.id ? "selected" : ""}`}
                aria-pressed={selected?.id === row.id}
                onClick={() => choose(row)}
              >
                <span className="quality-record-meta">
                  <span className="quality-record-category">
                    {row.rating === "helpful"
                      ? "有帮助"
                      : categories[row.category]}
                  </span>
                  <span
                    className={`quality-record-status status-${row.status}`}
                  >
                    {statuses[row.status]}
                  </span>
                </span>
                <strong title={row.snapshot.question}>
                  {row.snapshot.question || "未记录问题"}
                </strong>
                {row.comment && (
                  <span className="quality-record-comment">{row.comment}</span>
                )}
                <span className="quality-record-footer">
                  <time
                    dateTime={row.updated_at}
                    title={new Date(row.updated_at).toLocaleString("zh-CN")}
                  >
                    {new Date(row.updated_at).toLocaleString("zh-CN", {
                      month: "2-digit",
                      day: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                      hour12: false,
                    })}
                  </time>
                  {row.publication && (
                    <span className="quality-record-publication">
                      {row.publication.status === "withdrawn"
                        ? "已撤回"
                        : row.publication.retrieval_active
                          ? `已发布 · v${row.publication.revision}`
                          : "来源待复核"}
                    </span>
                  )}
                </span>
              </button>
            ))
          ) : (
            <div className="quality-empty">
              <h2>暂无反馈</h2>
              <p>
                在问答页面的回答下方点击“有帮助”或“有问题”，即可建立改进记录。
              </p>
            </div>
          )}
          <div className="quality-pagination">
            <button
              disabled={!offset || busy || loading}
              onClick={() => setOffset(Math.max(0, offset - 20))}
            >
              上一页
            </button>
            <button
              disabled={offset + 20 >= total || busy || loading}
              onClick={() => setOffset(offset + 20)}
            >
              下一页
            </button>
          </div>
        </section>
        <section className="quality-detail" aria-label="反馈详情">
          {selected ? (
            <>
              <h2>{selected.snapshot.question}</h2>
              <p>{selected.comment || "未填写补充说明"}</p>
              <h3>原回答快照</h3>
              <div className="quality-markdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {selected.snapshot.answer}
                </ReactMarkdown>
              </div>
              <p>原检索模式：{selected.snapshot.retrieval_mode || "未记录"}</p>
              {selected.snapshot.warning && <p>{selected.snapshot.warning}</p>}
              <details>
                <summary>
                  原引用（{selected.snapshot.citations.length} 条）
                </summary>
                {selected.snapshot.citations.map((c) => (
                  <blockquote key={c.chunk_id}>
                    <a
                      href={`/api/documents/${c.document_id}/file#page=${c.page}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {c.document_name} · 第 {c.page} 页
                    </a>
                    <p>{c.content}</p>
                  </blockquote>
                ))}
              </details>
              <h3>原文独立验证</h3>
              <label>
                验证问题
                <textarea
                  maxLength={2000}
                  disabled={busy}
                  value={verifyQuestion}
                  onChange={(e) => setVerifyQuestion(e.target.value)}
                  placeholder="可先修正错别字、补充型号或条件，再验证原始文档。"
                />
              </label>
              <p>
                验证仅使用当前原始文档，不召回已发布问答，避免用自己的答案证明自己。本地验证只更新依据；模型验证会发送问题和资料到已配置服务并产生费用。不会自动覆盖标准答案。
              </p>
              <div className="quality-toolbar">
                <button
                  disabled={busy || !verifyQuestion.trim()}
                  onClick={() => void verify("local")}
                >
                  本地检索验证
                </button>
                <button
                  disabled={busy || !verifyQuestion.trim()}
                  onClick={() => void verify("live")}
                >
                  模型回答验证
                </button>
                {busy && <span role="status">处理中…</span>}
              </div>
              {selected.verification.checked_at && (
                <div className="quality-verification">
                  <p>
                    {new Date(selected.verification.checked_at).toLocaleString(
                      "zh-CN",
                    )}{" "}
                    · {selected.verification.retrieval_mode}
                  </p>
                  {selected.verification.warning && (
                    <p className="quality-error">
                      {selected.verification.warning}
                    </p>
                  )}
                  {selected.verification.answer && (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {selected.verification.answer}
                    </ReactMarkdown>
                  )}
                  {selected.verification.citations?.map((c) => (
                    <details key={c.chunk_id}>
                      <summary>
                        {c.document_name} · 第 {c.page} 页
                      </summary>
                      <p>{c.content}</p>
                    </details>
                  ))}
                  <p>
                    检索结果仅供核对。确认正确后审核发布；无需发布时，可填写原因关闭反馈。
                  </p>
                </div>
              )}
              <KnowledgePublisher
                key={`${selected.id}-${selected.publication?.revision || 0}`}
                feedback={selected}
                onPublished={(publication) => {
                  setSelected({
                    ...selected,
                    publication,
                    status:
                      publication.status === "published"
                        ? "resolved"
                        : "in_progress",
                    resolution: publication.review_note,
                    updated_at: publication.updated_at,
                  });
                  setClosing(false);
                  setResolution("");
                  void load();
                }}
              />
              <section className="quality-outcome" aria-label="反馈处理结果">
                <div className="quality-outcome-summary">
                  <span
                    className={`quality-record-status status-${selected.status}`}
                  >
                    {statuses[selected.status]}
                  </span>
                  <span>
                    {selected.publication?.status === "published"
                      ? `已审核发布 v${selected.publication.revision}，反馈已自动解决`
                      : selected.status === "resolved"
                        ? "反馈已关闭"
                        : "审核并发布后，反馈将自动标记为已解决。"}
                  </span>
                  {selected.status === "resolved" && (
                    <time dateTime={selected.updated_at}>
                      {new Date(selected.updated_at).toLocaleString("zh-CN")}
                    </time>
                  )}
                </div>
                {selected.resolution && (
                  <p className="quality-outcome-note">{selected.resolution}</p>
                )}
                {selected.status !== "resolved" &&
                  selected.publication?.status !== "published" &&
                  (closing ? (
                    <div className="quality-close-form">
                      <label htmlFor="feedback-close-reason">关闭原因</label>
                      <textarea
                        id="feedback-close-reason"
                        disabled={busy}
                        maxLength={4000}
                        value={resolution}
                        onChange={(e) => setResolution(e.target.value)}
                        placeholder="例如：核对后原回答正确，或与已有反馈重复。"
                      />
                      <div className="quality-close-actions">
                        <button
                          disabled={busy || !resolution.trim()}
                          onClick={() => void closeFeedback()}
                        >
                          {busy ? "正在关闭…" : "确认关闭"}
                        </button>
                        <button
                          disabled={busy}
                          onClick={() => {
                            setClosing(false);
                            setResolution("");
                          }}
                        >
                          取消
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button disabled={busy} onClick={() => setClosing(true)}>
                      无需发布，关闭反馈
                    </button>
                  ))}
              </section>
            </>
          ) : (
            <div className="quality-empty quality-detail-empty">
              <span className="quality-empty-icon">
                <ClipboardCheck size={28} />
              </span>
              <h2>选择一条反馈</h2>
              <p>查看问题、原回答与证据，再记录修正和验证结果。</p>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
