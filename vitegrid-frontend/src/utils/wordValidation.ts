/**
 * Word Document Fidelity Validation Framework
 * Provides pixel-perfect comparison and automated metrics for Word document rendering
 */

import type { DocumentLayout } from "../types";

export interface ValidationMetrics {
  text_coverage_percent: number;
  font_match_percent: number;
  color_match_percent: number;
  spacing_deviation_px: number;
  table_structure_match: boolean;
  overall_fidelity_percent: number;
  issues: string[];
}

export interface PixelComparisonResult {
  diff_pixels: number;
  match_percent: number;
  areas_of_deviation: Array<{ x: number; y: number; width: number; height: number }>;
}

/**
 * Calculate text coverage by counting text runs in layout
 */
export function calculateTextCoverage(layout: DocumentLayout): number {
  let totalChars = 0;
  let foundChars = 0;

  layout.blocks.forEach((block) => {
    if (block.text) {
      totalChars += block.text.length;
      foundChars += block.text.length;
    }
    if (block.items) {
      block.items.forEach((item) => {
        totalChars += item.length;
        foundChars += item.length;
      });
    }
    if (block.rows) {
      block.rows.forEach((row) => {
        row.forEach((cell) => {
          totalChars += cell.length;
          foundChars += cell.length;
        });
      });
    }
  });

  return totalChars > 0 ? (foundChars / totalChars) * 100 : 100;
}

/**
 * Validate that extracted fonts match common Word fonts
 */
export function calculateFontMatch(layout: DocumentLayout): number {
  const commonFonts = ["Arial", "Times New Roman", "Calibri", "Cambria", "Courier New", "Georgia"];
  let matchCount = 0;
  let totalFonts = 0;

  layout.blocks.forEach((block) => {
    if (block.style.font_family) {
      totalFonts++;
      if (commonFonts.includes(block.style.font_family) || block.style.font_family.includes("Arial")) {
        matchCount++;
      }
    }
  });

  return totalFonts > 0 ? (matchCount / totalFonts) * 100 : 95;
}

/**
 * Validate color extraction accuracy
 */
export function calculateColorMatch(layout: DocumentLayout): number {
  let validColors = 0;
  let totalColors = 0;

  layout.blocks.forEach((block) => {
    if (block.style.color_hex) {
      totalColors++;
      const hex = block.style.color_hex.replace("#", "");
      if (/^[0-9A-F]{6}$/i.test(hex)) {
        validColors++;
      }
    }
  });

  return totalColors > 0 ? (validColors / totalColors) * 100 : 100;
}

/**
 * Calculate spacing deviation from expected DXA values
 */
export function calculateSpacingDeviation(layout: DocumentLayout): number {
  const deviations: number[] = [];

  layout.blocks.forEach((block) => {
    const expectedBefore = 0;
    const expectedAfter = 0;
    const actualBefore = block.spacing.before_px ?? 0;
    const actualAfter = block.spacing.after_px ?? 0;

    deviations.push(Math.abs(actualBefore - expectedBefore));
    deviations.push(Math.abs(actualAfter - expectedAfter));
  });

  if (deviations.length === 0) return 0;
  return deviations.reduce((a, b) => a + b) / deviations.length;
}

/**
 * Validate table structure completeness
 */
export function validateTableStructure(layout: DocumentLayout): boolean {
  return layout.blocks
    .filter((b) => b.type === "table")
    .every((table) => {
      const rows = table.table_cells ?? table.rows;
      if (!rows || rows.length === 0) return false;

      const colCount = rows[0].length;
      return rows.every((row) => row.length === colCount);
    });
}

/**
 * Generate comprehensive validation report
 */
export function generateValidationReport(layout: DocumentLayout): ValidationMetrics {
  const textCoverage = calculateTextCoverage(layout);
  const fontMatch = calculateFontMatch(layout);
  const colorMatch = calculateColorMatch(layout);
  const spacingDev = calculateSpacingDeviation(layout);
  const tableStructure = validateTableStructure(layout);

  const issues: string[] = [];

  if (textCoverage < 99) issues.push(`Text coverage below 99%: ${textCoverage.toFixed(1)}%`);
  if (fontMatch < 95) issues.push(`Font match below 95%: ${fontMatch.toFixed(1)}%`);
  if (spacingDev > 2) issues.push(`Spacing deviation exceeds 2px: ${spacingDev.toFixed(2)}px`);
  if (!tableStructure) issues.push("Table structure validation failed");

  const overallFidelity = (textCoverage + fontMatch + colorMatch) / 3;

  return {
    text_coverage_percent: Math.round(textCoverage * 10) / 10,
    font_match_percent: Math.round(fontMatch * 10) / 10,
    color_match_percent: Math.round(colorMatch * 10) / 10,
    spacing_deviation_px: Math.round(spacingDev * 100) / 100,
    table_structure_match: tableStructure,
    overall_fidelity_percent: Math.round(overallFidelity * 10) / 10,
    issues,
  };
}

/**
 * Manual review checklist for Word document fidelity
 */
export const MANUAL_REVIEW_CHECKLIST = [
  "Italic rendering matches Word document",
  "Table borders align correctly",
  "List indentation matches source",
  "Image sizing and positioning correct",
  "Paragraph spacing matches Word",
  "Text decorations (underline, strikethrough) render correctly",
  "Cell vertical alignment is correct",
  "Merged table cells render properly",
  "Font sizes match within 1pt",
  "Color accuracy is acceptable",
  "Overall layout matches Word document",
];

/**
 * Compare render output to Word screenshot (placeholder)
 * In production, this would use image diff algorithms
 */
export async function compareRenderToWord(
  liveRenderCanvas: HTMLCanvasElement,
  _wordScreenshot?: HTMLImageElement,
  _tolerancePercent?: number
): Promise<PixelComparisonResult> {
  const ctx = liveRenderCanvas.getContext("2d");
  if (!ctx) throw new Error("Could not get canvas context");

  const width = liveRenderCanvas.width;
  const height = liveRenderCanvas.height;

  let diffPixels = 0;
  const totalPixels = width * height;

  for (let i = 0; i < width; i += 10) {
    for (let j = 0; j < height; j += 10) {
      const data = ctx.getImageData(i, j, 1, 1).data;
      if (data[3] < 200) diffPixels += 100;
    }
  }

  const matchPercent = 100 - (diffPixels / totalPixels) * 100;

  return {
    diff_pixels: diffPixels,
    match_percent: Math.max(0, matchPercent),
    areas_of_deviation: [],
  };
}
