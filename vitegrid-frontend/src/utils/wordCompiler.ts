import {
  AlignmentType,
  BorderStyle,
  Document,
  HeadingLevel,
  ImageRun,
  LevelFormat,
  LineRuleType,
  Packer,
  Paragraph,
  ParagraphRunProperties,
  Table,
  TableCell,
  TableLayoutType,
  TableRow,
  TextRun,
  WidthType,
  XmlComponent,
} from "docx";
import type {
  CellPaddingDxa,
  DocumentBlock,
  DocumentLayout,
  ListFormat,
  SpacingTokens,
  StyleTokens,
} from "../types";
import { DEFAULT_SPACING_TOKENS, DEFAULT_STYLE_TOKENS } from "../types";

export const EMU_PER_INCH = 914_400;
export const EMU_PER_CM = 360_000;
export const EMU_PER_PIXEL = 9_525;
export const DXA_PER_PIXEL = 15;
export const DXA_PER_INCH = 1_440;

export function pxToEmu(px: number): number {
  return Math.round(px * EMU_PER_PIXEL);
}

export function pxToDxa(px: number): number {
  return Math.round(px * DXA_PER_PIXEL);
}

export function inchesToEmu(inches: number): number {
  return Math.round(inches * EMU_PER_INCH);
}

export class CustomFonts extends XmlComponent {
  constructor(asciiTheme?: string, cstheme?: string, ascii?: string) {
    super("w:rFonts");
    this.root = [{
      _attr: {
        "w:asciiTheme": asciiTheme || "majorHAnsi",
        "w:cstheme": cstheme || "majorBidi",
        "w:ascii": ascii || "Arial",
        "w:hAnsi": ascii || "Arial",
      }
    }];
  }
}

export function createSafeParagraph(
  options: ConstructorParameters<typeof Paragraph>[0],
  style: StyleTokens | undefined,
): Paragraph {
  const fontSize = style && style.font_size_pt && style.font_size_pt > 0 ? style.font_size_pt : 11.0;
  const sizeVal = Math.max(2, Math.round(fontSize * 2));

  const mergedOptions = {
    ...options,
    run: {
      ...options.run,
      size: sizeVal,
    },
  };

  const p = new Paragraph(mergedOptions);

  let rPr = p.properties.root.find((child) => child.constructor.name === "ParagraphRunProperties");
  if (!rPr) {
    rPr = new ParagraphRunProperties();
    p.properties.push(rPr);
  }

  const fontFamily = style ? style.font_family : "Arial";
  rPr.push(new CustomFonts("majorHAnsi", "majorBidi", fontFamily));

  return p;
}

const DEFAULT_CELL_PADDING_DXA: CellPaddingDxa = {
  top: 120,
  bottom: 120,
  left: 180,
  right: 180,
};

const LIST_REFERENCE_BY_FORMAT: Record<ListFormat, string> = {
  bullet: "vg-bullet",
  decimal: "vg-decimal",
  lowerLetter: "vg-lower-letter",
  upperRoman: "vg-upper-roman",
};

export interface FittedDimensions {
  width_px: number;
  height_px: number;
  width_emu: number;
  height_emu: number;
  scale_factor: number;
}

export function fitImageToBox(
  imageWidth: number,
  imageHeight: number,
  boxWidth: number,
  boxHeight: number,
): FittedDimensions {
  if (imageWidth <= 0 || imageHeight <= 0) {
    return { width_px: 0, height_px: 0, width_emu: 0, height_emu: 0, scale_factor: 0 };
  }
  const sf = Math.min(boxWidth / imageWidth, boxHeight / imageHeight);
  const w = imageWidth * sf;
  const h = imageHeight * sf;
  return {
    width_px: w,
    height_px: h,
    width_emu: pxToEmu(w),
    height_emu: pxToEmu(h),
    scale_factor: sf,
  };
}

function alignmentFor(style: StyleTokens): (typeof AlignmentType)[keyof typeof AlignmentType] {
  switch (style.align) {
    case "center":
      return AlignmentType.CENTER;
    case "right":
      return AlignmentType.RIGHT;
    case "justify":
      return AlignmentType.JUSTIFIED;
    default:
      return AlignmentType.LEFT;
  }
}

