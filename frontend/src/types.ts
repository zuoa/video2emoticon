export interface VideoInfo {
  id: string;
  source_type: "upload" | "bilibili";
  filename: string;
  duration: number;
  width: number;
  height: number;
  preview_url: string;
}

export interface BilibiliPageInfo {
  page: number;
  title: string;
  duration: number | null;
}

export interface BilibiliPagesResponse {
  bv: string;
  selected_page: number;
  pages: BilibiliPageInfo[];
}

export interface CropRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface TextLayer {
  enabled: boolean;
  content: string;
  position: "top" | "center" | "bottom";
  font_size: number;
  font_id: string | null;
  color: string;
  stroke_color: string;
  box: boolean;
  box_color: string;
  box_opacity: number;
}

export interface FontInfo {
  id: string;
  name: string;
  family: string;
  filename: string;
  url: string;
}

export interface ExportResponse {
  filename: string;
  download_url: string;
  size_bytes: number;
}
