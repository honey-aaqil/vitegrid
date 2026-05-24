import type {
  DocumentLayout,
  GenerateResponse,
  LockTier,
  TemplateSummary,
} from "./types";

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export async function generateFromPrompt(goal: string): Promise<GenerateResponse> {
  const res = await fetch("/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ goal }),
  });
  return jsonOrThrow<GenerateResponse>(res);
}

export async function importLayout(
  document: File,
  templateImage: File | null,
): Promise<GenerateResponse> {
  const form = new FormData();
  form.append("document", document);
  if (templateImage) form.append("template_image", templateImage);
  const res = await fetch("/api/import", { method: "POST", body: form });
  return jsonOrThrow<GenerateResponse>(res);
}

export async function chatWithAgent(
  layout: DocumentLayout,
  history: { role: "user" | "assistant"; content: string }[],
  message: string,
): Promise<{ assistant_message: string; updated_layout: DocumentLayout | null }> {
  const res = await fetch("/api/agent/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ layout, history, message }),
  });
  return jsonOrThrow(res);
}

export async function refineText(text: string, instruction: string): Promise<string> {
  const res = await fetch("/api/agent/refine", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, instruction }),
  });
  const json = await jsonOrThrow<{ text: string }>(res);
  return json.text;
}

export async function uploadImage(
  file: File,
  templateId?: number,
): Promise<{ id: number; local_path: string; width_px: number; height_px: number }> {
  const form = new FormData();
  form.append("file", file);
  if (templateId != null) form.append("template_id", String(templateId));
  const res = await fetch("/api/images", { method: "POST", body: form });
  return jsonOrThrow(res);
}

export async function listTemplates(): Promise<TemplateSummary[]> {
  const res = await fetch("/api/templates");
  return jsonOrThrow<TemplateSummary[]>(res);
}

export async function saveTemplate(args: {
  name: string;
  source_type: "imported" | "generated";
  layout: DocumentLayout;
  lock_tier: LockTier;
  original_file_path?: string | null;
}): Promise<{ id: number }> {
  const res = await fetch("/api/templates", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  return jsonOrThrow(res);
}

export async function updateTemplate(
  id: number,
  args: { name?: string; layout?: DocumentLayout; lock_tier?: LockTier },
): Promise<{ id: number }> {
  const res = await fetch(`/api/templates/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  return jsonOrThrow(res);
}

export async function getTemplate(
  id: number,
): Promise<{ id: number; name: string; layout: DocumentLayout; lock_tier: LockTier }> {
  const res = await fetch(`/api/templates/${id}`);
  return jsonOrThrow(res);
}
