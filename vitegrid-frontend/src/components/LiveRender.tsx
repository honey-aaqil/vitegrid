import { useState } from "react";

import type { DocumentBlock, DocumentLayout } from "../types";

interface Props {
  layout: DocumentLayout;
  blocks: DocumentBlock[];
}

function styleToCss(block: DocumentBlock): React.CSSProperties {
  const s = block.style;
  return {
    color: s.color_hex,
    backgroundColor: s.background_hex ?? undefined,
    textAlign: s.align,
    fontFamily: s.font_family,
    fontSize: s.font_size_pt ? `${s.font_size_pt}pt` : undefined,
    fontWeight: s.font_weight,
  };
}

function RenderedBlock({ block }: { block: DocumentBlock }) {
  const css = styleToCss(block);
  switch (block.type) {
    case "heading":
      return (
        <h2 style={css} className="m-0">
          {block.text}
        </h2>
      );
    case "paragraph":
      return (
        <p style={css} className="m-0 whitespace-pre-wrap">
          {block.text}
        </p>
      );
    case "list":
      return (
        <ul style={css} className="m-0 list-disc pl-6">
          {(block.items ?? []).map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      );
    case "table":
      return (
        <table style={css} className="w-full border-collapse">
          <tbody>
            {(block.rows ?? []).map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td
                    key={c}
                    className={
                      block.style.border_visible === true
                        ? "border border-black/40 px-2 py-1 align-top break-words"
                        : "border border-transparent px-2 py-1 align-top break-words"
                    }
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
    case "image_placeholder":
      return (
        <div style={css}>
          {block.image_ref ? (
            <img src={block.image_ref} alt="" className="max-w-full" />
          ) : (
            <div className="flex h-32 items-center justify-center rounded border border-dashed border-black/30 text-xs text-black/40">
              image placeholder
            </div>
          )}
        </div>
      );
  }
}

export function LiveRender({ layout, blocks }: Props) {
  const [zoom, setZoom] = useState(0.8);

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-line bg-panel px-3 py-2 text-xs">
        <span className="font-semibold uppercase tracking-wide text-muted">Live render</span>
        <label className="flex items-center gap-2 text-muted">
          Zoom
          <input
            type="range"
            min={0.4}
            max={1.6}
            step={0.05}
            value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
          />
          <span className="w-10 text-right">{Math.round(zoom * 100)}%</span>
        </label>
      </header>
      <div className="flex-1 overflow-auto bg-bg p-6">
        <div
          className="mx-auto bg-white text-black shadow-2xl"
          style={{
            width: layout.page_width_px,
            minHeight: layout.page_height_px,
            transform: `scale(${zoom})`,
            transformOrigin: "top center",
            position: "relative",
            color: "#1a1a1a",
          }}
        >
          <div
            className="space-y-2"
            style={{
              paddingTop: layout.margin_px.top,
              paddingRight: layout.margin_px.right,
              paddingBottom: layout.margin_px.bottom,
              paddingLeft: layout.margin_px.left,
            }}
          >
            {blocks.map((block) => (
              <RenderedBlock key={block.id} block={block} />
            ))}
            {blocks.length === 0 && (
              <p className="py-12 text-center text-sm text-black/40">Empty document</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
