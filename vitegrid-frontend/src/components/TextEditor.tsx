import type { DocumentBlock } from "../types";

interface Props {
  blocks: DocumentBlock[];
  onChange: (next: DocumentBlock[]) => void;
}

const TYPE_LABEL: Record<DocumentBlock["type"], string> = {
  heading: "Heading",
  paragraph: "Paragraph",
  list: "List",
  table: "Table",
  image_placeholder: "Image",
};

export function TextEditor({ blocks, onChange }: Props) {
  const replace = (id: string, next: DocumentBlock) => {
    onChange(blocks.map((b) => (b.id === id ? next : b)));
  };

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-line bg-panel px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted">
        Text editor · content only
      </header>
      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        {blocks.map((block) => (
          <div key={block.id} className="rounded border border-line bg-panel p-3">
            <div className="mb-2 flex items-center gap-2 text-[10px] uppercase tracking-wide text-muted">
              <span>{TYPE_LABEL[block.type]}</span>
              <span className="font-mono">{block.id}</span>
            </div>
            {block.type === "heading" || block.type === "paragraph" ? (
              <textarea
                value={block.text ?? ""}
                onChange={(e) => replace(block.id, { ...block, text: e.target.value })}
                rows={block.type === "heading" ? 1 : 3}
                className="field resize-y"
                placeholder={`Edit ${block.type}...`}
              />
            ) : null}
            {block.type === "list" && (
              <div className="space-y-1">
                {(block.items ?? []).map((item, i) => (
                  <input
                    key={i}
                    value={item}
                    onChange={(e) => {
                      const items = [...(block.items ?? [])];
                      items[i] = e.target.value;
                      replace(block.id, { ...block, items });
                    }}
                    className="field"
                    placeholder={`Item ${i + 1}`}
                  />
                ))}
              </div>
            )}
            {block.type === "table" && (
              <div className="space-y-1">
                {(block.rows ?? []).map((row, r) => (
                  <div key={r} className="flex gap-1">
                    {row.map((cell, c) => (
                      <input
                        key={c}
                        value={cell}
                        onChange={(e) => {
                          const rows = (block.rows ?? []).map((r2) => [...r2]);
                          rows[r][c] = e.target.value;
                          replace(block.id, { ...block, rows });
                        }}
                        className="field flex-1"
                        placeholder={`r${r}c${c}`}
                      />
                    ))}
                  </div>
                ))}
              </div>
            )}
            {block.type === "image_placeholder" && (
              <p className="text-xs text-muted">
                Image block · use the AI chat to attach an image or change dimensions.
              </p>
            )}
          </div>
        ))}
        {blocks.length === 0 && (
          <p className="py-12 text-center text-sm text-muted">No blocks yet</p>
        )}
      </div>
    </div>
  );
}
