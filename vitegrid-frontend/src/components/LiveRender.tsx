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

  let colorValue = s.color_hex;
  if (colorValue && !colorValue.startsWith("#")) {
    colorValue = `#${colorValue}`;
  }
  if (!colorValue) {
    colorValue = "#000000";
  }

  let bgColor: string | undefined = s.background_hex ?? undefined;
  if (bgColor && bgColor !== "FFFFFF" && bgColor !== "#FFFFFF") {
    if (!bgColor.startsWith("#")) {
      bgColor = `#${bgColor}`;
    }
  } else {
    bgColor = undefined;
  }

  let borderTop = undefined;
  let borderBottom = undefined;
  let borderLeft = undefined;
  let borderRight = undefined;

  if (s.line_alignment && s.line_alignment !== "none") {
    const thickness = `${s.line_thickness_px ?? 1}px`;
    let color = s.line_color_hex ?? "000000";
    if (!color.startsWith("#")) {
      color = `#${color}`;
    }
    const borderStr = `${thickness} solid ${color}`;

    if (s.line_alignment === "top") borderTop = borderStr;
    else if (s.line_alignment === "bottom") borderBottom = borderStr;
    else if (s.line_alignment === "left") borderLeft = borderStr;
    else if (s.line_alignment === "right") borderRight = borderStr;
    else if (s.line_alignment === "all") {
      borderTop = borderStr;
      borderBottom = borderStr;
      borderLeft = borderStr;
      borderRight = borderStr;
    }
  }

  return {
    color: colorValue,
    backgroundColor: bgColor || undefined,
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
    borderTop,
    borderBottom,
    borderLeft,
    borderRight,
  };
}

