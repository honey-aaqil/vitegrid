import {
  AlignmentType,
  BorderStyle,
  Document,
  HeadingLevel,
  ImageRun,
  Packer,
  Paragraph,
  Table,
  TableCell,
  TableRow,
  TextRun,
  WidthType,
} from "docx";
import { saveAs } from "file-saver";

import type { DocumentBlock, DocumentLayout, StyleTokens } from "../types";

export const EMU_PER_INCH = 914_400;
export const EMU_PER_CM = 360_000;
export const EMU_PER_PIXEL = 9_525;

export function pxToEmu(px: number): number {
  return Math.round(px * EMU_PER_PIXEL);
}

export function inchesToEmu(inches: number): number {
  return Math.round(inches * EMU_PER_INCH);
}

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

async function blockToChildren(block: DocumentBlock): Promise<(Paragraph | Table)[]> {
  switch (block.type) {
    case "heading":
      return [
        new Paragraph({
          heading: HeadingLevel.HEADING_1,
          alignment: alignmentFor(block.style),
          children: [textRun(block.text ?? "", block.style)],
        }),
      ];
    case "paragraph":
      return [
        new Paragraph({
          alignment: alignmentFor(block.style),
          children: [textRun(block.text ?? "", block.style)],
        }),
      ];
    case "list":
      return (block.items ?? []).map(
        (item) =>
          new Paragraph({
            bullet: { level: 0 },
            alignment: alignmentFor(block.style),
            children: [textRun(item, block.style)],
          }),
      );
    case "table": {
      const rows = block.rows ?? [];
      const visible = block.style.border_visible !== false;
      const border = {
        style: visible ? BorderStyle.SINGLE : BorderStyle.NONE,
        size: visible ? 4 : 0,
        color: "000000",
      };
      const table = new Table({
        width: { size: 100, type: WidthType.PERCENTAGE },
        rows: rows.map(
          (row) =>
            new TableRow({
              children: row.map(
                (cell) =>
                  new TableCell({
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
            alignment: alignmentFor(block.style),
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

export async function compileToDocx(layout: DocumentLayout): Promise<Blob> {
  const childrenLists = await Promise.all(layout.blocks.map(blockToChildren));
  const children = childrenLists.flat();

  const doc = new Document({
    sections: [
      {
        properties: {
          page: {
            margin: {
              top: pxToEmu(layout.margin_px.top),
              right: pxToEmu(layout.margin_px.right),
              bottom: pxToEmu(layout.margin_px.bottom),
              left: pxToEmu(layout.margin_px.left),
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
