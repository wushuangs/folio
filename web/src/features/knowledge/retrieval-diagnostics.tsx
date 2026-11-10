"use client";

import { useState } from "react";
import { ArrowUpRight, Search } from "lucide-react";
import { api } from "./api";
import type { Document } from "./types";
import { Select } from "../workspace/select";

type Candidate = {
  id?: number;
  rank?: number;
  chunk_id: string;
  document_id: string;
  document_name: string;
  revision?: number;
  page: number;
  heading: string;
  content: string;
  kind?: string;
  score?: number;
  similarity?: number;
  included?: boolean;
  selected?: boolean;
  reason?: string;
};
type Diagnosis = {
  question: string;
  query: string;
  answer_path: "published_exact" | "retrieval";
  retrieval_mode: "approved" | "hybrid" | "keyword";
  warning: string;
  published?: { question: string; revision: number } | null;
  trace: {
    corpus_chunks: number;
    fts_candidates?: number;
    lexical_candidates?: number;
    lexical: Candidate[];
    vector: Candidate[];
    fusion: Candidate[];
    final: Candidate[];
    vector_state: string;
    block_reason?: string;
    timings_ms: Record<string, number>;
  };
};
type Stage = "final" | "lexical" | "vector" | "fusion";

const stages: { id: Stage; label: string }[] = [
  { id: "final", label: "最终依据" },
  { id: "lexical", label: "关键词候选" },
  { id: "vector", label: "向量候选" },
  { id: "fusion", label: "融合排序" },
];
const vectorStates: Record<string, string> = {
  disabled: "本次仅用关键词",
  not_configured: "尚未配置向量服务",
  index_unavailable: "当前文档没有匹配的向量索引",
  failed: "向量调用失败，已降级",
  used: "向量候选已参与检索",
  skipped: "已直接采用审核答案",
};

