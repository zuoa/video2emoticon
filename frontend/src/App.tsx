import {
  Clock3,
  Download,
  Film,
  FolderUp,
  Loader2,
  MousePointer2,
  Play,
  RefreshCw,
  Repeat,
  Scissors,
  SkipBack,
  Type,
  Upload,
  Video
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type {
  BilibiliPageInfo,
  BilibiliPagesResponse,
  CropRect,
  ExportResponse,
  FontInfo,
  TextLayer,
  VideoInfo
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

const defaultText: TextLayer = {
  enabled: false,
  content: "",
  position: "bottom",
  font_size: 32,
  font_id: null,
  color: "#ffffff",
  stroke_color: "#111111",
  box: true,
  box_color: "#000000",
  box_opacity: 0.45
};

const MIN_CLIP_DURATION = 0.1;
const MIN_SPEED_LEVEL = -16;
const MAX_SPEED_LEVEL = 16;

type DragMode = "create" | "move" | "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

interface ClipRange {
  start: number;
  end: number;
  duration: number;
}

interface DragState {
  mode: DragMode;
  pointerId: number;
  startPoint: { x: number; y: number };
  initialCrop: CropRect;
}

interface VideoFrame {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface ParsedBilibiliInput {
  bv: string;
  page: number | null;
}

function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function hexToRgba(hex: string, opacity: number): string {
  const normalized = hex.trim().replace("#", "");
  const fullHex =
    normalized.length === 3
      ? normalized
          .split("")
          .map((char) => `${char}${char}`)
          .join("")
      : normalized;
  const value = Number.parseInt(fullHex, 16);
  if (Number.isNaN(value)) {
    return `rgba(0, 0, 0, ${clamp(opacity, 0, 1)})`;
  }
  const red = (value >> 16) & 255;
  const green = (value >> 8) & 255;
  const blue = value & 255;
  return `rgba(${red}, ${green}, ${blue}, ${clamp(opacity, 0, 1)})`;
}

function roundTime(value: number): number {
  return Math.round(value * 100) / 100;
}

function formatTimeInput(value: number): string {
  const centiseconds = Math.round(Math.max(0, value) * 100);
  const totalSeconds = Math.floor(centiseconds / 100);
  const decimal = centiseconds % 100;
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  let secondsText = String(seconds).padStart(2, "0");

  if (decimal > 0) {
    secondsText += `.${String(decimal).padStart(2, "0").replace(/0$/, "")}`;
  }

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${secondsText}`;
  }
  return `${minutes}:${secondsText}`;
}

function formatSecondsInput(value: number): string {
  return String(roundTime(Math.max(MIN_CLIP_DURATION, value)));
}

function parseTimeInput(value: string): number | null {
  const normalized = value.trim().replace(/：/g, ":");
  if (!normalized) {
    return null;
  }

  const parts = normalized.split(":").map((part) => part.trim());
  const numberPattern = /^\d+(?:\.\d+)?$/;
  if (parts.length > 3 || parts.some((part) => part === "")) {
    return null;
  }

  if (parts.length === 1) {
    return numberPattern.test(parts[0]) ? Number(parts[0]) : null;
  }

  if (!parts.slice(0, -1).every((part) => /^\d+$/.test(part)) || !numberPattern.test(parts[parts.length - 1])) {
    return null;
  }

  const values = parts.map(Number);
  const seconds = values[values.length - 1];
  if (seconds >= 60) {
    return null;
  }

  if (parts.length === 2) {
    return values[0] * 60 + seconds;
  }

  const minutes = values[1];
  if (minutes >= 60) {
    return null;
  }
  return values[0] * 3600 + minutes * 60 + seconds;
}

function parseOutputWidthInput(value: string): number | null | false {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  if (!/^\d+$/.test(trimmed)) {
    return false;
  }
  const width = Number(trimmed);
  return width > 0 ? width : false;
}

function parsePositivePage(value: string | null): number | null {
  if (value === null || !/^\d+$/.test(value)) {
    return null;
  }
  const page = Number(value);
  return page >= 1 ? page : null;
}

function isBilibiliHost(hostname: string): boolean {
  const host = hostname.toLowerCase();
  return host === "bilibili.com" || host.endsWith(".bilibili.com");
}

function parseBilibiliInput(value: string): ParsedBilibiliInput | null {
  const text = value.trim();
  if (!text) {
    return null;
  }

  if (/^BV[0-9A-Za-z]{10}$/.test(text)) {
    return { bv: text, page: null };
  }

  const urlText =
    !text.includes("://") && /^(?:[a-z0-9-]+\.)*bilibili\.com\//i.test(text) ? `https://${text}` : text;
  let url: URL;
  try {
    url = new URL(urlText);
  } catch {
    return null;
  }

  if (!["http:", "https:"].includes(url.protocol) || !isBilibiliHost(url.hostname)) {
    return null;
  }

  const match = url.pathname.match(/\/video\/(BV[0-9A-Za-z]{10})(?:\/|$)/);
  if (!match) {
    return null;
  }
  const rawPage = url.searchParams.get("p");
  const page = parsePositivePage(rawPage);
  if (rawPage !== null && page === null) {
    return null;
  }

  return {
    bv: match[1],
    page
  };
}

function formatBilibiliPageOption(page: BilibiliPageInfo): string {
  const duration = page.duration ? ` · ${formatTimeInput(page.duration)}` : "";
  return `P${page.page} · ${page.title}${duration}`;
}

function speedFactorFromLevel(level: number): number {
  if (level === 0 || Math.abs(level) === 1) {
    return 1;
  }
  return level > 0 ? level : 1 / Math.abs(level);
}

function normalizeStartTime(value: number, duration: number | undefined): number {
  if (duration === undefined) {
    return roundTime(Math.max(0, value));
  }
  const maxStart = Math.max(0, duration - Math.min(MIN_CLIP_DURATION, duration));
  return roundTime(clamp(value, 0, maxStart));
}

function normalizeClipDuration(value: number, start: number, duration: number | undefined): number {
  if (duration === undefined) {
    return roundTime(Math.max(MIN_CLIP_DURATION, value));
  }
  const availableDuration = Math.max(MIN_CLIP_DURATION, duration - start);
  return roundTime(clamp(value, MIN_CLIP_DURATION, availableDuration));
}

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) {
    return `${Math.round(bytes / 1024)} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

async function readJson<T>(response: Response): Promise<T> {
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(payload?.detail ?? "请求失败");
  }
  return payload as T;
}

function triggerDownload(response: ExportResponse): void {
  const anchor = document.createElement("a");
  anchor.href = apiUrl(response.download_url);
  anchor.download = response.filename;
  anchor.style.display = "none";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
}

export function App() {
  const [videoInfo, setVideoInfo] = useState<VideoInfo | null>(null);
  const [crop, setCrop] = useState<CropRect | null>(null);
  const [startTime, setStartTime] = useState(0);
  const [clipDuration, setClipDuration] = useState(3);
  const [startTimeInput, setStartTimeInput] = useState(formatTimeInput(0));
  const [clipDurationInput, setClipDurationInput] = useState(formatSecondsInput(3));
  const [currentTime, setCurrentTime] = useState(0);
  const [fps, setFps] = useState(12);
  const [outputWidthInput, setOutputWidthInput] = useState("");
  const [speedLevel, setSpeedLevel] = useState(0);
  const [loop, setLoop] = useState(true);
  const [text, setText] = useState<TextLayer>(defaultText);
  const [bv, setBv] = useState("");
  const [bilibiliPages, setBilibiliPages] = useState<BilibiliPagesResponse | null>(null);
  const [bilibiliPage, setBilibiliPage] = useState(1);
  const [bilibiliStatus, setBilibiliStatus] = useState("");
  const [busy, setBusy] = useState<"upload" | "pages" | "download" | "export" | null>(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ExportResponse | null>(null);
  const [videoFrame, setVideoFrame] = useState<VideoFrame>({ left: 0, top: 0, width: 0, height: 0 });
  const [fonts, setFonts] = useState<FontInfo[]>([]);
  const [fontBusy, setFontBusy] = useState<"load" | "upload" | null>(null);
  const [fontError, setFontError] = useState("");

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const fontInputRef = useRef<HTMLInputElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const clipPreviewRef = useRef<ClipRange | null>(null);

  const parsedStartInput = useMemo(() => parseTimeInput(startTimeInput), [startTimeInput]);
  const parsedClipDurationInput = useMemo(() => parseTimeInput(clipDurationInput), [clipDurationInput]);
  const normalizedStartInput =
    parsedStartInput === null ? null : normalizeStartTime(parsedStartInput, videoInfo?.duration);
  const normalizedClipDurationInput =
    parsedClipDurationInput === null || normalizedStartInput === null
      ? null
      : normalizeClipDuration(parsedClipDurationInput, normalizedStartInput, videoInfo?.duration);
  const parsedOutputWidth = useMemo(() => parseOutputWidthInput(outputWidthInput), [outputWidthInput]);
  const outputWidthIsValid = parsedOutputWidth !== false;
  const parsedBilibiliInput = useMemo(() => parseBilibiliInput(bv), [bv]);
  const inputBv = parsedBilibiliInput?.bv ?? null;
  const availableBilibiliPages = useMemo(
    () => (inputBv && bilibiliPages?.bv === inputBv ? bilibiliPages.pages : []),
    [bilibiliPages, inputBv]
  );
  const canExport = Boolean(
    videoInfo &&
      crop &&
      normalizedStartInput !== null &&
      normalizedClipDurationInput !== null &&
      outputWidthIsValid &&
      !busy
  );

  const loadFonts = useCallback(async () => {
    setFontBusy("load");
    setFontError("");
    try {
      const nextFonts = await fetch(apiUrl("/api/fonts")).then(readJson<FontInfo[]>);
      setFonts(nextFonts);
    } catch (err) {
      setFontError(err instanceof Error ? err.message : "字体列表读取失败");
    } finally {
      setFontBusy(null);
    }
  }, []);

  useEffect(() => {
    void loadFonts();
  }, [loadFonts]);

  useEffect(() => {
    if (!videoInfo) {
      return;
    }
    const nextStartTime = 0;
    const nextClipDuration = Math.min(3, Math.max(MIN_CLIP_DURATION, videoInfo.duration));
    setStartTime(nextStartTime);
    setClipDuration(nextClipDuration);
    setStartTimeInput(formatTimeInput(nextStartTime));
    setClipDurationInput(formatSecondsInput(nextClipDuration));
    setCurrentTime(0);
    clipPreviewRef.current = null;
    setCrop({
      x: Math.round(videoInfo.width * 0.15),
      y: Math.round(videoInfo.height * 0.15),
      width: Math.round(videoInfo.width * 0.7),
      height: Math.round(videoInfo.height * 0.7)
    });
    setResult(null);
  }, [videoInfo]);

  useEffect(() => {
    const element = videoRef.current;
    const stage = stageRef.current;
    if (!element || !stage) {
      return;
    }
    const update = () => {
      const stageRect = stage.getBoundingClientRect();
      const videoRect = element.getBoundingClientRect();
      const nextFrame = {
        left: videoRect.left - stageRect.left - stage.clientLeft,
        top: videoRect.top - stageRect.top - stage.clientTop,
        width: videoRect.width,
        height: videoRect.height
      };

      setVideoFrame((current) => {
        if (
          current.left === nextFrame.left &&
          current.top === nextFrame.top &&
          current.width === nextFrame.width &&
          current.height === nextFrame.height
        ) {
          return current;
        }
        return nextFrame;
      });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    observer.observe(stage);
    window.addEventListener("resize", update);
    element.addEventListener("loadedmetadata", update);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", update);
      element.removeEventListener("loadedmetadata", update);
    };
  }, [videoInfo]);

  const cropLayerStyle = useMemo(() => {
    if (!videoInfo || videoFrame.width === 0 || videoFrame.height === 0) {
      return null;
    }
    return {
      left: videoFrame.left,
      top: videoFrame.top,
      width: videoFrame.width,
      height: videoFrame.height
    };
  }, [videoFrame.height, videoFrame.left, videoFrame.top, videoFrame.width, videoInfo]);

  const cropStyle = useMemo(() => {
    if (!videoInfo || !crop || videoFrame.width === 0 || videoFrame.height === 0) {
      return null;
    }
    return {
      left: (crop.x / videoInfo.width) * videoFrame.width,
      top: (crop.y / videoInfo.height) * videoFrame.height,
      width: (crop.width / videoInfo.width) * videoFrame.width,
      height: (crop.height / videoInfo.height) * videoFrame.height
    };
  }, [crop, videoFrame.height, videoFrame.width, videoInfo]);

  const selectedLabel = useMemo(() => {
    if (!crop) {
      return "未选择";
    }
    return `${crop.width} x ${crop.height} @ ${crop.x}, ${crop.y}`;
  }, [crop]);

  const selectedFont = useMemo(
    () => fonts.find((font) => font.id === text.font_id) ?? null,
    [fonts, text.font_id]
  );

  const currentTimeLabel = useMemo(() => formatTimeInput(currentTime), [currentTime]);
  const durationLabel = useMemo(() => formatTimeInput(videoInfo?.duration ?? 0), [videoInfo?.duration]);
  const clipDurationLabel = useMemo(() => {
    return formatTimeInput(normalizedClipDurationInput ?? clipDuration);
  }, [clipDuration, normalizedClipDurationInput]);
  const effectiveOutputWidth = useMemo(() => {
    if (parsedOutputWidth !== null && parsedOutputWidth !== false) {
      return parsedOutputWidth;
    }
    return crop?.width ?? 0;
  }, [crop?.width, parsedOutputWidth]);
  const outputSizeLabel = useMemo(() => {
    if (!crop || effectiveOutputWidth <= 0) {
      return "未选择";
    }
    const outputHeight = Math.max(1, Math.round((effectiveOutputWidth / crop.width) * crop.height));
    return `${effectiveOutputWidth} x ${outputHeight}`;
  }, [crop, effectiveOutputWidth]);
  const speedFactor = useMemo(() => speedFactorFromLevel(speedLevel), [speedLevel]);
  const speedLabel = useMemo(() => {
    if (speedLevel === 0 || Math.abs(speedLevel) === 1) {
      return "1x 原速";
    }
    if (speedLevel > 0) {
      return `${speedLevel}x 加速`;
    }
    return `${speedLevel}x 减速 · 实际 1/${Math.abs(speedLevel)}x`;
  }, [speedLevel]);

  const videoTextStyle = useMemo<CSSProperties | undefined>(() => {
    if (!cropStyle || effectiveOutputWidth <= 0) {
      return undefined;
    }
    const previewScale = cropStyle.width / effectiveOutputWidth;
    const fontSize = clamp(text.font_size * previewScale, 11, 96);
    const strokeWidth = Math.max(1, Math.round(fontSize / 16));
    return {
      backgroundColor: text.box ? hexToRgba(text.box_color, text.box_opacity) : "transparent",
      color: text.color,
      fontFamily: selectedFont ? `"${selectedFont.family}", sans-serif` : undefined,
      fontSize,
      textShadow: `0 1px 1px ${text.stroke_color}, 1px 0 1px ${text.stroke_color}, 0 -1px 1px ${text.stroke_color}, -1px 0 1px ${text.stroke_color}`,
      WebkitTextStroke: `${strokeWidth}px ${text.stroke_color}`
    };
  }, [cropStyle, effectiveOutputWidth, selectedFont, text]);

  const fontFaceStyles = useMemo(
    () =>
      fonts
        .map((font) => `@font-face{font-family:"${font.family}";src:url("${apiUrl(font.url)}");font-display:swap;}`)
        .join("\n"),
    [fonts]
  );

  const previewText = text.content.trim() || "预览文字 Aa 你好";

  const getPoint = useCallback(
    (event: React.PointerEvent) => {
      if (!stageRef.current || !videoInfo || videoFrame.width === 0 || videoFrame.height === 0) {
        return { x: 0, y: 0 };
      }
      const rect = stageRef.current.getBoundingClientRect();
      const contentLeft = rect.left + stageRef.current.clientLeft;
      const contentTop = rect.top + stageRef.current.clientTop;
      const x = clamp(
        ((event.clientX - contentLeft - videoFrame.left) / videoFrame.width) * videoInfo.width,
        0,
        videoInfo.width
      );
      const y = clamp(
        ((event.clientY - contentTop - videoFrame.top) / videoFrame.height) * videoInfo.height,
        0,
        videoInfo.height
      );
      return { x, y };
    },
    [videoFrame.height, videoFrame.left, videoFrame.top, videoFrame.width, videoInfo]
  );

  const commitCrop = useCallback(
    (nextCrop: CropRect) => {
      if (!videoInfo) {
        return;
      }
      const width = clamp(Math.round(nextCrop.width), 4, videoInfo.width);
      const height = clamp(Math.round(nextCrop.height), 4, videoInfo.height);
      const x = clamp(Math.round(nextCrop.x), 0, videoInfo.width - width);
      const y = clamp(Math.round(nextCrop.y), 0, videoInfo.height - height);
      setCrop({ x, y, width, height });
    },
    [videoInfo]
  );

  const startDrag = useCallback(
    (event: React.PointerEvent, mode: DragMode) => {
      if (!videoInfo) {
        return;
      }
      const point = getPoint(event);
      const initialCrop = crop ?? { x: point.x, y: point.y, width: 4, height: 4 };
      dragRef.current = {
        mode,
        pointerId: event.pointerId,
        startPoint: point,
        initialCrop
      };
      stageRef.current?.setPointerCapture(event.pointerId);
      event.stopPropagation();
    },
    [crop, getPoint, videoInfo]
  );

  const handleStagePointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (!videoInfo) {
        return;
      }
      if (event.target instanceof HTMLVideoElement) {
        return;
      }
      const point = getPoint(event);
      const initialCrop = { x: point.x, y: point.y, width: 4, height: 4 };
      dragRef.current = {
        mode: "create",
        pointerId: event.pointerId,
        startPoint: point,
        initialCrop
      };
      commitCrop(initialCrop);
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    [commitCrop, getPoint, videoInfo]
  );

  const handlePointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (!drag || !videoInfo || drag.pointerId !== event.pointerId) {
        return;
      }
      const point = getPoint(event);
      const dx = point.x - drag.startPoint.x;
      const dy = point.y - drag.startPoint.y;
      const initial = drag.initialCrop;
      let next = { ...initial };

      if (drag.mode === "create") {
        next = {
          x: Math.min(drag.startPoint.x, point.x),
          y: Math.min(drag.startPoint.y, point.y),
          width: Math.abs(dx),
          height: Math.abs(dy)
        };
      }
      if (drag.mode === "move") {
        next.x = clamp(initial.x + dx, 0, videoInfo.width - initial.width);
        next.y = clamp(initial.y + dy, 0, videoInfo.height - initial.height);
      }
      if (drag.mode.includes("e")) {
        next.width = clamp(initial.width + dx, 4, videoInfo.width - initial.x);
      }
      if (drag.mode.includes("s")) {
        next.height = clamp(initial.height + dy, 4, videoInfo.height - initial.y);
      }
      if (drag.mode.includes("w")) {
        const x = clamp(initial.x + dx, 0, initial.x + initial.width - 4);
        next.width = initial.width + initial.x - x;
        next.x = x;
      }
      if (drag.mode.includes("n")) {
        const y = clamp(initial.y + dy, 0, initial.y + initial.height - 4);
        next.height = initial.height + initial.y - y;
        next.y = y;
      }

      commitCrop(next);
    },
    [commitCrop, getPoint, videoInfo]
  );

  const handlePointerUp = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId === event.pointerId) {
      dragRef.current = null;
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }, []);

  const updateBvInput = (value: string) => {
    const parsedInput = parseBilibiliInput(value);
    setBv(value);
    setBilibiliPages(null);
    setBilibiliPage(parsedInput?.page ?? 1);
    setBilibiliStatus(
      parsedInput && value.trim() !== parsedInput.bv
        ? `已识别 ${parsedInput.bv}${parsedInput.page ? ` P${parsedInput.page}` : ""}`
        : ""
    );
  };

  const loadUploadedFile = async (file: File) => {
    setBusy("upload");
    setError("");
    setBilibiliStatus("");
    const form = new FormData();
    form.append("file", file);
    try {
      const info = await fetch(apiUrl("/api/videos/upload"), {
        method: "POST",
        body: form
      }).then(readJson<VideoInfo>);
      setVideoInfo(info);
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setBusy(null);
    }
  };

  const fetchBilibiliPages = async (
    parsedInput: ParsedBilibiliInput
  ): Promise<BilibiliPagesResponse | null> => {
    if (!parsedInput.bv) {
      setError("请输入 BV 号或合法的 Bilibili 视频地址");
      return null;
    }
    setBusy("pages");
    setError("");
    setBilibiliStatus("");
    try {
      const response = await fetch(apiUrl("/api/videos/bilibili/pages"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bv: parsedInput.bv, page: parsedInput.page })
      }).then(readJson<BilibiliPagesResponse>);
      setBv(response.bv);
      setBilibiliPages(response);
      const nextPage =
        response.pages.some((page) => page.page === response.selected_page)
          ? response.selected_page
          : response.pages[0]?.page ?? 1;
      setBilibiliPage(nextPage);
      setBilibiliStatus(
        response.pages.length > 1
          ? `已识别 ${response.bv} 的 ${response.pages.length} 个分 P`
          : `已识别 ${response.bv} P${nextPage}`
      );
      return response;
    } catch (err) {
      setError(err instanceof Error ? err.message : "分 P 读取失败");
      return null;
    } finally {
      setBusy(null);
    }
  };

  const downloadBilibiliPage = async (parsedInput: ParsedBilibiliInput, page: number) => {
    setBusy("download");
    setError("");
    setBilibiliStatus("");
    try {
      const info = await fetch(apiUrl("/api/videos/bilibili"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bv: parsedInput.bv, page })
      }).then(readJson<VideoInfo>);
      setBv(parsedInput.bv);
      setVideoInfo(info);
    } catch (err) {
      setError(err instanceof Error ? err.message : "下载失败");
    } finally {
      setBusy(null);
    }
  };

  const loadBilibili = async () => {
    if (!bv.trim()) {
      setError("请输入 BV 号或 Bilibili 视频地址");
      return;
    }
    const parsedInput = parseBilibiliInput(bv);
    if (!parsedInput) {
      setError("请输入 BV 号或合法的 bilibili.com/video/BV... 地址");
      return;
    }

    if (availableBilibiliPages.length === 0) {
      const response = await fetchBilibiliPages(parsedInput);
      if (!response) {
        return;
      }
      if (response.pages.length > 1 && parsedInput.page === null) {
        return;
      }
      await downloadBilibiliPage({ bv: response.bv, page: response.selected_page }, response.selected_page);
      return;
    }

    const selectedPage = availableBilibiliPages.some((page) => page.page === bilibiliPage)
      ? bilibiliPage
      : availableBilibiliPages[0]?.page ?? 1;
    await downloadBilibiliPage(parsedInput, selectedPage);
  };

  const uploadFonts = async (fileList: FileList | null) => {
    const files = Array.from(fileList ?? []);
    if (files.length === 0) {
      return;
    }
    setFontBusy("upload");
    setFontError("");
    const previousIds = new Set(fonts.map((font) => font.id));
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    try {
      const nextFonts = await fetch(apiUrl("/api/fonts/upload"), {
        method: "POST",
        body: form
      }).then(readJson<FontInfo[]>);
      setFonts(nextFonts);
      const addedFont = nextFonts.find((font) => !previousIds.has(font.id));
      if (addedFont) {
        setText((current) => ({ ...current, font_id: addedFont.id }));
      }
    } catch (err) {
      setFontError(err instanceof Error ? err.message : "字体上传失败");
    } finally {
      setFontBusy(null);
      if (fontInputRef.current) {
        fontInputRef.current.value = "";
      }
    }
  };

  const commitClipInputs = useCallback((): ClipRange | null => {
    const nextStartRaw = parseTimeInput(startTimeInput);
    const nextDurationRaw = parseTimeInput(clipDurationInput);
    if (nextStartRaw === null || nextDurationRaw === null) {
      setError("开始时间或持续秒数格式不正确");
      return null;
    }

    const nextStart = normalizeStartTime(nextStartRaw, videoInfo?.duration);
    const nextDuration = normalizeClipDuration(nextDurationRaw, nextStart, videoInfo?.duration);
    const nextEnd = roundTime(nextStart + nextDuration);
    setStartTime(nextStart);
    setClipDuration(nextDuration);
    setStartTimeInput(formatTimeInput(nextStart));
    setClipDurationInput(formatSecondsInput(nextDuration));

    setError("");
    return { start: nextStart, end: nextEnd, duration: nextDuration };
  }, [clipDurationInput, startTimeInput, videoInfo?.duration]);

  const setClipStartFromCurrent = () => {
    if (!videoInfo) {
      return;
    }
    const rawTime = videoRef.current?.currentTime ?? currentTime;
    const nextStart = normalizeStartTime(rawTime, videoInfo.duration);
    const nextDuration = normalizeClipDuration(clipDuration, nextStart, videoInfo.duration);
    setStartTime(nextStart);
    setClipDuration(nextDuration);
    setStartTimeInput(formatTimeInput(nextStart));
    setClipDurationInput(formatSecondsInput(nextDuration));
    setError("");
  };

  const seekVideo = (value: number) => {
    const nextTime = roundTime(value);
    clipPreviewRef.current = null;
    setCurrentTime(nextTime);
    if (videoRef.current) {
      videoRef.current.currentTime = nextTime;
    }
  };

  const updateCurrentTime = useCallback(() => {
    const element = videoRef.current;
    if (element) {
      setCurrentTime(roundTime(element.currentTime));
    }
  }, []);

  const playClip = () => {
    const element = videoRef.current;
    if (!element || !videoInfo) {
      return;
    }
    const range = commitClipInputs();
    if (!range) {
      return;
    }
    clipPreviewRef.current = range;
    element.currentTime = clamp(range.start, 0, videoInfo.duration);
    void element.play();
  };

  const onTimeUpdate = () => {
    const element = videoRef.current;
    if (!element) {
      return;
    }
    setCurrentTime(roundTime(element.currentTime));

    const previewRange = clipPreviewRef.current;
    if (!previewRange) {
      return;
    }
    if (element.currentTime >= previewRange.end) {
      element.pause();
      element.currentTime = previewRange.start;
      clipPreviewRef.current = null;
    }
  };

  const exportGif = async () => {
    if (!videoInfo || !crop) {
      return;
    }
    const range = commitClipInputs();
    if (!range) {
      return;
    }
    if (parsedOutputWidth === false) {
      setError("输出宽度请输入正整数，或留空按框选宽度");
      return;
    }
    setBusy("export");
    setError("");
    setResult(null);
    try {
      const response = await fetch(apiUrl("/api/gif/export"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          video_id: videoInfo.id,
          start_time: range.start,
          duration: range.duration,
          crop,
          output_width: parsedOutputWidth,
          fps,
          speed_factor: speedFactor,
          loop,
          text
        })
      }).then(readJson<ExportResponse>);
      setResult(response);
      triggerDownload(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出失败");
    } finally {
      setBusy(null);
    }
  };

  return (
    <main className="app-shell">
      <style>{fontFaceStyles}</style>
      <header className="topbar">
        <div>
          <div className="brandline">
            <Film size={24} />
            <h1>Video2Emoticon</h1>
          </div>
          <p>{videoInfo ? `${videoInfo.filename} · ${videoInfo.width}x${videoInfo.height}` : "视频转 GIF 表情工具"}</p>
        </div>
        <button className="primary-button" disabled={!canExport} onClick={exportGif}>
          {busy === "export" ? <Loader2 className="spin" size={18} /> : <Download size={18} />}
          导出 GIF
        </button>
      </header>

      <section className="workbench">
        <aside className="control-panel">
          <section className="panel-section">
            <div className="section-title">
              <Upload size={18} />
              <h2>视频源</h2>
            </div>
            <input
              ref={fileInputRef}
              className="file-input"
              type="file"
              accept="video/*"
              onChange={(event) => {
                const file = event.currentTarget.files?.[0];
                if (file) {
                  void loadUploadedFile(file);
                }
              }}
            />
            <button className="wide-button" disabled={Boolean(busy)} onClick={() => fileInputRef.current?.click()}>
              {busy === "upload" ? <Loader2 className="spin" size={17} /> : <Video size={17} />}
              上传视频
            </button>
            <div className="bv-row">
              <input
                value={bv}
                placeholder="BV1... 或 bilibili 视频 URL"
                onChange={(event) => updateBvInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    void loadBilibili();
                  }
                }}
              />
              <button disabled={Boolean(busy)} onClick={() => void loadBilibili()}>
                {busy === "download" || busy === "pages" ? (
                  <Loader2 className="spin" size={17} />
                ) : (
                  <Download size={17} />
                )}
              </button>
            </div>
            {availableBilibiliPages.length > 1 ? (
              <label>
                分 P
                <select
                  value={bilibiliPage}
                  disabled={Boolean(busy)}
                  onChange={(event) => {
                    setBilibiliPage(Number(event.target.value));
                    setBilibiliStatus("");
                  }}
                >
                  {availableBilibiliPages.map((page) => (
                    <option key={page.page} value={page.page}>
                      {formatBilibiliPageOption(page)}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {bilibiliStatus ? <div className="metric">{bilibiliStatus}</div> : null}
          </section>

          <section className="panel-section">
            <div className="section-title">
              <Scissors size={18} />
              <h2>片段</h2>
            </div>
            <div className="field-grid two">
              <label>
                开始
                <input
                  className="time-input"
                  type="text"
                  placeholder="0:00"
                  value={startTimeInput}
                  onBlur={() => {
                    commitClipInputs();
                  }}
                  onChange={(event) => setStartTimeInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.currentTarget.blur();
                    }
                  }}
                />
              </label>
              <label>
                持续秒数
                <input
                  className="time-input"
                  type="text"
                  inputMode="decimal"
                  placeholder="3"
                  value={clipDurationInput}
                  onBlur={() => {
                    commitClipInputs();
                  }}
                  onChange={(event) => setClipDurationInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.currentTarget.blur();
                    }
                  }}
                />
              </label>
            </div>
            <div className="clip-readout">
              <div>
                <span>当前</span>
                <strong>{currentTimeLabel}</strong>
              </div>
              <div>
                <span>片段</span>
                <strong>{clipDurationLabel}</strong>
              </div>
              <div>
                <span>总长</span>
                <strong>{durationLabel}</strong>
              </div>
            </div>
            <label>
              播放位置
              <input
                type="range"
                min="0"
                max={videoInfo?.duration ?? 0}
                step="0.01"
                value={currentTime}
                disabled={!videoInfo}
                onChange={(event) => seekVideo(Number(event.target.value))}
              />
            </label>
            <div className="clip-actions">
              <button className="small-button secondary" disabled={!videoInfo} onClick={setClipStartFromCurrent}>
                <SkipBack size={16} />
                设为开始
              </button>
            </div>
            <button className="wide-button secondary" disabled={!videoInfo} onClick={playClip}>
              <Play size={17} />
              播放片段
            </button>
          </section>

          <section className="panel-section">
            <div className="section-title">
              <MousePointer2 size={18} />
              <h2>裁剪区域</h2>
            </div>
            <div className="metric">{selectedLabel}</div>
          </section>

          <section className="panel-section">
            <div className="section-title">
              <Repeat size={18} />
              <h2>输出</h2>
            </div>
            <label>
              帧率
              <input
                type="range"
                min="6"
                max="24"
                value={fps}
                onChange={(event) => setFps(Number(event.target.value))}
              />
            </label>
            <div className="range-value">{fps} fps</div>
            <label>
              变速
              <input
                type="range"
                min={MIN_SPEED_LEVEL}
                max={MAX_SPEED_LEVEL}
                step="1"
                value={speedLevel}
                onChange={(event) => setSpeedLevel(Number(event.target.value))}
              />
            </label>
            <div className="range-scale">
              <span>-16x</span>
              <span>0x</span>
              <span>16x</span>
            </div>
            <div className="range-value">{speedLabel}</div>
            <label>
              输出宽度
              <input
                type="number"
                min="1"
                inputMode="numeric"
                placeholder={crop ? `留空 = ${crop.width}` : "留空 = 框选宽度"}
                value={outputWidthInput}
                onChange={(event) => setOutputWidthInput(event.target.value)}
              />
            </label>
            <div className="range-value">{outputSizeLabel}</div>
            <label className="switch-row">
              <input type="checkbox" checked={loop} onChange={(event) => setLoop(event.target.checked)} />
              循环播放
            </label>
          </section>

          <section className="panel-section">
            <div className="section-title">
              <Type size={18} />
              <h2>文字</h2>
            </div>
            <label className="switch-row">
              <input
                type="checkbox"
                checked={text.enabled}
                onChange={(event) => setText((current) => ({ ...current, enabled: event.target.checked }))}
              />
              启用文字
            </label>
            <input
              ref={fontInputRef}
              className="file-input"
              type="file"
              accept=".ttf,.otf,.ttc,.otc,font/ttf,font/otf"
              multiple
              onChange={(event) => void uploadFonts(event.currentTarget.files)}
            />
            <textarea
              rows={3}
              value={text.content}
              placeholder="输入表情文字"
              onChange={(event) => setText((current) => ({ ...current, content: event.target.value }))}
            />
            <label>
              字体
              <select
                value={text.font_id ?? ""}
                onChange={(event) =>
                  setText((current) => ({ ...current, font_id: event.target.value || null }))
                }
              >
                <option value="">系统默认</option>
                {fonts.map((font) => (
                  <option key={font.id} value={font.id}>
                    {font.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="font-actions">
              <button className="small-button secondary" disabled={Boolean(fontBusy)} onClick={() => void loadFonts()}>
                {fontBusy === "load" ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
                刷新
              </button>
              <button className="small-button" disabled={Boolean(fontBusy)} onClick={() => fontInputRef.current?.click()}>
                {fontBusy === "upload" ? <Loader2 className="spin" size={16} /> : <FolderUp size={16} />}
                上传字体
              </button>
            </div>
            <div
              className="font-preview"
              style={selectedFont ? { fontFamily: `"${selectedFont.family}", sans-serif` } : undefined}
            >
              {previewText}
            </div>
            {fontError ? <div className="error-box compact">{fontError}</div> : null}
            <div className="field-grid two">
              <label>
                位置
                <select
                  value={text.position}
                  onChange={(event) =>
                    setText((current) => ({ ...current, position: event.target.value as TextLayer["position"] }))
                  }
                >
                  <option value="top">顶部</option>
                  <option value="center">居中</option>
                  <option value="bottom">底部</option>
                </select>
              </label>
              <label>
                字号
                <input
                  type="number"
                  min="12"
                  max="96"
                  value={text.font_size}
                  onChange={(event) => setText((current) => ({ ...current, font_size: Number(event.target.value) }))}
                />
              </label>
            </div>
            <div className="field-grid three">
              <label>
                文字
                <input
                  type="color"
                  value={text.color}
                  onChange={(event) => setText((current) => ({ ...current, color: event.target.value }))}
                />
              </label>
              <label>
                描边
                <input
                  type="color"
                  value={text.stroke_color}
                  onChange={(event) => setText((current) => ({ ...current, stroke_color: event.target.value }))}
                />
              </label>
              <label>
                背景
                <input
                  type="color"
                  value={text.box_color}
                  onChange={(event) => setText((current) => ({ ...current, box_color: event.target.value }))}
                />
              </label>
            </div>
            <label className="switch-row">
              <input
                type="checkbox"
                checked={text.box}
                onChange={(event) => setText((current) => ({ ...current, box: event.target.checked }))}
              />
              背景框
            </label>
          </section>
        </aside>

        <section className="preview-pane">
          <div
            className={`video-stage ${videoInfo ? "" : "empty"}`}
            ref={stageRef}
            onPointerDown={handleStagePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerUp}
          >
            {videoInfo ? (
              <>
                <video
                  ref={videoRef}
                  src={apiUrl(videoInfo.preview_url)}
                  controls
                  playsInline
                  onEnded={() => {
                    clipPreviewRef.current = null;
                    updateCurrentTime();
                  }}
                  onLoadedMetadata={updateCurrentTime}
                  onPause={() => {
                    clipPreviewRef.current = null;
                    updateCurrentTime();
                  }}
                  onSeeked={updateCurrentTime}
                  onTimeUpdate={onTimeUpdate}
                />
                {cropLayerStyle && cropStyle ? (
                  <div className="crop-layer" style={cropLayerStyle}>
                    <div className="shade top" style={{ height: cropStyle.top }} />
                    <div
                      className="shade left"
                      style={{ top: cropStyle.top, width: cropStyle.left, height: cropStyle.height }}
                    />
                    <div
                      className="shade right"
                      style={{
                        top: cropStyle.top,
                        left: cropStyle.left + cropStyle.width,
                        height: cropStyle.height
                      }}
                    />
                    <div
                      className="shade bottom"
                      style={{ top: cropStyle.top + cropStyle.height }}
                    />
                    <div
                      className="crop-rect"
                      style={cropStyle}
                      onPointerDown={(event) => startDrag(event, "move")}
                    >
                      {text.enabled && text.content.trim() && videoTextStyle ? (
                        <div className={`video-text-preview ${text.position}`} style={videoTextStyle}>
                          {text.content.trim()}
                        </div>
                      ) : null}
                      {(["nw", "n", "ne", "e", "se", "s", "sw", "w"] as DragMode[]).map((handle) => (
                        <span
                          key={handle}
                          className={`handle ${handle}`}
                          onPointerDown={(event) => startDrag(event, handle)}
                        />
                      ))}
                    </div>
                  </div>
                ) : null}
              </>
            ) : (
              <div className="empty-state">
                <Film size={48} />
                <span>等待视频</span>
              </div>
            )}
          </div>

          <div className="status-row">
            {error ? (
              <div className="error-box">{error}</div>
            ) : (
              <div className="hint-box">
                <Clock3 size={16} />
                {currentTimeLabel} / {durationLabel}
              </div>
            )}
            {result ? (
              <a className="download-link" href={apiUrl(result.download_url)} download>
                <Download size={18} />
                {result.filename} · {formatSize(result.size_bytes)}
              </a>
            ) : null}
          </div>
        </section>
      </section>
      <div className="made-by">Made with ❤️ by ZUOAJ</div>
    </main>
  );
}
