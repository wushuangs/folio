"use client";

import { useCallback, useEffect, useState } from "react";
import { FolderSync, RotateCcw } from "lucide-react";
import { api } from "./api";

type SyncItem = {
  relative_path: string;
  size: number | null;
  sha256: string | null;
  status: "new" | "changed" | "unchanged" | "indexing" | "missing" | "too_large";
  document_id: string | null;
  can_rollback: boolean;
};
type Preview = {
  configured: boolean;
  root: string | null;
  items: SyncItem[];
};

const labels: Record<SyncItem["status"], string> = {
  new: "待导入",
  changed: "内容已变化",
  unchanged: "已同步",
  indexing: "正在建立索引",
  missing: "源文件已移走",
  too_large: "超过 50 MB",
};

export function LocalSync({ onChanged }: { onChanged: () => Promise<void> }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const scan = useCallback(async () => {
    setError("");
    try {
      setPreview(await api<Preview>("/sync/preview"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法扫描同步目录");
    }
  }, []);
  useEffect(() => {
    const initial = setTimeout(() => void scan(), 0);
    return () => clearTimeout(initial);
  }, [scan]);

  async function apply(item: SyncItem) {
    if (!item.sha256) return;
    setBusy(item.relative_path);
    setError("");
    try {
      await api("/sync/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          relative_path: item.relative_path,
          expected_sha256: item.sha256,
        }),
      });
      await onChanged();
      await scan();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "同步失败");
    } finally {
      setBusy("");
    }
  }

  async function rollback(item: SyncItem) {
    setBusy(item.relative_path);
    setError("");
    try {
      await api("/sync/rollback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ relative_path: item.relative_path }),
      });
      await onChanged();
      await scan();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "回退失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="local-sync" aria-labelledby="local-sync-title">
      <div className="local-sync-heading">
        <FolderSync size={18} aria-hidden="true" />
        <div>
          <h3 id="local-sync-title">本地目录同步</h3>
          <p>扫描指定文件夹，确认变更后再建立新索引。新索引完成前，旧文档仍可查询。</p>
        </div>
        <button className="text-button" type="button" onClick={() => void scan()}>
          刷新变更
        </button>
      </div>
      {preview && !preview.configured && (
        <p className="local-sync-note">
          在 .env.local 配置 LOCAL_SYNC_DIR 并重启后端，即可同步本地文件夹。
        </p>
      )}
      {preview?.configured && (
        <>
          <p className="local-sync-root">同步目录：{preview.root}</p>
          {preview.items.length ? (
            <div className="local-sync-list">
              {preview.items.map((item) => (
                <div className="local-sync-row" key={item.relative_path}>
                  <div>
                    <strong>{item.relative_path}</strong>
                    <span>{labels[item.status]}{item.size !== null ? ` · ${(item.size / 1024 / 1024).toFixed(1)} MB` : ""}</span>
                  </div>
                  {(item.status === "new" || item.status === "changed") && (
                    <button className="text-button" type="button"
                      disabled={Boolean(busy)} onClick={() => void apply(item)}>
                      {busy === item.relative_path ? "处理中…" : item.status === "new" ? "导入" : "更新索引"}
                    </button>
                  )}
                  {item.can_rollback && (
                    <button className="text-button" type="button"
                      disabled={Boolean(busy)} onClick={() => void rollback(item)}>
                      <RotateCcw size={12} /> 回退
                    </button>
                  )}
                </div>
              ))}
            </div>
          ) : <p className="local-sync-note">目录内尚无支持的文档。</p>}
        </>
      )}
      {error && <p className="diagnostics-error" role="alert">{error}</p>}
    </section>
  );
}