export function RetrievalDiagnostics({ documents }: { documents: Document[] }) {
  const ready = documents.filter((document) => document.status === "ready");
  const [question, setQuestion] = useState("");
  const [scope, setScope] = useState("all");
  const [retrieval, setRetrieval] = useState("hybrid");
  const [stage, setStage] = useState<Stage>("final");
  const [result, setResult] = useState<Diagnosis | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const selectedScope = ready.some((document) => document.id === scope)
    ? scope
    : "all";

  async function run() {
    if (!question.trim() || !ready.length || busy) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const response = await api<Diagnosis>("/retrieval/diagnose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: question.trim(),
          document_ids: selectedScope === "all" ? [] : [selectedScope],
          retrieval,
        }),
      });
      setResult(response);
      setStage("final");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "检索测试失败");
    } finally {
      setBusy(false);
    }
  }

  const candidates = result?.trace[stage] || [];
  return (
    <section className="retrieval-diagnostics" aria-labelledby="retrieval-diagnostics-title">
      <div className="diagnostics-heading">
        <div>
          <span className="eyebrow">RETRIEVAL TEST</span>
          <h3 id="retrieval-diagnostics-title">检索诊断</h3>
          <p>输入问题，检查片段如何进入回答依据。测试不会创建对话或调用回答模型。</p>
        </div>
        <Search size={21} aria-hidden="true" />
      </div>
      <form
        className="diagnostics-form"
        onSubmit={(event) => {
          event.preventDefault();
          void run();
        }}
      >
        <label className="diagnostics-question">
          <span>测试问题</span>
          <input
            type="text"
            maxLength={500}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="例如：手机钥匙如何使用？"
            disabled={busy}
          />
        </label>
        <div className="diagnostics-options">
          <div>
            <span>文档范围</span>
            <Select
              label="诊断文档范围"
              value={selectedScope}
              disabled={busy}
              onChange={setScope}
              options={[
                { value: "all", label: "全部可用文档" },
                ...ready.map((document) => ({
                  value: document.id,
                  label: document.name,
                })),
              ]}
            />
          </div>
          <div>
            <span>检索方式</span>
            <Select
              label="诊断检索方式"
              value={retrieval}
              disabled={busy}
              onChange={setRetrieval}
              options={[
                { value: "hybrid", label: "自动混合检索" },
                { value: "keyword", label: "仅关键词检索" },
              ]}
            />
          </div>
          <button className="button primary diagnostics-run" disabled={busy || !ready.length || !question.trim()}>
            {busy ? "诊断中…" : "开始诊断"}
          </button>
        </div>
        <p className="diagnostics-note">
          自动混合检索在向量索引可用时会调用已配置的向量服务，可能产生费用。
        </p>
      </form>
      {!ready.length && <p className="diagnostics-empty">上传并完成文档解析后，即可测试检索。</p>}
      {error && <p className="diagnostics-error" role="alert">{error}</p>}
      {result && (
        <div className="diagnostics-result" aria-live="polite">
          <div className="diagnostics-summary">
            <strong>
              {result.answer_path === "published_exact"
                ? "命中已发布标准答案"
                : result.trace.final.length
                  ? `找到 ${result.trace.final.length} 条最终依据`
                  : "没有找到可用依据"}
            </strong>
            <span>{result.retrieval_mode === "hybrid" ? "混合检索" : result.retrieval_mode === "approved" ? "审核答案" : "关键词检索"}</span>
            {result.trace.timings_ms.total !== undefined && (
              <span>{Math.round(result.trace.timings_ms.total)} ms</span>
            )}
          </div>
          <p className="diagnostics-query">实际检索问题：{result.query}</p>
          {result.answer_path === "published_exact" ? (
            <p className="diagnostics-hint">
              与已发布问题完全匹配；实际问答会直接返回审核版本，因此不再执行普通检索。
            </p>
          ) : (
            <p className="diagnostics-hint">
              共 {result.trace.corpus_chunks} 个有效片段 · 全文初筛 {result.trace.fts_candidates ?? 0} 条 · {vectorStates[result.trace.vector_state] || result.trace.vector_state}
            </p>
          )}
          {result.trace.block_reason && <p className="diagnostics-error">{result.trace.block_reason}</p>}
          {result.warning && <p className="diagnostics-error" role="status">{result.warning}</p>}
          <div className="diagnostics-tabs" role="tablist" aria-label="检索阶段">
            {stages.map((item) => (
              <button
                type="button"
                role="tab"
                aria-selected={stage === item.id}
                className={stage === item.id ? "active" : ""}
                key={item.id}
                onClick={() => setStage(item.id)}
              >
                {item.label}
                <span>{result.trace[item.id].length}</span>
              </button>
            ))}
          </div>
          <div className="diagnostics-list" role="tabpanel">
            {candidates.length ? candidates.map((candidate) => (
              <article className="diagnostics-candidate" key={`${stage}-${candidate.chunk_id}-${candidate.rank ?? candidate.id ?? 0}`}>
                <div className="diagnostics-candidate-top">
                  <strong>{candidate.rank ?? candidate.id}. {candidate.heading || "未命名片段"}</strong>
                  <span className={candidate.included === false || candidate.selected === false ? "diagnostics-rejected" : "diagnostics-accepted"}>
                    {candidate.reason || (candidate.kind === "approved_qa" ? "已审核" : "最终依据")}
                  </span>
                </div>
                <p className="diagnostics-candidate-meta">
                  {candidate.document_name} · 第 {candidate.page} 页 / 节
                  {candidate.revision !== undefined && ` · 索引 v${candidate.revision}`}
                  {candidate.score !== undefined && ` · ${stage === "lexical" ? "词法分" : "融合分"} ${candidate.score}`}
                  {candidate.similarity !== undefined && ` · 相似度 ${candidate.similarity}`}
                </p>
                <p className="diagnostics-candidate-text">{candidate.content}</p>
                {documents.find((document) => document.id === candidate.document_id)?.file_type === "pdf" && (
                  <a href={`/api/documents/${candidate.document_id}/file#page=${candidate.page}`} target="_blank" rel="noreferrer">
                    查看 PDF 原文 <ArrowUpRight size={13} />
                  </a>
                )}
              </article>
            )) : (
              <p className="diagnostics-empty">
                {stage === "vector"
                  ? vectorStates[result.trace.vector_state] || "本次没有向量候选。"
                  : stage === "final"
                    ? "本次没有可供回答的原文依据。"
                    : "这个阶段没有候选片段。"}
              </p>
            )}
          </div>
          <p className="diagnostics-note">
            词法分、向量相似度与融合分属于不同计算阶段，不能直接比较；最终答案仍需核对原文。
          </p>
        </div>
      )}
    </section>
  );
}
