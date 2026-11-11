"use client";
import { useState } from "react";
import { api } from "./api";
import type { Document } from "./types";

type PreviewChunk = {
  id: string;
  page: number;
  heading: string;
  content: string;
  enabled: boolean;
  revision: number;
};

export function ParseReport({
  document,
  onChanged,
}: {
  document: Document;
  onChanged?: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState("1");
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<PreviewChunk[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [keepVectors, setKeepVectors] = useState(true);
  const [reason, setReason] = useState("");
  const [history, setHistory] = useState<
    { from_revision: number; to_revision: number; page: number; reason: string }[]
  >([]);
  const [showHistory, setShowHistory] = useState(false);
  const report = document.quality_report?.status
    ? document.quality_report
    : undefined;
  async function preview(nextOffset = 0) {
    setBusy(true);
    setError("");
    setRows([]);
    setLoaded(false);
    try {
      setRows(
        await api(`/documents/${document.id}/chunks?page=${Number(page) || 1}&offset=${nextOffset}`),
      );
      setOffset(nextOffset);
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法加载解析内容");
    } finally {
      setLoaded(true);
      setBusy(false);
    }
  }
  async function save(row: PreviewChunk) {
    if (!reason.trim()) {
      setError("请填写修正原因。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api(`/documents/${document.id}/chunks/${row.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_revision: row.revision,
          content: draft,
          enabled,
          reason: reason.trim(),
          rebuild_vectors: keepVectors,
        }),
      });
      setEditing(null);
      setReason("");
      await onChanged?.();
      await preview(offset);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "片段修正失败");
    } finally {
      setBusy(false);
    }
  }
  async function loadHistory() {
    setShowHistory(!showHistory);
    if (showHistory) return;
    try {
      setHistory(await api(`/documents/${document.id}/chunk-edits`));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法加载修正记录");
    }
  }
  return (
    <div className="parse-report">
      <button
        className="text-button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        解析检查 ·{" "}
        {report?.status === "passed"
          ? "规则通过"
          : report?.status === "warning"
            ? "需核对"
            : "未检查"}
      </button>
      {open && (
        <div className={`parse-report-body ${editing ? "is-editing" : ""}`}>
          {report ? (
            <>
              <p>
                {report.characters} 字符 · {report.chunks} 片段 · OCR{" "}
                {report.ocr_pages.length} 页
              </p>
              {report.warnings.map((warning) => (
                <p key={warning}>{warning}</p>
              ))}
              {!!report.empty_pages.length && (
                <p>
                  空白位置：{report.empty_pages.slice(0, 30).join("、")}
                  {report.empty_pages.length > 30 ? "…" : ""}
                </p>
              )}
              <p>{report.note}</p>
            </>
          ) : (
            <p>旧索引尚无解析报告，执行文字或向量重建后生成。</p>
          )}
          <div className="parse-preview-controls">
            <label>
              页 / 节{" "}
              <input
                disabled={busy}
                type="number"
                min="1"
                max={document.pages || 1}
                value={page}
                onChange={(e) => {
                  setPage(e.target.value);
                  setOffset(0);
                }}
              />
            </label>
            <button
              className="text-button"
              disabled={busy}
              onClick={() => void preview(0)}
            >
              {busy ? "加载中…" : "查看提取文字"}
            </button>
          </div>
          {error && <p role="alert">{error}</p>}
          {loaded && !rows.length && !error && (
            <p>该位置没有索引片段，请检查是否为空白页、目录或未识别内容。</p>
          )}
          {rows.map((row) => (
            <div className="chunk-preview" key={row.id}>
              <div className="chunk-preview-heading">
                <strong>{row.heading} · {row.page}</strong>
                <span>{row.enabled ? "参与检索" : "已停用"}</span>
                <button
                  type="button"
                  className="text-button"
                  disabled={busy || document.status !== "ready" ||
                    ["queued", "processing"].includes(document.indexing_status)}
                  onClick={() => {
                    setEditing(editing === row.id ? null : row.id);
                    setDraft(row.content);
                    setEnabled(row.enabled);
                    setKeepVectors(document.vector_ready);
                    setReason("");
                    setError("");
                  }}
                >
                  {editing === row.id ? "取消修正" : "修正 / 停用"}
                </button>
              </div>
              {editing === row.id ? (
                <div className="chunk-editor">
                  <div className="chunk-editor-fields">
                    <label>
                      修正后的检索文字
                      <textarea value={draft} maxLength={4000}
                        onChange={(event) => setDraft(event.target.value)} disabled={busy} />
                    </label>
                    <label className="chunk-editor-check">
                      <input type="checkbox" checked={enabled}
                        onChange={(event) => setEnabled(event.target.checked)} disabled={busy} />
                      启用此片段
                    </label>
                    {document.vector_ready && (
                      <label className="chunk-editor-check">
                        <input type="checkbox" checked={keepVectors}
                          onChange={(event) => setKeepVectors(event.target.checked)} disabled={busy} />
                        保留向量索引（修改文字时调用向量服务）
                      </label>
                    )}
                    <label>
                      修正原因
                      <input value={reason} maxLength={500} disabled={busy}
                        onChange={(event) => setReason(event.target.value)}
                        placeholder="例如：OCR 误识别了参数单位" />
                    </label>
                    <p>保存会生成新索引版本；原文件与旧版本保留。来源版本变化后，相关已发布答案需要重新核对。</p>
                    <button type="button" className="button primary"
                      disabled={busy || !reason.trim() || (enabled && !draft.trim())}
                      onClick={() => void save(row)}>
                      {busy ? "保存中…" : "保存为新版本"}
                    </button>
                  </div>
                  {document.file_type === "pdf" ? (
                    <iframe title={`${document.name} 第 ${row.page} 页原文`}
                      src={`/api/documents/${document.id}/file#page=${row.page}`} />
                  ) : (
                    <a href={`/api/documents/${document.id}/file`} target="_blank" rel="noreferrer">
                      下载原文件核对
                    </a>
                  )}
                </div>
              ) : (
                <pre>{row.content}</pre>
              )}
            </div>
          ))}
          {loaded && (offset > 0 || rows.length >= 30) && (
            <div className="parse-preview-pagination">
              <button type="button" className="text-button" disabled={busy || offset === 0}
                onClick={() => void preview(Math.max(0, offset - 30))}>
                上一组
              </button>
              <span>第 {offset + 1}–{offset + rows.length} 个片段</span>
              <button type="button" className="text-button" disabled={busy || rows.length < 30}
                onClick={() => void preview(offset + 30)}>
                下一组
              </button>
            </div>
          )}
          <button type="button" className="text-button"
            onClick={() => void loadHistory()}>
            {showHistory ? "收起修正记录" : "查看修正记录"}
          </button>
          {showHistory && (
            <div className="chunk-edit-history">
              {history.length ? history.map((entry) => (
                <p key={`${entry.from_revision}-${entry.to_revision}`}>
                  v{entry.from_revision} → v{entry.to_revision} · 第 {entry.page} 页 / 节 · {entry.reason}
                </p>
              )) : <p>暂无人工修正记录。</p>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
