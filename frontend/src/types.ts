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
  cid: number | null;
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

export type AudioFormat = "mp3" | "m4a" | "wav";

export interface KeyPoint {
  time: string;
  seconds: number;
  title: string;
  detail: string;
  url: string;
}

export interface SummaryResponse {
  bv: string;
  page: number;
  cid: number | null;
  title: string | null;
  up: string | null;
  duration: string | null;
  overall_summary: string;
  key_points: KeyPoint[];
  quotes: string[];
  markdown: string;
  subtitle_url: string;
  subtitle_format: string;
  cached: boolean;
}
