import { useState } from "react";

import type { DocumentBlock, DocumentLayout } from "../types";

interface Props {
  layout: DocumentLayout;
  blocks: DocumentBlock[];
}

const DXA_TO_PX = 96 / 1440;

function styleToCss(block: DocumentBlock): React.CSSProperties {
  const s = block.style;
  const textDecorationLine = s.strikethrough ? "line-through" : s.underline !== "none" ? "underline" : undefined;
  return {
    color: s.color_hex,
    backgroundColor: s.background_hex ?? undefined,
    textAlign: s.align,
    fontFamily: s.font_family,
    fontSize: s.font_size_pt ? `${s.font_size_pt}pt` : undefined,
    fontWeight: s.font_weight,
    fontStyle: s.italic ? "italic" : "normal",
    textDecoration: textDecorationLine,
    textDecorationColor: s.underline_color_rgba ? s.underline_color_rgba : undefined,
    lineHeight: block.spacing.line_height_px ? `${block.spacing.line_height_px}px` : undefined,
    letterSpacing: s.letter_spacing_px ? `${s.letter_spacing_px}px` : undefined,
    wordSpacing: s.word_spacing_px ? `${s.word_spacing_px}px` : undefined,
    whiteSpace: "pre-wrap",
  };
}

function RenderedBlock({ block, index, totalBlocks }: { block: DocumentBlock; index: number; totalBlocks: number }) {
  const css = styleToCss(block);
  const margin_top = index === 0 ? 0 : block.spacing.before_px ?? 0;
  const margin_bottom = block.spacing.after_px ?? 0;

  switch (block.type) {
    case "heading":
      return (
        <h2 style={{ ...css, marginTop: margin_top, marginBottom: margin_bottom }}>
          {block.text}
        </h2>
      );
    case "paragraph":
      return (
        <p style={{ ...css, marginTop: margin_top, marginBottom: margin_bottom }}>
          {block.text}
        </p>
      );
    case "list": {
      const listPaddingLeft = block.style.list_level_indent_px ?? 0;
      const listHangingIndent = block.style.list_hanging_indent_px ?? 0;
      const effectivePaddingLeft = Math.max(listPaddingLeft, 8);
      const effectiveHangingIndent = Math.max(listHangingIndent, 0);

      return (
        <ul
          style={{
            ...css,
            marginTop: margin_top,
            marginBottom: margin_bottom,
            paddingLeft: effectivePaddingLeft,
            listStylePosition: "outside",
          }}
        >
          {(block.items ?? []).map((item, i) => (
            <li
              key={i}
              style={{
                textIndent: effectiveHangingIndent > 0 ? `-${effectiveHangingIndent}px` : undefined,
                paddingLeft: effectiveHangingIndent > 0 ? `${effectiveHangingIndent}px` : undefined,
              }}
            >
              {item}
            </li>
          ))}
        </ul>
      );
    }
    case "table": {
      const defaultCellPadding = block.style.cell_padding_dxa || { top: 120, bottom: 120, left: 180, right: 180 };
      const defaultPaddingPx = {
        top: defaultCellPadding.top * DXA_TO_PX,
        bottom: defaultCellPadding.bottom * DXA_TO_PX,
        left: defaultCellPadding.left * DXA_TO_PX,
        right: defaultCellPadding.right * DXA_TO_PX,
      };

      const tableRows = block.table_cells || (block.rows?.map((row) => row.map((text) => ({ text }))) ?? []);

      return (
        <table style={{ ...css, marginTop: margin_top, marginBottom: margin_bottom, width: "100%", borderCollapse: "collapse" }}>
          <tbody>
            {tableRows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td
                    key={c}
                    colSpan={cell.col_span ?? 1}
                    rowSpan={cell.row_span ?? 1}
                    style={{
                      border: block.style.border_visible ? "1px solid rgba(0,0,0,0.25)" : "1px solid transparent",
                      paddingTop: (cell.padding_top_px ?? defaultPaddingPx.top),
                      paddingBottom: (cell.padding_bottom_px ?? defaultPaddingPx.bottom),
                      paddingLeft: (cell.padding_left_px ?? defaultPaddingPx.left),
                      paddingRight: (cell.padding_right_px ?? defaultPaddingPx.right),
                      verticalAlign: (cell.vertical_align ?? "top") as "top" | "middle" | "bottom",
                      wordBreak: "break-word",
                    }}
                  >
                    {typeof cell === 'string' ? cell : cell.text}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
    }
    case "image_placeholder":
      return (
        <div style={{ ...css, marginTop: margin_top, marginBottom: margin_bottom }}>
          {block.image_ref ? (
            <img src={block.image_ref} alt="" style={{ maxWidth: "100%" }} />
          ) : (
            <div style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              height: "128px",
              border: "2px dashed rgba(0,0,0,0.3)",
              borderRadius: "6px",
              fontSize: "12px",
              color: "rgba(0,0,0,0.4)",
            }}>
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
            style={{
              paddingTop: layout.margin_px.top,
              paddingRight: layout.margin_px.right,
              paddingBottom: layout.margin_px.bottom,
              paddingLeft: layout.margin_px.left,
            }}
          >
            {blocks.map((block, index) => (
              <RenderedBlock key={block.id} block={block} index={index} totalBlocks={blocks.length} />
            ))}
            {blocks.length === 0 && (
              <p style={{ paddingTop: "48px", paddingBottom: "48px", textAlign: "center", fontSize: "14px", color: "rgba(0,0,0,0.4)" }}>Empty document</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
