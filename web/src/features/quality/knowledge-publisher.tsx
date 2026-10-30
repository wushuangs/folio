"use client";
import { useState } from "react";
import {
  BookOpenCheck,
  History,
  Upload,
  Undo2,
  FileText,
  ChevronDown,
  ExternalLink,
} from "lucide-react";
import { api } from "../knowledge/api";
import { Select } from "../workspace/select";
import type { Feedback, Publication } from "./types";

export function KnowledgePublisher({
  feedback,
  onPublished,
}: {
  feedback: Feedback;
  onPublished: (row: Publication) => void;
}) {
  const published = feedback.publication;
  const initial = published ? "published" : "snapshot";
  const [sourceSet, setSourceSet] = useState(initial);
  const [question, setQuestion] = useState(
    published?.question || feedback.snapshot.question,
  );
  const [answer, setAnswer] = useState(
    published?.answer || feedback.snapshot.answer,
  );
  const [sourceIds, setSourceIds] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [history, setHistory] = useState<
    { action: string; snapshot: Publication }[] | null
  >(null);
  const sourceGroup = feedback.publication_sources?.[sourceSet];
  const fallbackSources =
    sourceSet === "published"
      ? published?.sources
      : sourceSet === "verification"
        ? feedback.verification.citations
        : feedback.snapshot.citations;
  const sources =
    sourceGroup?.items ||
    (fallbackSources || []).filter((c) => c.kind !== "approved_qa");
  const selectedSources = sources.filter((c) => sourceIds.includes(c.chunk_id));
  const citedIds = [
    ...new Set(
      [...answer.matchAll(/\[(\d+)\]/g)].map((match) => Number(match[1])),
    ),
  ];
  const missingIds = citedIds.filter(
    (id) => !selectedSources.some((c) => c.id === id),
  );
  const blockers = [
    !question.trim() && "填写标准问题。",
    !answer.trim() && "填写经核对的标准答案。",
    answer.length > 6000 && "标准答案不能超过 6000 字。",
    !!sourceGroup?.warning && sourceGroup.warning,
    !sources.length &&
      "当前来源组没有可发布的原文，请切换来源组或重新进行原文验证。",
    !selectedSources.length && "至少勾选一条已核对的原文依据。",
    selectedSources.length > 6 && "每次最多选择 6 条来源。",
    selectedSources.some((c) => "is_current" in c && c.is_current === false) &&
      "所选来源已经失效，请重新验证后选择最新原文。",
    missingIds.length > 0 &&
      `答案引用 ${missingIds.map((id) => `[${id}]`).join("、")} 尚未勾选。请补选来源，或修正标准答案中的引用编号。`,
    !note.trim() && "填写本次审核说明。",
    !confirmed && "勾选人工核对确认。",
  ].filter((reason): reason is string => typeof reason === "string");
  function adopt(origin: "snapshot" | "verification" | "published") {
    const content =
      origin === "published"
        ? published
        : origin === "verification"
          ? feedback.verification
          : feedback.snapshot;
    if (!content) return;
    setQuestion(content.question || feedback.snapshot.question);
    setAnswer(content.answer || "");
    setSourceSet(origin);
    setSourceIds([]);
    setConfirmed(false);
    setError("");
  }
  async function publish() {
    setBusy(true);
    setError("");
    try {
      const row = await api<Publication>(`/feedback/${feedback.id}/publish`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          answer,
          source_set: sourceSet,
          source_ids: sourceIds,
          review_note: note,
          confirmed,
          expected_revision: published?.revision || 0,
        }),
      });
      onPublished(row);
    } catch (e) {
      setError(e instanceof Error ? e.message : "发布失败");
    } finally {
      setBusy(false);
    }
  }
  async function withdraw() {
    setBusy(true);
    setError("");
    try {
      const row = await api<Publication>(`/feedback/${feedback.id}/withdraw`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reason: note,
          expected_revision: published?.revision,
        }),
      });
      onPublished(row);
    } catch (e) {
      setError(e instanceof Error ? e.message : "撤回失败");
    } finally {
      setBusy(false);
    }
  }
  async function showHistory() {
    try {
      setHistory(await api(`/feedback/${feedback.id}/publication-history`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }
  return (
    <section className="knowledge-publisher" aria-label="发布正式知识">
      <div className="publication-heading">
        <BookOpenCheck size={19} />
        <h3>审核并发布正式知识</h3>
      </div>
      <p>
        标准答案经人工核对后进入检索。选择文档时仅使用对应来源，来源归档或版本变化后暂停使用。
      </p>
      {published && (
        <div
          className={`publication-status ${published.retrieval_active ? "active" : ""}`}
          role="status"
        >
          版本 {published.revision} ·{" "}
          {published.status === "withdrawn"
            ? "已撤回"
            : published.retrieval_active
              ? "已发布 · 正在参与检索"
              : "已发布 · 来源失效，暂停检索"}
          <small>{published.review_note}</small>
          {published.status === "published" && (
            <small>
              当前版本已经发布，无需重复操作。修改后重新核对并发布新版本。
            </small>
          )}
        </div>
      )}
      <div className="publication-adopt">
        <span>编辑起点</span>
        <button type="button" disabled={busy} onClick={() => adopt("snapshot")}>
          采用原回答及来源
        </button>
        <button
          type="button"
          disabled={
            busy ||
            feedback.verification.answer_mode !== "generated" ||
            !feedback.verification.answer
          }
          onClick={() => adopt("verification")}
        >
          采用验证答案及来源
        </button>
        {published && (
          <button
            type="button"
            disabled={busy}
            onClick={() => adopt("published")}
          >
            采用已发布版本
          </button>
        )}
      </div>
      {feedback.verification.answer_mode === "retrieval_only" && (
        <p className="publication-hint">
          最近一次为本地原文验证，只更新依据，不生成新答案。请人工编辑标准答案，或采用原回答后核对引用。
        </p>
      )}
      <label>
        标准问题
        <textarea
          disabled={busy}
          value={question}
          maxLength={2000}
          onChange={(e) => {
            setQuestion(e.target.value);
            setConfirmed(false);
          }}
        />
      </label>
      <label>
        标准答案
        <textarea
          className="standard-answer"
          disabled={busy}
          value={answer}
          maxLength={6000}
          onChange={(e) => {
            setAnswer(e.target.value);
            setConfirmed(false);
          }}
          placeholder="填写经核对的完整答案，保留适用条件、单位及正确的引用编号。"
        />
      </label>
      <div className="quality-filter">
        <span>来源范围</span>
        <Select
          label="发布来源范围"
          value={sourceSet}
          disabled={busy}
          options={[
            ...(published
              ? [{ value: "published", label: "已发布版本来源" }]
              : []),
            { value: "verification", label: "最新验证原文" },
            { value: "snapshot", label: "原回答来源" },
          ]}
          onChange={(value) => {
            setSourceSet(value);
            setError("");
            setSourceIds([]);
            setConfirmed(false);
          }}
        />
      </div>
      <fieldset className="publication-sources" disabled={busy}>
        <legend>勾选已核对的原文依据</legend>
        {sourceGroup?.warning && (
          <p role="status" className="publication-hint">
            {sourceGroup.warning}
          </p>
        )}
        <div className="source-selection-heading">
          <p>展开原文核对后，勾选作为标准答案的依据。</p>
          <span>
            已选 {sourceIds.length} / {sources.length}
          </span>
        </div>
        <div className="source-selection-list">
          {sources.length ? (
            sources.map((c) => (
              <article
                className={`source-selection-card ${sourceIds.includes(c.chunk_id) ? "is-selected" : ""}`}
                key={c.chunk_id}
              >
                <label className="source-choice">
                  <input
                    className="source-choice-checkbox"
                    type="checkbox"
                    aria-label={`选择来源 ${c.document_name} 第 ${c.page} 页`}
                    disabled={
                      busy || ("is_current" in c && c.is_current === false)
                    }
                    checked={sourceIds.includes(c.chunk_id)}
                    onChange={(e) => {
                      setSourceIds(
                        e.target.checked
                          ? [...sourceIds, c.chunk_id]
                          : sourceIds.filter((id) => id !== c.chunk_id),
                      );
                      setConfirmed(false);
                    }}
                  />
                  <span className="source-file-icon" aria-hidden="true">
                    <FileText size={18} />
                  </span>
                  <span className="source-choice-text">
                    <strong title={c.document_name}>{c.document_name}</strong>
                    <span className="source-choice-meta">
                      <span>来源 [{c.id}]</span>
                      <span>第 {c.page} 页</span>
                      <span>索引 v{c.revision ?? "未知"}</span>
                      {"source_issue" in c &&
                        typeof c.source_issue === "string" &&
                        c.source_issue && (
                          <span className="source-issue">
                            {String(c.source_issue)}
                          </span>
                        )}
                    </span>
                  </span>
                </label>
                <details className="source-evidence">
                  <summary>
                    <span>{c.heading || "查看原文依据"}</span>
                    <span className="source-evidence-action">
                      核对原文
                      <ChevronDown size={14} />
                    </span>
                  </summary>
                  <div className="source-evidence-body">
                    <p>{c.content}</p>
                    <a
                      href={`/api/documents/${c.document_id}/file#page=${c.page}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <ExternalLink size={13} />
                      打开 PDF 原页
                    </a>
                  </div>
                </details>
              </article>
            ))
          ) : (
            <div className="source-selection-empty">
              <FileText size={22} />
              <p>暂无可发布的原文来源</p>
              <small>请先完成本地检索或模型验证，再选择最新来源。</small>
            </div>
          )}
        </div>
      </fieldset>
      <label>
        审核说明 / 撤回原因
        <textarea
          disabled={busy}
          maxLength={2000}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="记录核对结论、适用范围或撤回原因。"
        />
      </label>
      <label className="publication-confirm">
        <input
          type="checkbox"
          disabled={busy}
          checked={confirmed}
          onChange={(e) => setConfirmed(e.target.checked)}
        />
        我已核对标准答案、适用条件与所选原文，确认发布为正式知识。
      </label>
      <div
        className={`publication-checklist ${blockers.length ? "" : "ready"}`}
        aria-live="polite"
      >
        <strong>
          {blockers.length ? "尚未满足的发布条件" : "发布条件已满足"}
        </strong>
        {blockers.length ? (
          <ul>
            {blockers.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        ) : (
          <p>已选择依据并完成人工确认，可以发布为正式知识。</p>
        )}
      </div>
      {error && (
        <p role="alert" className="quality-error">
          {error}
        </p>
      )}
      <div className="quality-toolbar">
        <button
          className="publication-primary"
          disabled={busy || blockers.length > 0}
          onClick={() => void publish()}
        >
          <Upload size={14} />
          {published ? "审核并发布新版本" : "审核并发布"}
        </button>
        {published?.status === "published" && (
          <button
            disabled={busy || !note.trim()}
            onClick={() => void withdraw()}
          >
            <Undo2 size={14} />
            撤回知识
          </button>
        )}
        <button disabled={busy} onClick={() => void showHistory()}>
          <History size={14} />
          发布历史
        </button>
      </div>
      {history && (
        <div className="publication-history">
          {history.length ? (
            history.map((event) => (
              <details key={`${event.snapshot.revision}-${event.action}`}>
                <summary>
                  版本 {event.snapshot.revision} ·{" "}
                  {event.action === "publish" ? "发布" : "撤回"} ·{" "}
                  {new Date(event.snapshot.updated_at).toLocaleString("zh-CN")}
                </summary>
                <p>{event.snapshot.review_note}</p>
                <pre>
                  {event.snapshot.question}
                  {"\n\n"}
                  {event.snapshot.answer}
                </pre>
                <p>
                  {event.snapshot.sources
                    .map((c) => `${c.document_name} 第${c.page}页`)
                    .join("；")}
                </p>
              </details>
            ))
          ) : (
            <p>暂无发布记录。</p>
          )}
        </div>
      )}
    </section>
  );
}
