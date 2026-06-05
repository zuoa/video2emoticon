export interface VideoInfo {
  id: string;
  source_type: "upload" | "bilibili";
  filename: string;
  duration: number;
  width: number;
  height: number;
  preview_url: string;
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
  color: string;
  stroke_color: string;
  box: boolean;
  box_color: string;
  box_opacity: number;
}

export interface ExportResponse {
  filename: string;
  download_url: string;
  size_bytes: number;
}
