import { useEffect, useReducer, useState } from "react";

import { saveTemplate } from "./api";
import { ChatEditor, type ChatTurn } from "./components/ChatEditor";
import { Dashboard } from "./components/Dashboard";
import { HistoryPanel } from "./components/HistoryPanel";
import { LiveRender } from "./components/LiveRender";
import { TextEditor } from "./components/TextEditor";
import { editorReducer, initEditorState } from "./state/history";
import type { AuditReport, DocumentBlock, DocumentLayout, GenerateResponse } from "./types";
import { downloadDocx } from "./utils/wordCompiler";

interface Workspace {
  layout: DocumentLayout;
  audit: AuditReport;
  sourcePreview: string | null;
}

const EMPTY_LAYOUT: DocumentLayout = {
  title: "",
  page_width_px: 816,
  page_height_px: 1056,
  margin_px: { top: 72, right: 72, bottom: 72, left: 72 },
  blocks: [],
};

function isPdf(url: string) {
  return url.toLowerCase().endsWith(".pdf");
}
function isImage(url: string) {
  return /\.(png|jpe?g|webp|gif)$/i.test(url);
}

export default function App() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [state, dispatch] = useReducer(editorReducer, EMPTY_LAYOUT, initEditorState);
  const [chatHistory, setChatHistory] = useState<ChatTurn[]>([]);
  const [showSource, setShowSource] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!workspace) return;
    dispatch({ type: "replace", layout: workspace.layout });
    setChatHistory([]);
  }, [workspace]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const meta = e.ctrlKey || e.metaKey;
      if (meta && e.key.toLowerCase() === "z" && !e.shiftKey) {
        e.preventDefault();
        dispatch({ type: "undo" });
      } else if (meta && (e.key.toLowerCase() === "y" || (e.key.toLowerCase() === "z" && e.shiftKey))) {
        e.preventDefault();
        dispatch({ type: "redo" });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const handleReady = (result: GenerateResponse) => {
    let preview: string | null = null;
    if (result.layout._source_file) {
      const raw = result.layout._source_file.replace(/\\/g, "/");
      preview = raw.startsWith("/") ? raw : `/${raw.replace(/^\.\//, "")}`;
    }
    setWorkspace({
      layout: result.layout,
      audit: result.audit,
      sourcePreview: preview,
    });
  };

  const handleBlocksChange = (next: DocumentBlock[]) => {
    dispatch({ type: "commit", next });
  };

  const handleLayoutFromChat = (next: DocumentLayout) => {
    if (workspace) {
      setWorkspace({ ...workspace, layout: next });
    }
    dispatch({ type: "commit", next: next.blocks });
  };

  const handleExport = async () => {
    if (!workspace) return;
    await downloadDocx({ ...workspace.layout, blocks: state.present });
  };

  const handleSave = async () => {
    if (!workspace) return;
    const name = window.prompt("Template name:", workspace.layout.title || "Untitled");
    if (!name) return;
    setSaving(true);
    try {
      await saveTemplate({
        name,
        source_type: workspace.layout._source_file ? "imported" : "generated",
        layout: { ...workspace.layout, blocks: state.present },
        lock_tier: 3,
        original_file_path: workspace.layout._source_file ?? null,
      });
      window.alert("Saved.");
    } catch (e) {
      window.alert(`Save failed: ${e}`);
    } finally {
      setSaving(false);
    }
  };

  if (!workspace) {
    return <Dashboard onReady={handleReady} />;
  }

  const liveLayout: DocumentLayout = { ...workspace.layout, blocks: state.present };

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-line bg-panel px-4 py-2">
        <div className="flex items-center gap-4">
          <h1 className="text-lg font-bold tracking-tight">Vitegrid</h1>
          <span className="text-xs text-muted">{workspace.layout.title}</span>
        </div>
        <div className="flex items-center gap-3">
          <HistoryPanel
            state={state}
            onUndo={() => dispatch({ type: "undo" })}
            onRedo={() => dispatch({ type: "redo" })}
          />
          <button
            onClick={() => setShowSource((s) => !s)}
            className={`btn ${showSource ? "border-accent text-accent" : ""}`}
            title="Toggle source reference panel"
          >
            {showSource ? "Hide source" : "Show source"}
          </button>
          <button onClick={handleSave} disabled={saving} className="btn">
            {saving ? "Saving..." : "Save"}
          </button>
          <button onClick={handleExport} className="btn btn-primary">
            Export .docx
          </button>
          <button onClick={() => setWorkspace(null)} className="btn">
            New
          </button>
        </div>
      </header>

      {!workspace.audit.approved && (
        <div className="border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-xs text-amber-300">
          Audit flagged issues:{" "}
          {workspace.audit.layout_issues.join("; ") ||
            workspace.audit.patch_instructions ||
            "see patch instructions"}
        </div>
      )}

      <div className="flex flex-1 overflow-hidden">
        {showSource && (
          <aside className="flex w-72 shrink-0 flex-col border-r border-line bg-panel">
            <header className="border-b border-line px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted">
              Source reference
            </header>
            <div className="flex flex-1 items-center justify-center overflow-auto p-3">
              {!workspace.sourcePreview ? (
                <p className="text-xs text-muted">No source file (generated layout)</p>
              ) : isPdf(workspace.sourcePreview) ? (
                <iframe
                  src={workspace.sourcePreview}
                  title="Source PDF"
                  className="h-full w-full rounded border border-line bg-white"
                />
              ) : isImage(workspace.sourcePreview) ? (
                <img
                  src={workspace.sourcePreview}
                  alt="Source"
                  className="max-h-full max-w-full rounded border border-line"
                />
              ) : (
                <div className="rounded border border-dashed border-line p-4 text-center text-xs text-muted">
                  <p>Preview unavailable for .docx.</p>
                  <a
                    href={workspace.sourcePreview}
                    download
                    className="mt-2 inline-block text-accent underline"
                  >
                    Download original
                  </a>
                </div>
              )}
            </div>
          </aside>
        )}

        <main className="flex flex-1 overflow-hidden border-r border-line">
          <LiveRender layout={liveLayout} blocks={state.present} />
        </main>

        <aside className="flex w-96 shrink-0 flex-col">
          <div className="flex-1 overflow-hidden border-b border-line">
            <TextEditor blocks={state.present} onChange={handleBlocksChange} />
          </div>
          <div className="h-[45%] overflow-hidden">
            <ChatEditor
              layout={liveLayout}
              history={chatHistory}
              onHistoryChange={setChatHistory}
              onLayoutChange={handleLayoutFromChat}
            />
          </div>
        </aside>
      </div>
    </div>
  );
}