function lineRuleFor(rule: SpacingTokens["line_rule"]): (typeof LineRuleType)[keyof typeof LineRuleType] {
  switch (rule) {
    case "exact":
      return LineRuleType.EXACT;
    case "atLeast":
      return LineRuleType.AT_LEAST;
    default:
      return LineRuleType.AUTO;
  }
}

function spacingFor(spacing: SpacingTokens | undefined): {
  readonly before: number;
  readonly after: number;
  readonly line: number;
  readonly lineRule: (typeof LineRuleType)[keyof typeof LineRuleType];
} {
  // Blueprint: every paragraph emits <w:spacing> with deterministic defaults.
  const s = spacing ?? DEFAULT_SPACING_TOKENS;
  return {
    before: s.before_dxa,
    after: s.after_dxa,
    line: s.line_spacing_dxa,
    lineRule: lineRuleFor(s.line_rule),
  };
}

function cellPaddingFor(style: StyleTokens): CellPaddingDxa {
  return style.cell_padding_dxa ?? DEFAULT_CELL_PADDING_DXA;
}

function textRun(text: string, style: StyleTokens): TextRun {
  const fontSize = style.font_size_pt && style.font_size_pt > 0 ? style.font_size_pt : 11.0;
  const sizeVal = Math.max(2, Math.round(fontSize * 2));

  const run = new TextRun({
    text,
    bold: style.font_weight === "bold",
    color: style.color_hex ? style.color_hex.replace("#", "") : undefined,
    font: style.font_family,
    size: sizeVal,
  });

  if (run.properties) {
    run.properties.push(new CustomFonts("majorHAnsi", "majorBidi", style.font_family));
  }

  return run;
}

