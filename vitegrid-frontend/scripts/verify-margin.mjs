// Phase 0/2 regression check. Generates .docx samples with the compiler's
// emitted XML and asserts the wire-level facts we depend on:
//   * <w:pgMar> uses DXA (twips), not EMU
//   * <w:pgSz> is present and set
//   * Tables emit <w:tblW w:type="dxa">, <w:tblLayout w:type="fixed">,
//     <w:gridCol w:w="..."/> per column, and <w:tcMar> cell margins
//   * Paragraph spacing emits <w:spacing> with explicit lineRule
//   * <w:numbering> registers our custom references
//
// Run: `node scripts/verify-margin.mjs` from vitegrid-frontend/

import {
  AlignmentType,
  BorderStyle,
  Document,
  HeadingLevel,
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
import { execSync } from "node:child_process";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const EMU_PER_PIXEL = 9525;
const pxToEmu = (px) => Math.round(px * EMU_PER_PIXEL);
const pxToDxa = (px) => Math.round(px * 15);

function unzipDocx(buf, label) {
  const dir = join(tmpdir(), `vg-${label}-${Date.now()}`);
  mkdirSync(dir, { recursive: true });
  const zip = join(dir, "out.zip");
  writeFileSync(zip, buf);
  execSync(
    `powershell -NoProfile -Command "Expand-Archive -LiteralPath '${zip}' -DestinationPath '${dir}' -Force"`,
  );
  const doc = readFileSync(join(dir, "word", "document.xml"), "utf8");
  let numbering = "";
  try {
    numbering = readFileSync(join(dir, "word", "numbering.xml"), "utf8");
  } catch {
    numbering = "";
  }
  rmSync(dir, { recursive: true, force: true });
  return { doc, numbering };
}

function assert(name, cond, info) {
  const symbol = cond ? "PASS" : "FAIL";
  console.log(`  ${symbol}  ${name}${info ? ` -- ${info}` : ""}`);
  if (!cond) process.exitCode = 1;
}

async function caseMarginEmu() {
  console.log("\n=== margin-emu-bug (regression sample of OLD behavior) ===");
  const doc = new Document({
    sections: [
      {
        properties: {
          page: { margin: { top: pxToEmu(72), right: pxToEmu(72), bottom: pxToEmu(72), left: pxToEmu(72) } },
        },
        children: [new Paragraph({ text: "old-margin" })],
      },
    ],
  });
  const buf = await Packer.toBuffer(doc);
  const { doc: xml } = unzipDocx(buf, "old-emu");
  const m = xml.match(/<w:pgMar[^/]*\/>/)?.[0] ?? "";
  console.log(`  emitted: ${m}`);
  assert(
    "OLD code's EMU value lands directly in <w:pgMar> twips (proof of the bug)",
    m.includes('w:top="685800"'),
    "685800 twips = 476 inches; Word silently clamps",
  );
}

async function caseFixedCompilerOutput() {
  console.log("\n=== fixed-compiler (page DXA + tables DXA + numbering + spacing) ===");
  const pageWidthDxa = pxToDxa(816);
  const pageHeightDxa = pxToDxa(1056);
  const marginDxa = pxToDxa(72);
  const contentWidthDxa = pageWidthDxa - marginDxa * 2;
  const colCount = 3;
  const colWidthDxa = Math.floor(contentWidthDxa / colCount);

  const border = { style: BorderStyle.SINGLE, size: 4, color: "000000" };
  const cellMargins = { top: 120, bottom: 120, left: 180, right: 180, marginUnitType: WidthType.DXA };

  const table = new Table({
    width: { size: contentWidthDxa, type: WidthType.DXA },
    columnWidths: Array(colCount).fill(colWidthDxa),
    layout: TableLayoutType.FIXED,
    rows: [
      new TableRow({
        children: ["a", "b", "c"].map(
          (t) =>
            new TableCell({
              width: { size: colWidthDxa, type: WidthType.DXA },
              margins: cellMargins,
              borders: { top: border, bottom: border, left: border, right: border },
              children: [new Paragraph({ children: [new TextRun(t)] })],
            }),
        ),
      }),
    ],
  });

  const doc = new Document({
    numbering: {
      config: [
        {
          reference: "vg-bullet",
          levels: [
            {
              level: 0,
              format: LevelFormat.BULLET,
              text: "•",
              alignment: AlignmentType.LEFT,
              style: { paragraph: { indent: { left: 360, hanging: 260 } } },
            },
          ],
        },
        {
          reference: "vg-decimal",
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
      ],
    },
    sections: [
      {
        properties: {
          page: {
            size: { width: pageWidthDxa, height: pageHeightDxa },
            margin: { top: marginDxa, right: marginDxa, bottom: marginDxa, left: marginDxa },
          },
        },
        children: [
          new Paragraph({
            heading: HeadingLevel.HEADING_1,
            spacing: { before: 240, after: 120, line: 360, lineRule: LineRuleType.EXACT },
            children: [new TextRun({ text: "Title", bold: true })],
          }),
          new Paragraph({
            spacing: { before: 60, after: 60, line: 276, lineRule: LineRuleType.EXACT },
            children: [new TextRun("Body paragraph.")],
          }),
          new Paragraph({
            numbering: { reference: "vg-bullet", level: 0 },
            children: [new TextRun("bullet item")],
          }),
          new Paragraph({
            numbering: { reference: "vg-decimal", level: 0 },
            children: [new TextRun("decimal item")],
          }),
          table,
        ],
      },
    ],
  });
  const buf = await Packer.toBuffer(doc);
  const { doc: xml, numbering: numXml } = unzipDocx(buf, "fixed");

  // Page geometry
  const pgMar = xml.match(/<w:pgMar[^/]*\/>/)?.[0] ?? "";
  const pgSz = xml.match(/<w:pgSz[^/]*\/>/)?.[0] ?? "";
  console.log(`  <w:pgMar>: ${pgMar}`);
  console.log(`  <w:pgSz>:  ${pgSz}`);
  assert("pgMar uses DXA (1080 twips = 0.75in)", pgMar.includes('w:top="1080"'));
  assert("pgSz width is page DXA (12240)", pgSz.includes('w:w="12240"'));
  assert("pgSz height is page DXA (15840)", pgSz.includes('w:h="15840"'));

  // Table absolutes
  const tblW = xml.match(/<w:tblW[^/]*\/>/)?.[0] ?? "";
  const tblLayout = xml.match(/<w:tblLayout[^/]*\/>/)?.[0] ?? "";
  const gridCols = [...xml.matchAll(/<w:gridCol[^/]*\/>/g)].map((m) => m[0]);
  const tcMar = xml.match(/<w:tcMar>[\s\S]*?<\/w:tcMar>/)?.[0] ?? "";
  console.log(`  <w:tblW>:     ${tblW}`);
  console.log(`  <w:tblLayout>: ${tblLayout}`);
  console.log(`  <w:gridCol>s: ${gridCols.length} (${gridCols.join(", ")})`);
  console.log(`  <w:tcMar>:    ${tcMar.replace(/\s+/g, " ").slice(0, 200)}`);
  assert("tblW is DXA, not pct", tblW.includes('w:type="dxa"'));
  assert("tblLayout is fixed", tblLayout.includes('w:type="fixed"'));
  assert("gridCol count matches columns", gridCols.length === colCount);
  assert("tcMar present", tcMar.length > 0);

  // Spacing
  const spacingMatches = [...xml.matchAll(/<w:spacing[^/]*\/>/g)].map((m) => m[0]);
  console.log(`  <w:spacing> count: ${spacingMatches.length}`);
  assert(
    "at least one <w:spacing> has lineRule=exact",
    spacingMatches.some((s) => s.includes('w:lineRule="exact"')),
  );

  // Numbering
  console.log(`  numbering.xml present: ${numXml.length > 0 ? "yes" : "no"}`);
  assert("numbering.xml exists", numXml.length > 0);
  assert("custom decimal level emitted", numXml.includes('w:val="decimal"'));
  assert("custom bullet level emitted", numXml.includes('w:val="bullet"'));
}

await caseMarginEmu();
await caseFixedCompilerOutput();

if (process.exitCode) {
  console.log("\nverify-margin: FAIL");
} else {
  console.log("\nverify-margin: PASS");
}
