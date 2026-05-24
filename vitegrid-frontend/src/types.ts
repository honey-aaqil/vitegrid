export type BlockType =
  | "heading"
  | "paragraph"
  | "list"
  | "table"
  | "image_placeholder";

export type LockTier = 1 | 2 | 3;

export type Align = "left" | "center" | "right" | "justify";

export interface StyleTokens {
  font_family?: string;
  font_size_pt?: number;
  font_weight?: "normal" | "bold";
  color_hex?: string;
  background_hex?: string;
  align?: Align;
  border_visible?: boolean;
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
