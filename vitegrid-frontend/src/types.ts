export type BlockType =
  | "heading"
  | "paragraph"
  | "list"
  | "table"
  | "image_placeholder";

export type LockTier = 1 | 2 | 3;

export type Align = "left" | "center" | "right" | "justify";

export type ListFormat = "bullet" | "decimal" | "lowerLetter" | "upperRoman";

export type LineRule = "auto" | "exact" | "atLeast";

export type FontWeight = "normal" | "bold";

export interface CellPaddingDxa {
  top: number;
  bottom: number;
  left: number;
  right: number;
}

export interface StyleTokens {
  font_family: string;
  font_size_pt: number;
  font_weight: FontWeight;
  color_hex: string;
  background_hex: string | null;
  align: Align;
  border_visible: boolean;
  cell_padding_dxa: CellPaddingDxa;
  list_format: ListFormat;
  list_level: number;
}

export interface SpacingTokens {
  before_dxa: number;
  after_dxa: number;
  line_spacing_dxa: number;
  line_rule: LineRule;
}

export interface BoundingBox {
  x_px: number;
  y_px: number;
  width_px: number;
  height_px: number;
}

export interface DocumentBlock {
  id: string;
  type: BlockType;
  text?: string;
  items?: string[];
  rows?: string[][];
  image_ref?: string;
  bbox?: BoundingBox;
  style: StyleTokens;
  spacing: SpacingTokens;
  lock_tier: LockTier;
}

export interface PageMargin {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

export interface DocumentLayout {
  title: string;
  page_width_px: number;
  page_height_px: number;
  margin_px: PageMargin;
  blocks: DocumentBlock[];
  _source_file?: string;
}

export const DEFAULT_STYLE_TOKENS: StyleTokens = {
  font_family: "Arial",
  font_size_pt: 11.0,
  font_weight: "normal",
  color_hex: "000000",
  background_hex: "FFFFFF",
  align: "left",
  border_visible: true,
  cell_padding_dxa: { top: 120, bottom: 120, left: 180, right: 180 },
  list_format: "bullet",
  list_level: 0,
};

export const DEFAULT_SPACING_TOKENS: SpacingTokens = {
  before_dxa: 0,
  after_dxa: 0,
  line_spacing_dxa: 240,
  line_rule: "auto",
};

export interface AuditReport {
  approved: boolean;
  missing_text: string[];
  layout_issues: string[];
  patch_instructions: string | null;
}

export interface GenerateResponse {
  layout: DocumentLayout;
  audit: AuditReport;
}

export interface TemplateSummary {
  id: number;
  name: string;
  source_type: "imported" | "generated";
  lock_tier: LockTier;
  thumbnail_path: string | null;
  created_at: string;
  updated_at: string;
}
