import { useEffect, useRef, useState } from "react";

import { chatWithAgent } from "../api";
import type { DocumentLayout } from "../types";

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

interface Props {
  layout: DocumentLayout;
  history: ChatTurn[];
  onHistoryChange: (next: ChatTurn[]) => void;
  onLayoutChange: (next: DocumentLayout) => void;
}

export function ChatEditor({ layout, history, onHistoryChange, onLayoutChange }: Props) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [history, busy]);

  const send = async () => {
    const trimmed = message.trim();
    if (!trimmed || busy) return;
    const nextHistory: ChatTurn[] = [...history, { role: "user", content: trimmed }];
    onHistoryChange(nextHistory);
    setMessage("");
    setBusy(true);
    setError(null);
    try {
      const reply = await chatWithAgent(layout, nextHistory, trimmed);
      onHistoryChange([...nextHistory, { role: "assistant", content: reply.assistant_message }]);
      if (reply.updated_layout) onLayoutChange(reply.updated_layout);
    } catch (e) {
      setError(String(e));
      onHistoryChange(nextHistory);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-line bg-panel px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted">
        AI chat · high-precision edits
      </header>
      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-3 text-sm">
        {history.length === 0 && (
          <div className="rounded border border-dashed border-line p-3 text-xs text-muted">
            <p className="font-semibold text-ink">Talk to the AI to edit the document.</p>
            <p className="mt-1">Try things like:</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-4">
              <li>Make all headings bold and dark blue</li>
              <li>Add a "Skills" section with 4 bullet points</li>
              <li>Change the second paragraph to be more formal</li>
              <li>Remove block-3</li>
            </ul>
          </div>
        )}
        {history.map((turn, i) => (
          <div
            key={i}
            className={`flex ${turn.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 ${
                turn.role === "user"
                  ? "bg-accent text-white"
                  : "border border-line bg-panel text-ink"
              }`}
            >
              {turn.content}
            </div>
          </div>
        ))}
        {busy && (
          <div className="flex justify-start">
            <div className="rounded-lg border border-line bg-panel px-3 py-2 text-muted">
              <span className="inline-block h-3 w-3 animate-pulse rounded-full bg-muted" /> thinking...
            </div>
          </div>
        )}
        {error && (
          <div className="rounded border border-danger/40 bg-danger/10 p-2 text-xs text-danger">
            {error}
          </div>
        )}
      </div>
      <div className="border-t border-line bg-panel p-2">
        <div className="flex gap-2">
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="Tell the AI what to change (Enter to send, Shift+Enter for newline)..."
            rows={2}
            disabled={busy}
            className="field flex-1 resize-none"
          />
          <button onClick={send} disabled={busy || !message.trim()} className="btn btn-primary">
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
