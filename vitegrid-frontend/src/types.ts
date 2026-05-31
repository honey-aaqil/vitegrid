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
  italic?: boolean;
  underline?: "none" | "single" | "double";
  underline_color_rgba?: string | null;
  strikethrough?: boolean;
  color_hex: string;
  background_hex: string | null;
  align: Align;
  line_height_px?: number | null;
  letter_spacing_px?: number;
  word_spacing_px?: number;
  border_visible: boolean;
  cell_padding_dxa: CellPaddingDxa;
  list_format: ListFormat;
  list_level: number;
  list_level_indent_px?: number;
  list_hanging_indent_px?: number;
}

export interface SpacingTokens {
  before_dxa: number;
  after_dxa: number;
  before_px?: number;
  after_px?: number;
  line_spacing_dxa: number;
  line_height_px?: number | null;
  line_rule: LineRule;
}

export interface TableCell {
  text: string;
  padding_top_px?: number;
  padding_bottom_px?: number;
  padding_left_px?: number;
  padding_right_px?: number;
  vertical_align?: "top" | "center" | "bottom";
  row_span?: number;
  col_span?: number;
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
  table_cells?: TableCell[][];
  image_ref?: string;
  image_width_px?: number;
  image_height_px?: number;
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
  italic: false,
  underline: "none",
  strikethrough: false,
  color_hex: "000000",
  background_hex: "FFFFFF",
  align: "left",
  line_height_px: undefined,
  letter_spacing_px: 0,
  word_spacing_px: 0,
  border_visible: true,
  cell_padding_dxa: { top: 120, bottom: 120, left: 180, right: 180 },
  list_format: "bullet",
  list_level: 0,
  list_level_indent_px: 0,
  list_hanging_indent_px: 0,
};

export const DEFAULT_SPACING_TOKENS: SpacingTokens = {
  before_dxa: 0,
  after_dxa: 0,
  before_px: 0,
  after_px: 0,
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
