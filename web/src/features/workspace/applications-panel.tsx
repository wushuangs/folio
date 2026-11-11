"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Copy, KeyRound } from "lucide-react";
import { api } from "../knowledge/api";
import type { Document } from "../knowledge/types";
import { Select } from "./select";

type App = {
  id: string;
  name: string;
  document_ids: string[];
  model_id: string;
  answer_mode: string;
  retrieval: string;
  key_prefix: string;
  enabled: boolean;
};
type CreatedApp = App & { api_key: string };

export function ApplicationsPanel({
  documents,
  models,
}: {
  documents: Document[];
  models: { id: string; label: string }[];
}) {
  const ready = documents.filter((document) => document.status === "ready");
  const [apps, setApps] = useState<App[]>([]);
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [modelId, setModelId] = useState("default");
  const [answerMode, setAnswerMode] = useState("auto");
  const [key, setKey] = useState<{ app_id: string; value: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const load = useCallback(async () => {
    try {
      setApps(await api<App[]>("/apps"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法加载应用");
    }
  }, []);
  useEffect(() => {
    const initial = setTimeout(() => void load(), 0);
    return () => clearTimeout(initial);
  }, [load]);

  async function create() {
    if (!name.trim() || !selected.length) return;
    setBusy("create");
    setError("");
    setKey(null);
    setCopied(false);
    try {
      const created = await api<CreatedApp>("/apps", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          document_ids: selected,
          model_id: modelId,
          answer_mode: answerMode,
          retrieval: "auto",
        }),
      });
      setKey({ app_id: created.id, value: created.api_key });
      setName("");
      setSelected([]);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建应用失败");
    } finally {
      setBusy("");
    }
  }
  async function update(application: App, action: "toggle" | "rotate") {
    setBusy(application.id);
    setError("");
    setKey(null);
    setCopied(false);
    try {
      if (action === "rotate") {
        const changed = await api<CreatedApp>(`/apps/${application.id}/rotate-key`, {
          method: "POST",
        });
        setKey({ app_id: application.id, value: changed.api_key });
      } else {
        await api(`/apps/${application.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: !application.enabled }),
        });
      }
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "更新应用失败");
    } finally {
      setBusy("");
    }
  }
  async function copyKey() {
    if (!key) return;
    try {
      await navigator.clipboard.writeText(key.value);
      setCopied(true);
    } catch {
      setError("无法复制密钥，请手动选择复制。");
    }
  }

  return (
    <section className="applications-panel" aria-labelledby="applications-title">
      <div className="applications-heading">
        <KeyRound size={17} />
        <div>
          <h3 id="applications-title">业务应用接入</h3>
          <p>为每个应用固定可访问的文档范围，并生成独立 API Key。</p>
        </div>
      </div>
      <div className="applications-form">
        <label>
          应用名称
          <input value={name} maxLength={100} onChange={(event) => setName(event.target.value)}
            placeholder="例如：售后知识助手" />
        </label>
        <div className="applications-document-picker">
          <strong>可访问文档</strong>
          {ready.length ? ready.map((document) => (
            <label key={document.id}>
              <input type="checkbox" checked={selected.includes(document.id)}
                onChange={(event) => setSelected((current) =>
                  event.target.checked
                    ? [...current, document.id]
                    : current.filter((id) => id !== document.id))} />
              {document.name}
            </label>
          )) : <p>先上传并完成至少一份文档的索引。</p>}
        </div>
        <div className="applications-options">
          <Select label="应用回答模型" value={modelId} onChange={setModelId}
            options={models.map((model) => ({
              value: model.id, label: model.label,
            }))} />
          <Select label="应用回答方式" value={answerMode} onChange={setAnswerMode}
            options={[
              { value: "auto", label: "生成式问答" },
              { value: "extractive", label: "仅查原文" },
            ]} />
          <button className="button primary" type="button"
            disabled={Boolean(busy) || !name.trim() || !selected.length}
            onClick={() => void create()}>
            创建应用
          </button>
        </div>
      </div>
      {key && (
        <div className="applications-key">
          <strong>新 API Key · 仅显示这一次</strong>
          <code>{key.value}</code>
          <button type="button" className="text-button" onClick={() => void copyKey()}>
            {copied ? <Check size={13} /> : <Copy size={13} />}
            {copied ? "已复制" : "复制"}
          </button>
          <p>发送到 /api/integrations/{key.app_id}/chat，请求头使用 Authorization: Bearer API_KEY。</p>
        </div>
      )}
      {error && <p className="diagnostics-error" role="alert">{error}</p>}
      <div className="applications-list">
        {apps.map((application) => (
          <div className="applications-row" key={application.id}>
            <div>
              <strong>{application.name} · {application.enabled ? "已启用" : "已停用"}</strong>
              <span>{application.document_ids.length} 份文档 · {application.key_prefix}…</span>
              <code>/api/integrations/{application.id}/chat</code>
            </div>
            <button type="button" className="text-button"
              disabled={Boolean(busy)} onClick={() => void update(application, "toggle")}>
              {application.enabled ? "停用" : "启用"}
            </button>
            <button type="button" className="text-button"
              disabled={Boolean(busy)} onClick={() => void update(application, "rotate")}>
              轮换密钥
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}
