// Patch 3 verification: tables emit per-column DXA widths proportional to the
// max character length observed in each column (not blind uniform division).
//
// Builds a minimal DocumentLayout with a 3-column table where column-0 holds
// long paragraphs and column-2 holds single tokens. Compiles it via the real
// `compileToDocx` and inspects the emitted <w:gridCol w:w="..."/> values.
//
// Run: `npx tsx scripts/verify-table-widths.mts` from vitegrid-frontend/.

import { execSync } from "node:child_process";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { compileToDocx } from "../src/utils/wordCompiler";
import { DEFAULT_SPACING_TOKENS, DEFAULT_STYLE_TOKENS, type DocumentLayout } from "../src/types";

let failures = 0;
function assert(name: string, cond: boolean, info?: string): void {
  const symbol = cond ? "PASS" : "FAIL";
  console.log(`  ${symbol}  ${name}${info ? ` -- ${info}` : ""}`);
  if (!cond) failures++;
}

function unzip(buf: Buffer, label: string): string {
  const dir = join(tmpdir(), `vg-tbl-${label}-${Date.now()}`);
  mkdirSync(dir, { recursive: true });
  const zip = join(dir, "out.zip");
  writeFileSync(zip, buf);
  execSync(
    `powershell -NoProfile -Command "Expand-Archive -LiteralPath '${zip}' -DestinationPath '${dir}' -Force"`,
  );
  const xml = readFileSync(join(dir, "word", "document.xml"), "utf8");
  rmSync(dir, { recursive: true, force: true });
  return xml;
}

async function main(): Promise<void> {
  console.log("=== Patch 3: proportional column widths (max-char-length ratio) ===");

  // 3 columns: long paragraph / medium / one short token.
  // colMaxChars = [70, 12, 2]; ratios ~ [83%, 14%, 2.4%].
  const layout: DocumentLayout = {
    title: "Patch 3 table",
    page_width_px: 816,
    page_height_px: 1056,
    margin_px: { top: 72, right: 72, bottom: 72, left: 72 },
    blocks: [
      {
        id: "block-0",
        type: "table",
        rows: [
          [
            "A long paragraph that should claim the majority of the table width here.",
            "Medium row",
            "A",
          ],
          ["Another fairly long sentence in column 0.", "Short", "B"],
        ],
        style: { ...DEFAULT_STYLE_TOKENS },
        spacing: { ...DEFAULT_SPACING_TOKENS },
        lock_tier: 1,
      },
    ],
  };

  const blob = await compileToDocx(layout);
  const buf = Buffer.from(await blob.arrayBuffer());
  const xml = unzip(buf, "proportional");

  const gridCols = [...xml.matchAll(/<w:gridCol w:w="(\d+)"\/>/g)].map((m) => parseInt(m[1], 10));
  console.log(`  emitted <w:gridCol> widths (DXA): ${gridCols.join(", ")}`);

  assert("3 grid columns emitted", gridCols.length === 3, `got ${gridCols.length}`);
  if (gridCols.length === 3) {
    assert(
      "col-0 (long paragraph) is widest",
      gridCols[0] > gridCols[1] && gridCols[0] > gridCols[2],
      `widths: ${gridCols}`,
    );
    assert(
      "col-2 (single char) is narrowest",
      gridCols[2] <= gridCols[1] && gridCols[2] <= gridCols[0],
      `widths: ${gridCols}`,
    );
    assert(
      "col-0 width strictly larger than col-1 (proportional, not equal)",
      gridCols[0] > gridCols[1],
      `col-0=${gridCols[0]}, col-1=${gridCols[1]}`,
    );
    const total = gridCols.reduce((a, b) => a + b, 0);
    assert(
      "sum of column widths approximately equals contentWidthDxa (10080)",
      Math.abs(total - 10080) < 50,
      `total ${total}`,
    );
    const minColDxaFloor = 30 * 15;
    assert(
      "all columns at or above 30px floor",
      gridCols.every((w) => w >= minColDxaFloor),
      `widths: ${gridCols}`,
    );
  }

  console.log("\n=== Patch 3: short row gets padded to colCount cells ===");
  const lopsided: DocumentLayout = {
    title: "Patch 3 short row",
    page_width_px: 816,
    page_height_px: 1056,
    margin_px: { top: 72, right: 72, bottom: 72, left: 72 },
    blocks: [
      {
        id: "block-0",
        type: "table",
        rows: [
          ["x", "y", "z"], // 3 cells
          ["only-one"], // 1 cell — short row
        ],
        style: { ...DEFAULT_STYLE_TOKENS },
        spacing: { ...DEFAULT_SPACING_TOKENS },
        lock_tier: 1,
      },
    ],
  };
  const blob2 = await compileToDocx(lopsided);
  const buf2 = Buffer.from(await blob2.arrayBuffer());
  const xml2 = unzip(buf2, "lopsided");
  const tcCount = (xml2.match(/<w:tc>/g) ?? []).length;
  console.log(`  total <w:tc> emitted: ${tcCount}`);
  assert("short row padded to 3 cells (total 6 across both rows)", tcCount === 6, `got ${tcCount}`);

  if (failures) {
    console.log(`\nverify-table-widths: FAIL (${failures})`);
    process.exit(1);
  }
  console.log("\nverify-table-widths: PASS");
}

await main();
