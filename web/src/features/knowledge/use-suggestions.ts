"use client";
import { useEffect, useState } from "react";
import { api } from "./api";
import type { Document } from "./types";

type Suggestion = {
  text: string;
  kind: "overview" | "guide" | "troubleshoot" | "parameter";
  document_id: string;
  document_name: string;
  topic: string;
};
export function useSuggestions(documents: Document[]) {
  const key = JSON.stringify(
    documents.map((d) => [d.id, d.active_revision, d.name]).sort(),
  );
  const [result, setResult] = useState<{
    key: string;
    items: Suggestion[];
    error: string;
  } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const ids = (JSON.parse(key) as [string, number, string][]).map(
      (row) => row[0],
    );
    if (!ids.length) return;
    void api<{ items: Suggestion[] }>("/suggestions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document_ids: ids }),
      signal: controller.signal,
    })
      .then((data) => {
        if (!controller.signal.aborted)
          setResult({ key, items: data.items, error: "" });
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setResult({
            key,
            items: [],
            error: "暂时无法加载推荐问题，可以直接输入问题。",
          });
      });
    return () => controller.abort();
  }, [key]);
  return {
    items: result?.key === key ? result.items : [],
    loading: documents.length > 0 && result?.key !== key,
    error: result?.key === key ? result.error : "",
  };
}
