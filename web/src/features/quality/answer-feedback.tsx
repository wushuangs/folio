"use client";
import { useEffect, useState } from "react";
import { Select } from "../workspace/select";
import { api } from "../knowledge/api";
import { categories, type Feedback } from "./types";
import "./quality.css";

export function AnswerFeedback({ messageId }: { messageId: string }) {
  const [rating, setRating] = useState("");
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState("incorrect");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let active = true;
    api<Feedback | null>(`/messages/${messageId}/feedback`)
      .then((row) => {
        if (active && row) {
          setRating(row.rating);
          setCategory(row.category || "incorrect");
          setComment(row.comment);
        }
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [messageId]);
  async function save(value: "helpful" | "problem") {
    setBusy(true);
    setNotice("");
    try {
      await api(`/messages/${messageId}/feedback`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rating: value,
          category: value === "problem" ? category : "",
          comment: value === "problem" ? comment : "",
        }),
      });
      setRating(value);
      setOpen(false);
      setNotice("反馈已保存");
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="answer-feedback">
      <div className="feedback-actions">
        <span>这条回答有帮助吗？</span>
        <button
          disabled={busy}
          aria-pressed={rating === "helpful"}
          onClick={() => void save("helpful")}
        >
          有帮助
        </button>
        <button
          disabled={busy}
          aria-expanded={open}
          aria-pressed={rating === "problem"}
          onClick={() => setOpen(!open)}
        >
          有问题
        </button>
        <span role="status">{notice}</span>
      </div>
      {open && (
        <form
          className="feedback-form"
          onSubmit={(e) => {
            e.preventDefault();
            void save("problem");
          }}
        >
          <div className="feedback-category">
            <span>问题类型</span>
            <Select
              label="问题类型"
              value={category}
              disabled={busy}
              options={Object.entries(categories).map(([value, label]) => ({
                value,
                label,
              }))}
              onChange={setCategory}
            />
          </div>
          <label>
            补充说明
            <textarea
              maxLength={2000}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="哪里不对？你期望得到什么答案？"
            />
          </label>
          <button disabled={busy} type="submit">
            {busy ? "保存中…" : "提交问题"}
          </button>
        </form>
      )}
    </div>
  );
}
