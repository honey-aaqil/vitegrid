import { useState } from "react";

import { generateFromPrompt, importLayout } from "../api";
import type { GenerateResponse } from "../types";

interface Props {
  onReady: (result: GenerateResponse) => void;
}

export function Dashboard({ onReady }: Props) {
  const [goal, setGoal] = useState("");
  const [doc, setDoc] = useState<File | null>(null);
  const [tplImage, setTplImage] = useState<File | null>(null);
  const [busy, setBusy] = useState<"generate" | "import" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleGenerate = async () => {
    if (!goal.trim()) return;
    setBusy("generate");
    setError(null);
    try {
      const result = await generateFromPrompt(goal);
      onReady(result);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const handleImport = async () => {
    if (!doc) return;
    setBusy("import");
    setError(null);
    try {
      const result = await importLayout(doc, tplImage);
      onReady(result);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="relative mx-auto flex max-w-4xl flex-col gap-8 px-6 py-12">
      <header>
        <h1 className="text-4xl font-bold tracking-tight">Vitegrid</h1>
        <p className="mt-2 text-muted">
          Visual document analysis with Gemma 4 vision. Transform PDFs and DOCX into editable
          block layouts, or draft fresh ones with AI.
        </p>
      </header>

      {error && (
        <div className="flex items-start justify-between rounded border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
          <span className="whitespace-pre-wrap">{error}</span>
          <button onClick={() => setError(null)} className="ml-3 text-danger/60 hover:text-danger">
            ×
          </button>
        </div>
      )}

      <section className="rounded-lg border border-line bg-panel p-6">
        <h2 className="text-lg font-semibold">Start new</h2>
        <p className="mt-1 text-sm text-muted">Describe what you need. Agent 3 will draft it.</p>
        <textarea
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="e.g. A one-page professional cover letter for a software internship at a fintech startup."
          rows={3}
          disabled={busy !== null}
          className="field mt-3"
        />
        <button
          onClick={handleGenerate}
          disabled={!goal.trim() || busy !== null}
          className="btn btn-primary mt-3"
        >
          {busy === "generate" && <Spinner />}
          {busy === "generate" ? "Drafting layout..." : "Generate layout"}
        </button>
      </section>

      <section className="rounded-lg border border-line bg-panel p-6">
        <h2 className="text-lg font-semibold">Import layout</h2>
        <p className="mt-1 text-sm text-muted">
          PDFs are rendered as page images and analyzed visually. DOCX uses deterministic
          parsing. Image uploads (PNG, JPG, WebP) run through the three-step vision
          pipeline: spatial anchoring &rarr; optical parsing &rarr; per-element style mapping.
        </p>
        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
          <label className="flex cursor-pointer flex-col rounded border border-dashed border-line p-4 text-sm hover:border-accent">
            <span className="font-medium">Document</span>
            <span className="text-muted">{doc?.name ?? "Choose .pdf, .docx, .png, .jpg, .webp..."}</span>
            <input
              type="file"
              accept=".pdf,.docx,.png,.jpg,.jpeg,.webp"
              className="hidden"
              disabled={busy !== null}
              onChange={(e) => setDoc(e.target.files?.[0] ?? null)}
            />
          </label>
          <label className="flex cursor-pointer flex-col rounded border border-dashed border-line p-4 text-sm hover:border-accent">
            <span className="font-medium">Style reference (optional)</span>
            <span className="text-muted">{tplImage?.name ?? "Choose .png or .jpg..."}</span>
            <input
              type="file"
              accept=".png,.jpg,.jpeg,.webp"
              className="hidden"
              disabled={busy !== null}
              onChange={(e) => setTplImage(e.target.files?.[0] ?? null)}
            />
          </label>
        </div>
        <button
          onClick={handleImport}
          disabled={!doc || busy !== null}
          className="btn btn-primary mt-3"
        >
          {busy === "import" && <Spinner />}
          {busy === "import" ? "Vision analysis in progress..." : "Import & analyze"}
        </button>
      </section>

      {busy && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-bg/70 backdrop-blur-sm">
          <div className="flex items-center gap-3 rounded-lg border border-line bg-panel px-5 py-4 text-sm">
            <Spinner />
            <span>
              {busy === "generate"
                ? "Agents are drafting your layout..."
                : "Sending pages to Gemma 4 vision for analysis..."}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

function Spinner() {
  return (
    <span
      className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
      role="status"
      aria-label="Loading"
    />
  );
}