function RenderedBlock({ block, index }: { block: DocumentBlock; index: number }) {
  const css = styleToCss(block);
  const margin_top = index === 0 ? 0 : block.spacing.before_px ?? 0;
  const margin_bottom = block.spacing.after_px ?? 0;

  // DETECT ABSOLUTE GEOMETRY ANCHORS
  const hasAbsoluteGeometry = !!(block.bbox && block.bbox.width_px > 0 && block.bbox.height_px > 0);

  const wrapperStyle: React.CSSProperties = hasAbsoluteGeometry ? {
    position: "absolute",
    left: `${block.bbox!.x_px}px`,
    top: `${block.bbox!.y_px}px`,
    width: `${block.bbox!.width_px}px`,
    height: `${block.bbox!.height_px}px`,
    margin: 0,
    boxSizing: "border-box",
    overflow: "hidden",
  } : {
    marginTop: margin_top,
    marginBottom: margin_bottom,
  };

  const renderInnerContent = () => {
    const cleanCss = hasAbsoluteGeometry ? { ...css, marginTop: 0, marginBottom: 0 } : css;

    switch (block.type) {
    case "heading":
      return (
        <h2 style={{ ...cleanCss, margin: hasAbsoluteGeometry ? 0 : undefined, padding: hasAbsoluteGeometry ? 0 : undefined, marginTop: hasAbsoluteGeometry ? 0 : margin_top, marginBottom: hasAbsoluteGeometry ? 0 : margin_bottom }}>
          {block.text}
        </h2>
      );
    case "paragraph":
      return (
        <p style={{ ...cleanCss, margin: hasAbsoluteGeometry ? 0 : undefined, padding: hasAbsoluteGeometry ? 0 : undefined, marginTop: hasAbsoluteGeometry ? 0 : margin_top, marginBottom: hasAbsoluteGeometry ? 0 : margin_bottom }}>
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
            ...cleanCss,
            margin: hasAbsoluteGeometry ? 0 : undefined,
            padding: hasAbsoluteGeometry ? 0 : undefined,
            marginTop: hasAbsoluteGeometry ? 0 : margin_top,
            marginBottom: hasAbsoluteGeometry ? 0 : margin_bottom,
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

      const tableRows = block.table_cells || (block.rows?.map((row) => row.map((text) => ({ text, col_span: 1, row_span: 1, padding_top_px: 8, padding_bottom_px: 8, padding_left_px: 12, padding_right_px: 12, vertical_align: "top" as const }))) ?? []);

      return (
        <table style={{ ...cleanCss, margin: hasAbsoluteGeometry ? 0 : undefined, padding: hasAbsoluteGeometry ? 0 : undefined, marginTop: hasAbsoluteGeometry ? 0 : margin_top, marginBottom: hasAbsoluteGeometry ? 0 : margin_bottom, width: "100%", borderCollapse: "collapse" }}>
          <tbody>
            {tableRows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td
                    key={c}
                    colSpan={cell.col_span ?? 1}
                    rowSpan={cell.row_span ?? 1}
                    style={{
                      border: block.style.border_visible
                        ? `${block.style.border_width_px ?? 1}px ${block.style.border_style ?? "solid"} ${block.style.border_color_rgba ?? "rgba(0,0,0,1)"}`
                        : "1px solid transparent",
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
    case "image_placeholder": {
      const imgWidth = block.image_width_px ?? 200;
      const imgHeight = block.image_height_px ?? 150;
      const placeholderHeight = Math.max(imgHeight, 128);

      return (
        <div style={{ ...cleanCss, margin: hasAbsoluteGeometry ? 0 : undefined, marginTop: hasAbsoluteGeometry ? 0 : margin_top, marginBottom: hasAbsoluteGeometry ? 0 : margin_bottom }}>
          {block.image_ref ? (
            <img
              src={block.image_ref}
              alt=""
              style={{
                width: imgWidth,
                height: imgHeight,
                maxWidth: "100%",
                objectFit: "contain",
              }}
            />
          ) : (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                height: `${placeholderHeight}px`,
                border: "2px dashed rgba(0,0,0,0.3)",
                borderRadius: "6px",
                fontSize: "12px",
                color: "rgba(0,0,0,0.4)",
              }}
            >
              image placeholder
            </div>
          )}
        </div>
      );
    }
    case "divider": {
      // If line_alignment is specified, styleToCss already maps it to clean CSS borders on cleanCss.
      // So we just render a container div with cleanCss.
      if (block.style.line_alignment && block.style.line_alignment !== "none") {
        return (
          <div
            style={{
              ...cleanCss,
              margin: hasAbsoluteGeometry ? 0 : undefined,
              marginTop: hasAbsoluteGeometry ? 0 : margin_top,
              marginBottom: hasAbsoluteGeometry ? 0 : margin_bottom,
              width: "100%",
              height: "100%",
              boxSizing: "border-box",
            }}
          />
        );
      }

      let dividerColor = block.style.border_color_rgba;
      if (!dividerColor && block.style.color_hex) {
        dividerColor = block.style.color_hex.startsWith("#")
          ? block.style.color_hex
          : `#${block.style.color_hex}`;
      }
      if (!dividerColor) {
        dividerColor = "rgba(0, 0, 0, 0.5)";
      }

      const borderWidth = block.style.border_width_px ?? 1;
      const borderStyle = block.style.border_style ?? "solid";

      return (
        <div
          style={{
            ...cleanCss,
            margin: hasAbsoluteGeometry ? 0 : undefined,
            marginTop: hasAbsoluteGeometry ? 0 : margin_top,
            marginBottom: hasAbsoluteGeometry ? 0 : margin_bottom,
            display: "flex",
            alignItems: "center",
            width: "100%",
            height: "100%",
          }}
        >
          <hr
            style={{
              border: "none",
              borderTop: `${borderWidth}px ${borderStyle} ${dividerColor}`,
              margin: 0,
              width: "100%",
            }}
          />
        </div>
      );
    }
    }
  };

  return <div style={wrapperStyle} data-block-id={block.id}>{renderInnerContent()}</div>;
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
              position: "relative",
              width: "100%",
              minHeight: layout.page_height_px,
              paddingTop: layout.margin_px.top,
              paddingRight: layout.margin_px.right,
              paddingBottom: layout.margin_px.bottom,
              paddingLeft: layout.margin_px.left,
            }}
          >
            {blocks.map((block, index) => (
              <RenderedBlock key={block.id} block={block} index={index} />
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
