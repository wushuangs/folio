"use client";
import { useState } from "react";
import { api } from "./api";
import type { Document } from "./types";

export function ParseReport({ document }: { document: Document }) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState("1");
  const [rows, setRows] = useState<
    { id: string; page: number; heading: string; content: string }[]
  >([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const report = document.quality_report?.status
    ? document.quality_report
    : undefined;
  async function preview() {
    setBusy(true);
    setError("");
    setRows([]);
    setLoaded(false);
    try {
      setRows(
        await api(`/documents/${document.id}/chunks?page=${Number(page) || 1}`),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法加载解析内容");
    } finally {
      setLoaded(true);
      setBusy(false);
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
        <div className="parse-report-body">
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
                onChange={(e) => setPage(e.target.value)}
              />
            </label>
            <button
              className="text-button"
              disabled={busy}
              onClick={() => void preview()}
            >
              {busy ? "加载中…" : "查看提取文字"}
            </button>
          </div>
          {error && <p role="alert">{error}</p>}
          {loaded && !rows.length && !error && (
            <p>该位置没有索引片段，请检查是否为空白页、目录或未识别内容。</p>
          )}
          {rows.map((row) => (
            <details key={row.id}>
              <summary>
                {row.heading} · {row.page}
              </summary>
              <pre>{row.content}</pre>
            </details>
          ))}
          {rows.length >= 30 && <p>仅显示该位置前 30 个片段。</p>}
        </div>
      )}
    </div>
  );
}
