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
  Table,
  TableCell,
  TableLayoutType,
  TableRow,
  TextRun,
  WidthType,
} from "docx";
import { saveAs } from "file-saver";

import type {
  CellPaddingDxa,
  DocumentBlock,
  DocumentLayout,
  ListFormat,
  SpacingTokens,
  StyleTokens,
} from "../types";
import { DEFAULT_SPACING_TOKENS } from "../types";

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
  return new TextRun({
    text,
    bold: style.font_weight === "bold",
    color: style.color_hex ? style.color_hex.replace("#", "") : undefined,
    font: style.font_family,
    size: style.font_size_pt ? Math.round(style.font_size_pt * 2) : undefined,
  });
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
): Promise<(Paragraph | Table)[]> {
  const alignment = alignmentFor(block.style);
  const spacing = spacingFor(block.spacing);

  switch (block.type) {
    case "heading":
      return [
        new Paragraph({
          heading: HeadingLevel.HEADING_1,
          alignment,
          spacing,
          children: [textRun(block.text ?? "", block.style)],
        }),
      ];
    case "paragraph":
      return [
        new Paragraph({
          alignment,
          spacing,
          children: [textRun(block.text ?? "", block.style)],
        }),
      ];
    case "list": {
      const items = block.items ?? [];
      const format: ListFormat = block.style.list_format ?? "bullet";
      const reference = LIST_REFERENCE_BY_FORMAT[format];
      const level = Math.max(0, Math.min(block.style.list_level ?? 0, 5));
      return items.map(
        (item) =>
          new Paragraph({
            alignment,
            spacing,
            numbering: { reference, level },
            children: [textRun(item, block.style)],
          }),
      );
    }
    case "table": {
      const rows = block.rows ?? [];
      if (rows.length === 0) return [];
      const colCount = Math.max(1, ...rows.map((r) => r.length));
      const colWidthDxa = Math.floor(contentWidthDxa / colCount);
      const visible = block.style.border_visible !== false;
      const border = {
        style: visible ? BorderStyle.SINGLE : BorderStyle.NONE,
        size: visible ? 4 : 0,
        color: "000000",
      };
      const padding = cellPaddingFor(block.style);
      const table = new Table({
        width: { size: contentWidthDxa, type: WidthType.DXA },
        columnWidths: Array(colCount).fill(colWidthDxa),
        layout: TableLayoutType.FIXED,
        rows: rows.map(
          (row) =>
            new TableRow({
              children: row.map(
                (cell) =>
                  new TableCell({
                    width: { size: colWidthDxa, type: WidthType.DXA },
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
                    children: [new Paragraph({ children: [textRun(cell, block.style)] })],
                  }),
              ),
            }),
        ),
      });
      return [table];
    }
    case "image_placeholder": {
      if (!block.image_ref || !block.bbox) {
        return [new Paragraph({ children: [textRun("[image placeholder]", block.style)] })];
      }
      try {
        const { data, width, height } = await loadImageBytes(block.image_ref);
        const fitted = fitImageToBox(width, height, block.bbox.width_px, block.bbox.height_px);
        return [
          new Paragraph({
            alignment,
            spacing,
            children: [
              new ImageRun({
                data,
                transformation: { width: fitted.width_px, height: fitted.height_px },
              } as ConstructorParameters<typeof ImageRun>[0]),
            ],
          }),
        ];
      } catch {
        return [new Paragraph({ children: [textRun("[missing image]", block.style)] })];
      }
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

  const childrenLists = await Promise.all(
    layout.blocks.map((block) => blockToChildren(block, contentWidthDxa)),
  );
  const children = childrenLists.flat();

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
        children,
      },
    ],
  });

  return Packer.toBlob(doc);
}

export async function downloadDocx(layout: DocumentLayout, filename = "vitegrid.docx"): Promise<void> {
  const blob = await compileToDocx(layout);
  saveAs(blob, filename);
}