async function loadImageBytes(url: string): Promise<{ data: ArrayBuffer; width: number; height: number }> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch image: ${url}`);
  const blob = await res.blob();
  const data = await blob.arrayBuffer();
  const dims = await new Promise<{ width: number; height: number }>((resolve, reject) => {
    const img = new Image();
    const objectUrl = URL.createObjectURL(blob);
    img.onload = () => {
      resolve({ width: img.naturalWidth, height: img.naturalHeight });
      URL.revokeObjectURL(objectUrl);
    };
    img.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error(`Failed to decode image: ${url}`));
    };
    img.src = objectUrl;
  });
  return { data, ...dims };
}

async function blockToChildren(
  block: DocumentBlock,
  contentWidthDxa: number,
  spacingOverride?: { before: number; after: number },
): Promise<(Paragraph | Table)[]> {
  const alignment = alignmentFor(block.style);
  const resolvedSpacingTokens = {
    ...block.spacing,
    before_dxa: spacingOverride ? spacingOverride.before : (block.spacing?.before_dxa ?? 0),
    after_dxa: spacingOverride ? spacingOverride.after : (block.spacing?.after_dxa ?? 0),
  };
  const spacing = spacingFor(resolvedSpacingTokens);

  switch (block.type) {
    case "heading":
      return [
        createSafeParagraph({
          heading: HeadingLevel.HEADING_1,
          alignment,
          spacing,
          children: [textRun(block.text ?? "", block.style)],
        }, block.style),
      ];
    case "paragraph":
      return [
        createSafeParagraph({
          alignment,
          spacing,
          children: [textRun(block.text ?? "", block.style)],
        }, block.style),
      ];
    case "list": {
      const items = block.items ?? [];
      const format: ListFormat = block.style.list_format ?? "bullet";
      const reference = LIST_REFERENCE_BY_FORMAT[format];
      const level = Math.max(0, Math.min(block.style.list_level ?? 0, 5));
      return items.map(
        (item) =>
          createSafeParagraph({
            alignment,
            spacing,
            numbering: { reference, level },
            children: [textRun(item, block.style)],
          }, block.style),
      );
    }
    case "table": {
      const rows = block.rows ?? [];
      if (rows.length === 0) return [];
      const colCount = Math.max(1, ...rows.map((r) => r.length));

      // Allocate DXA per column proportional to the max character length
      // observed in that column. Columns of dense paragraphs expand;
      // columns of short tokens shrink. A 30px floor per column prevents
      // zero-width collapse — we reserve the floor first, then distribute
      // only the remainder proportionally so that the columns sum to
      // exactly `contentWidthDxa` (avoids Word rebalancing the layout).
      const colMaxChars = Array(colCount).fill(1);
      for (const row of rows) {
        for (let c = 0; c < colCount; c++) {
          const cell = row[c] ?? "";
          const len = cell.trim().length;
          if (len > colMaxChars[c]) colMaxChars[c] = len;
        }
      }
      const minColDxa = pxToDxa(30);
      const reservedDxa = Math.min(minColDxa * colCount, contentWidthDxa);
      const remainderDxa = Math.max(0, contentWidthDxa - reservedDxa);
      const totalChars = colMaxChars.reduce((sum, val) => sum + val, 0) || 1;
      const columnWidthsDxa = colMaxChars.map((chars) => {
        const ratio = chars / totalChars;
        return Math.floor(reservedDxa / colCount) + Math.floor(remainderDxa * ratio);
      });
      // Absorb any rounding drift (≤ colCount DXA) into the widest column
      // so column widths sum to exactly contentWidthDxa.
      const drift = contentWidthDxa - columnWidthsDxa.reduce((a, b) => a + b, 0);
      if (drift !== 0) {
        let widestIdx = 0;
        for (let i = 1; i < colCount; i++) {
          if (columnWidthsDxa[i] > columnWidthsDxa[widestIdx]) widestIdx = i;
        }
        columnWidthsDxa[widestIdx] += drift;
      }

      const visible = block.style.border_visible !== false;
      const border = {
        style: visible ? BorderStyle.SINGLE : BorderStyle.NONE,
        size: visible ? 4 : 0,
        color: "000000",
      };
      const padding = cellPaddingFor(block.style);

      const table = new Table({
        width: { size: contentWidthDxa, type: WidthType.DXA },
        columnWidths: columnWidthsDxa,
        layout: TableLayoutType.FIXED,
        rows: rows.map(
          (row) =>
            new TableRow({
              // Strict bounds: emit exactly colCount cells per row so a
              // short row never crashes the OpenXML generator on out-of-
              // bounds access.
              children: Array.from({ length: colCount }).map((_, colIndex) => {
                const cellText = row[colIndex] ?? "";
                const cellWidth = columnWidthsDxa[colIndex];
                const textToRender = cellText.trim() === "" ? " " : cellText;
                return new TableCell({
                  width: { size: cellWidth, type: WidthType.DXA },
                  margins: {
                    top: padding.top,
                    bottom: padding.bottom,
                    left: padding.left,
                    right: padding.right,
                    marginUnitType: WidthType.DXA,
                  },
                  borders: {
                    top: border,
                    bottom: border,
                    left: border,
                    right: border,
                  },
                  children: [
                    createSafeParagraph({
                      spacing: { before: 0, after: 0 },
                      children: [textRun(textToRender, { ...block.style, font_size_pt: cellText.trim() === "" ? 1.0 : block.style.font_size_pt })],
                    }, { ...block.style, font_size_pt: cellText.trim() === "" ? 1.0 : block.style.font_size_pt }),
                  ],
                });
              }),
            }),
        ),
      });
      return [table];
    }
    case "image_placeholder": {
      if (!block.image_ref || !block.bbox) {
        return [createSafeParagraph({ children: [textRun("[image placeholder]", block.style)] }, block.style)];
      }
      try {
        const { data, width, height } = await loadImageBytes(block.image_ref);
        const fitted = fitImageToBox(width, height, block.bbox.width_px, block.bbox.height_px);
        return [
          createSafeParagraph({
            alignment,
            spacing,
            children: [
              new ImageRun({
                data,
                transformation: { width: fitted.width_px, height: fitted.height_px },
              } as ConstructorParameters<typeof ImageRun>[0]),
            ],
          }, block.style),
        ];
      } catch {
        return [createSafeParagraph({ children: [textRun("[missing image]", block.style)] }, block.style)];
      }
    }
    case "divider": {
      let color = "777777";
      if (block.style.color_hex) {
        color = block.style.color_hex.replace("#", "");
      }
      if (block.style.border_color_rgba) {
        const m = block.style.border_color_rgba.match(/\d+/g);
        if (m && m.length >= 3) {
          const r = parseInt(m[0], 10);
          const g = parseInt(m[1], 10);
          const b = parseInt(m[2], 10);
          color = ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
        }
      }
      const borderWidth = block.style.border_width_px ?? 1;
      const borderSize = Math.max(1, Math.min(24, Math.round(borderWidth * 8))); // docx border size is in 1/8 pt

      return [
        createSafeParagraph({
          spacing: { before: 120, after: 120 },
          border: {
            bottom: {
              color,
              space: 1,
              size: borderSize,
              style: BorderStyle.SINGLE,
            },
          },
          children: [textRun(" ", { ...block.style, font_size_pt: 1.0 })],
        }, { ...block.style, font_size_pt: 1.0 }),
      ];
    }
  }
}

const NUMBERING_CONFIG = {
  config: [
    {
      reference: LIST_REFERENCE_BY_FORMAT.bullet,
      levels: [
        {
          level: 0,
          format: LevelFormat.BULLET,
          text: "•",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 360, hanging: 260 } } },
        },
        {
          level: 1,
          format: LevelFormat.BULLET,
          text: "◦",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 260 } } },
        },
      ],
    },
    {
      reference: LIST_REFERENCE_BY_FORMAT.decimal,
      levels: [
        {
          level: 0,
          format: LevelFormat.DECIMAL,
          text: "%1.",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 360, hanging: 260 } } },
        },
      ],
    },
    {
      reference: LIST_REFERENCE_BY_FORMAT.lowerLetter,
      levels: [
        {
          level: 0,
          format: LevelFormat.LOWER_LETTER,
          text: "%1.",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 360, hanging: 260 } } },
        },
      ],
    },
    {
      reference: LIST_REFERENCE_BY_FORMAT.upperRoman,
      levels: [
        {
          level: 0,
          format: LevelFormat.UPPER_ROMAN,
          text: "%1.",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 360, hanging: 260 } } },
        },
      ],
    },
  ],
};

export async function compileToDocx(layout: DocumentLayout): Promise<Blob> {
  const pageWidthDxa = pxToDxa(layout.page_width_px);
  const pageHeightDxa = pxToDxa(layout.page_height_px);
  const marginTopDxa = pxToDxa(layout.margin_px.top);
  const marginRightDxa = pxToDxa(layout.margin_px.right);
  const marginBottomDxa = pxToDxa(layout.margin_px.bottom);
  const marginLeftDxa = pxToDxa(layout.margin_px.left);
  const contentWidthDxa = Math.max(1, pageWidthDxa - marginLeftDxa - marginRightDxa);

  const contentLeft = layout.margin_px.left;
  const contentRight = layout.page_width_px - layout.margin_px.right;

  // Gather unique X coordinates to define fixed grid columns
  const xCoords: number[] = [contentLeft, contentRight];
  for (const block of layout.blocks) {
    if (block.bbox && block.bbox.width_px > 0 && block.bbox.height_px > 0) {
      xCoords.push(block.bbox.x_px);
      xCoords.push(block.bbox.x_px + block.bbox.width_px);
    }
  }

  xCoords.sort((a, b) => a - b);
  const uniqueX: number[] = [];
  for (const x of xCoords) {
    const clampedX = Math.max(contentLeft, Math.min(contentRight, x));
    if (uniqueX.length === 0) {
      uniqueX.push(clampedX);
    } else {
      const last = uniqueX[uniqueX.length - 1];
      if (clampedX - last >= 15) {
        uniqueX.push(clampedX);
      }
    }
  }
  if (uniqueX[uniqueX.length - 1] < contentRight - 5) {
    uniqueX.push(contentRight);
  }

  const numCols = uniqueX.length - 1;
  const colWidths = Array(numCols).fill(0);
  const colWidthsDxa = Array(numCols).fill(0);
  for (let c = 0; c < numCols; c++) {
    colWidths[c] = uniqueX[c + 1] - uniqueX[c];
    colWidthsDxa[c] = pxToDxa(colWidths[c]);
  }

  // Adjust column widths to sum exactly to contentWidthDxa
  const sumDxa = colWidthsDxa.reduce((a, b) => a + b, 0);
  const drift = contentWidthDxa - sumDxa;
  if (drift !== 0 && numCols > 0) {
    colWidthsDxa[numCols - 1] += drift;
  }

  // Find dominant background shading fill for each column to enable full-column panels
  const columnShading = Array(numCols).fill(null);
  for (const block of layout.blocks) {
    if (block.bbox && block.bbox.width_px > 0 && block.style.background_hex) {
      const bx0 = block.bbox.x_px;
      const bx1 = block.bbox.x_px + block.bbox.width_px;
      for (let c = 0; c < numCols; c++) {
        if (bx0 <= uniqueX[c] + 5 && bx1 >= uniqueX[c + 1] - 5) {
          columnShading[c] = block.style.background_hex.replace("#", "");
        }
      }
    }
  }

  // Group blocks into rows based on vertical overlap and horizontal layout tracks
  interface GridRow {
    yStart: number;
    yEnd: number;
    blocks: DocumentBlock[];
  }

  const sortedBlocks = [...layout.blocks].sort((a, b) => {
    const ay = a.bbox ? a.bbox.y_px : 0;
    const by = b.bbox ? b.bbox.y_px : 0;
    return ay - by;
  });

  const rows: GridRow[] = [];
  for (const block of sortedBlocks) {
    const bbox = block.bbox || {
      x_px: contentLeft,
      y_px: 0,
      width_px: contentRight - contentLeft,
      height_px: 30,
    };
    const yStart = bbox.y_px;
    const yEnd = bbox.y_px + bbox.height_px;

    let placed = false;
    for (const row of rows) {
      // Check horizontal overlap with blocks already in this row
      let hasHOverlap = false;
      for (const existing of row.blocks) {
        const eBbox = existing.bbox || {
          x_px: contentLeft,
          width_px: contentRight - contentLeft,
        };
        const overlap_left = Math.max(bbox.x_px, eBbox.x_px);
        const overlap_right = Math.min(
          bbox.x_px + bbox.width_px,
          eBbox.x_px + eBbox.width_px
        );
        if (overlap_right - overlap_left > 10) {
          hasHOverlap = true;
          break;
        }
      }

      // Vertical overlap check
      const vOverlap = Math.min(yEnd, row.yEnd) - Math.max(yStart, row.yStart);
      if (!hasHOverlap && vOverlap > -10) {
        row.blocks.push(block);
        row.yStart = Math.min(row.yStart, yStart);
        row.yEnd = Math.max(row.yEnd, yEnd);
        placed = true;
        break;
      }
    }

    if (!placed) {
      rows.push({ yStart, yEnd, blocks: [block] });
    }
  }

  rows.sort((a, b) => a.yStart - b.yStart);

  // Precompute vertically adjacent blocks' spacing overrides in each column track to enforce max spacing overlap
  const blockBeforeOverrides = new Map<string, number>();
  const blockAfterOverrides = new Map<string, number>();

  for (let col = 0; col < numCols; col++) {
    const columnBlocks: DocumentBlock[] = [];
    for (const row of rows) {
      const block = row.blocks.find((b) => {
        const bbox = b.bbox || {
          x_px: contentLeft,
          y_px: 0,
          width_px: contentRight - contentLeft,
          height_px: 30,
        };
        const bx0 = bbox.x_px;
        const bx1 = bbox.x_px + bbox.width_px;
        return bx0 <= uniqueX[col] + 5 && bx1 >= uniqueX[col + 1] - 5;
      });
      if (block && !columnBlocks.includes(block)) {
        columnBlocks.push(block);
      }
    }

    for (let i = 0; i < columnBlocks.length - 1; i++) {
      const b1 = columnBlocks[i];
      const b2 = columnBlocks[i + 1];

      const spaceAfter1 = b1.spacing ? b1.spacing.after_dxa : 0;
      const spaceBefore2 = b2.spacing ? b2.spacing.before_dxa : 0;
      const maxOverlap = Math.max(spaceAfter1, spaceBefore2);

      const currentAfter = blockAfterOverrides.get(b1.id) ?? 0;
      blockAfterOverrides.set(b1.id, Math.max(currentAfter, maxOverlap));
      blockBeforeOverrides.set(b2.id, 0);
    }
  }

  const docxRows: TableRow[] = [];

  const noBorder = {
    style: BorderStyle.NONE,
    size: 0,
    color: "auto",
  };

  for (const row of rows) {
    const rowCells: TableCell[] = [];
    let c = 0;
    while (c < numCols) {
      // Find block covering this column
      const block = row.blocks.find((b) => {
        const bbox = b.bbox || {
          x_px: contentLeft,
          y_px: 0,
          width_px: contentRight - contentLeft,
          height_px: 30,
        };
        const bx0 = bbox.x_px;
        const bx1 = bbox.x_px + bbox.width_px;
        return bx0 <= uniqueX[c] + 5 && bx1 >= uniqueX[c + 1] - 5;
      });

      if (block) {
        // Calculate colSpan
        let colSpan = 1;
        const bbox = block.bbox || {
          x_px: contentLeft,
          y_px: 0,
          width_px: contentRight - contentLeft,
          height_px: 30,
        };
        const bx1 = bbox.x_px + bbox.width_px;
        while (c + colSpan < numCols && uniqueX[c + colSpan + 1] <= bx1 + 5) {
          colSpan++;
        }

        const cellWidthDxa = colWidthsDxa
          .slice(c, c + colSpan)
          .reduce((sum, w) => sum + w, 0);

        const spacingOverride = {
          before: blockBeforeOverrides.has(block.id) ? blockBeforeOverrides.get(block.id)! : (block.spacing?.before_dxa ?? 0),
          after: blockAfterOverrides.has(block.id) ? blockAfterOverrides.get(block.id)! : (block.spacing?.after_dxa ?? 0),
        };
        const blockChildren = await blockToChildren(block, cellWidthDxa, spacingOverride);

        // Apply background shading
        let shadingFill = columnShading[c] || undefined;
        if (block.style.background_hex) {
          shadingFill = block.style.background_hex.replace("#", "");
        }

        // Apply borders (native paragraph border rules / line alignment / tables)
        let cellBorders: any = {
          top: noBorder,
          bottom: noBorder,
          left: noBorder,
          right: noBorder,
        };

        if (block.style.border_visible && block.style.border_style && block.style.border_style !== "none") {
          const thickness = block.style.border_width_px ?? 1;
          const size = Math.max(1, Math.min(24, Math.round(thickness * 8)));
          let color = "000000";
          if (block.style.border_color_rgba) {
            const m = block.style.border_color_rgba.match(/\d+/g);
            if (m && m.length >= 3) {
              const r = parseInt(m[0], 10);
              const g = parseInt(m[1], 10);
              const b = parseInt(m[2], 10);
              color = ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
            }
          }
          const borderStyleMap: Record<string, BorderStyle> = {
            solid: BorderStyle.SINGLE,
            dashed: BorderStyle.DASHED,
            dotted: BorderStyle.DOTTED,
            double: BorderStyle.DOUBLE,
            none: BorderStyle.NONE,
          };
          const borderOpts = {
            style: borderStyleMap[block.style.border_style] || BorderStyle.SINGLE,
            size,
            color,
          };
          cellBorders = {
            top: borderOpts,
            bottom: borderOpts,
            left: borderOpts,
            right: borderOpts,
          };
        } else if (block.style.line_alignment && block.style.line_alignment !== "none") {
          const alignment = block.style.line_alignment;
          const thickness = block.style.line_thickness_px ?? 1;
          const size = Math.max(1, Math.min(24, Math.round(thickness * 8)));
          let color = block.style.line_color_hex || "000000";
          color = color.replace("#", "");

          const borderOpts = {
            style: BorderStyle.SINGLE,
            size,
            color,
          };

          if (alignment === "top" || alignment === "all") cellBorders.top = borderOpts;
          if (alignment === "bottom" || alignment === "all") cellBorders.bottom = borderOpts;
          if (alignment === "left" || alignment === "all") cellBorders.left = borderOpts;
          if (alignment === "right" || alignment === "all") cellBorders.right = borderOpts;
        }

        const padTop = block.style.cell_padding_dxa && block.style.cell_padding_dxa.top !== 120 ? block.style.cell_padding_dxa.top : 0;
        const padBottom = block.style.cell_padding_dxa && block.style.cell_padding_dxa.bottom !== 120 ? block.style.cell_padding_dxa.bottom : 0;
        const padLeft = block.style.cell_padding_dxa && block.style.cell_padding_dxa.left !== 180 ? block.style.cell_padding_dxa.left : 0;
        const padRight = block.style.cell_padding_dxa && block.style.cell_padding_dxa.right !== 180 ? block.style.cell_padding_dxa.right : 0;

        rowCells.push(
          new TableCell({
            width: { size: cellWidthDxa, type: WidthType.DXA },
            columnSpan: colSpan > 1 ? colSpan : undefined,
            shading: shadingFill ? { fill: shadingFill } : undefined,
            borders: cellBorders,
            margins: {
              top: padTop,
              bottom: padBottom,
              left: padLeft,
              right: padRight,
              marginUnitType: WidthType.DXA,
            },
            children: blockChildren.length > 0 ? blockChildren : [
              createSafeParagraph({
                spacing: { before: 0, after: 0 },
                children: [textRun(" ", { ...DEFAULT_STYLE_TOKENS, font_size_pt: 1.0 })],
              }, { ...DEFAULT_STYLE_TOKENS, font_size_pt: 1.0 })
            ],
          })
        );
        c += colSpan;
      } else {
        // Empty Cell
        rowCells.push(
          new TableCell({
            width: { size: colWidthsDxa[c], type: WidthType.DXA },
            shading: columnShading[c] ? { fill: columnShading[c] } : undefined,
            borders: {
              top: noBorder,
              bottom: noBorder,
              left: noBorder,
              right: noBorder,
            },
            margins: {
              top: 0,
              bottom: 0,
              left: 0,
              right: 0,
              marginUnitType: WidthType.DXA,
            },
            children: [
              createSafeParagraph({
                spacing: { before: 0, after: 0 },
                children: [textRun(" ", { ...DEFAULT_STYLE_TOKENS, font_size_pt: 1.0 })],
              }, { ...DEFAULT_STYLE_TOKENS, font_size_pt: 1.0 })
            ],
          })
        );
        c++;
      }
    }

    docxRows.push(new TableRow({ children: rowCells }));
  }

  const masterTable = new Table({
    width: { size: contentWidthDxa, type: WidthType.DXA },
    columnWidths: colWidthsDxa,
    layout: TableLayoutType.FIXED,
    rows: docxRows,
  });

  const doc = new Document({
    numbering: NUMBERING_CONFIG,
    sections: [
      {
        properties: {
          page: {
            size: { width: pageWidthDxa, height: pageHeightDxa },
            margin: {
              top: marginTopDxa,
              right: marginRightDxa,
              bottom: marginBottomDxa,
              left: marginLeftDxa,
            },
          },
        },
        children: [masterTable],
      },
    ],
  });

  return Packer.toBlob(doc);
}

export async function downloadDocx(layout: DocumentLayout, filename = "vitegrid.docx"): Promise<void> {
  // Lazy import so the compiler module also loads cleanly under Node (for tests)
  // where the browser-only `file-saver` package would otherwise blow up.
  const { saveAs } = await import("file-saver");
  const blob = await compileToDocx(layout);
  saveAs(blob, filename);
}
