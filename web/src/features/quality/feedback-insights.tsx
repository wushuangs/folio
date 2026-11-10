"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "../knowledge/api";
import type { Feedback } from "./types";

type Group = {
  question: string;
  ids: string[];
  count: number;
  open_count: number;
  categories: Record<string, number>;
};
type Insights = {
  groups: Group[];
  sample_size: number;
  limit: number;
  note: string;
};
export function FeedbackInsights({
  onSelect,
  onChanged,
}: {
  onSelect: (row: Feedback) => void;
  onChanged: () => void;
}) {
  const [data, setData] = useState<Insights | null>(null);
  const [error, setError] = useState("");
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      setData(await api<Insights>("/feedback-insights"));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "分析失败");
    }
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => void load(), 0);
    return () => clearTimeout(timer);
  }, [load]);
  async function open(id: string) {
    setBusy(true);
    try {
      onSelect(await api<Feedback>(`/feedback/${id}`));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setBusy(false);
    }
  }
  async function close(group: Group) {
    const reason = reasons[group.ids[0]] || "";
    if (!reason.trim()) return;
    setBusy(true);
    try {
      await api("/feedback-batch-close", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: group.ids, reason }),
      });
      setReasons((previous) => {
        const next = { ...previous };
        delete next[group.ids[0]];
        return next;
      });
      await load();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "批量关闭失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="feedback-insights">
      <summary>共性问题 · {data?.groups.length || 0} 组重复反馈</summary>
      <p>
        {data?.note} 分析最近 {data?.sample_size || 0} 条问题反馈（最多{" "}
        {data?.limit || 2000} 条）。
      </p>
      <button disabled={busy} onClick={() => void load()}>
        刷新分析
      </button>
      {error && <p role="alert">{error}</p>}
      {!data?.groups.length && <p>暂无重复问题，新增反馈后可刷新分析。</p>}
      {data?.groups.map((group) => (
        <div className="feedback-insight-group" key={group.ids[0]}>
          <strong>{group.question}</strong>
          <span>
            {group.count} 条 · {group.open_count} 条未解决
          </span>
          <div className="feedback-group-links">
            {group.ids.map((id, i) => (
              <button disabled={busy} key={id} onClick={() => void open(id)}>
                查看反馈 {i + 1}
              </button>
            ))}
          </div>
          {!!group.open_count && (
            <details>
              <summary>无需发布，整组关闭</summary>
              <p>仅在重复反馈都无需发布时使用；包含已发布知识的组会被拦截。</p>
              <input
                aria-label={`关闭原因 ${group.question}`}
                value={reasons[group.ids[0]] || ""}
                maxLength={4000}
                onChange={(e) =>
                  setReasons({ ...reasons, [group.ids[0]]: e.target.value })
                }
                placeholder="填写核对结论或重复原因"
              />
              <button
                disabled={busy || !(reasons[group.ids[0]] || "").trim()}
                onClick={() => void close(group)}
              >
                关闭这组反馈
              </button>
            </details>
          )}
        </div>
      ))}
    </details>
  );
}
