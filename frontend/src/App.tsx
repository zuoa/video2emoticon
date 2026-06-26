import {
  Clock3,
  Download,
  Film,
  FileText,
  FolderUp,
  Home,
  Loader2,
  MousePointer2,
  Music,
  Play,
  Quote,
  RefreshCw,
  Repeat,
  Scissors,
  SkipBack,
  Sparkles,
  Type,
  Upload,
  Video
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import type {
  AudioFormat,
  BilibiliPageInfo,
  BilibiliPagesResponse,
  CropRect,
  ExportResponse,
  FontInfo,
  KeyPoint,
  SummaryResponse,
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

type AppPage = "home" | "gif" | "audio" | "summary";
type NavigateTo = (page: AppPage) => void;
type BilibiliBusy = "pages" | "download";

interface BilibiliDownloadContext {
  bv: string;
  page: number;
}

interface UseBilibiliSourceOptions {
  busy: string | null;
  setBusy: (busy: BilibiliBusy | null) => void;
  setError: (message: string) => void;
  onInputChange?: () => void;
  onPageChange?: () => void;
  onBeforeDownload?: () => void;
  onDownloaded: (info: VideoInfo, context: BilibiliDownloadContext) => string | void;
  selectedPageStatus: string;
  multiPageDetectedStatus: (bv: string, count: number) => string;
  downloadErrorMessage: string;
}

function pageFromHash(): AppPage {
  if (window.location.hash === "#/gif") {
    return "gif";
  }
  if (window.location.hash === "#/audio") {
    return "audio";
  }
  if (window.location.hash === "#/summary") {
    return "summary";
  }
  return "home";
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

function formatBilibiliMultiPageStatus(bv: string, count: number): string {
  return `已识别 ${bv} 的 ${count} 个分 P，请选择后下载`;
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

function normalizeEndTime(value: number, start: number, duration: number | undefined): number {
  if (duration === undefined) {
    return roundTime(Math.max(start + MIN_CLIP_DURATION, value));
  }
  return roundTime(clamp(value, start + MIN_CLIP_DURATION, duration));
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

interface ToolHeaderProps {
  currentPage: AppPage;
  title: string;
  subtitle: string;
  icon: ReactNode;
  actions?: ReactNode;
  navigateTo: NavigateTo;
}

function ToolNav({ currentPage, navigateTo }: { currentPage: AppPage; navigateTo: NavigateTo }) {
  return (
    <nav className="tool-nav" aria-label="工具导航">
      <button className={currentPage === "home" ? "active" : ""} type="button" onClick={() => navigateTo("home")}>
        <Home size={16} />
        主页
      </button>
      <button className={currentPage === "gif" ? "active" : ""} type="button" onClick={() => navigateTo("gif")}>
        <Film size={16} />
        GIF
      </button>
      <button className={currentPage === "audio" ? "active" : ""} type="button" onClick={() => navigateTo("audio")}>
        <Music size={16} />
        音频
      </button>
      <button className={currentPage === "summary" ? "active" : ""} type="button" onClick={() => navigateTo("summary")}>
        <FileText size={16} />
        总结
      </button>
    </nav>
  );
}

function ToolHeader({ currentPage, title, subtitle, icon, actions, navigateTo }: ToolHeaderProps) {
  return (
    <header className="topbar">
      <div className="brandline">
        <button className="brand-home" type="button" onClick={() => navigateTo("home")} aria-label="回到主页">
          <Video size={20} />
        </button>
        <div className="brand-copy">
          <div className="tool-title-row">
            {icon}
            <h1>{title}</h1>
          </div>
          <p>{subtitle}</p>
        </div>
      </div>
      <ToolNav currentPage={currentPage} navigateTo={navigateTo} />
      {actions ? <div className="topbar-actions">{actions}</div> : null}
    </header>
  );
}

function SiteFooter({ currentPage, navigateTo }: { currentPage: AppPage; navigateTo: NavigateTo }) {
  return (
    <footer className="site-footer">
      <div className="footer-brand">
        <span className="footer-mark">V2A</span>
        <div>
          <strong>Video to Any</strong>
          <span>视频处理工具箱</span>
        </div>
      </div>
      <ToolNav currentPage={currentPage} navigateTo={navigateTo} />
      <div className="footer-meta">MADE BY ZUOAJ</div>
    </footer>
  );
}

function useBilibiliSource({
  busy,
  setBusy,
  setError,
  onInputChange,
  onPageChange,
  onBeforeDownload,
  onDownloaded,
  selectedPageStatus,
  multiPageDetectedStatus,
  downloadErrorMessage
}: UseBilibiliSourceOptions) {
  const [bv, setBv] = useState("");
  const [pages, setPages] = useState<BilibiliPagesResponse | null>(null);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");

  const parsedInput = useMemo(() => parseBilibiliInput(bv), [bv]);
  const inputBv = parsedInput?.bv ?? null;
  const availablePages = useMemo(
    () => (inputBv && pages?.bv === inputBv ? pages.pages : []),
    [inputBv, pages]
  );
  const canUse = Boolean(parsedInput && !busy);

  const updateInput = useCallback(
    (value: string) => {
      const nextParsedInput = parseBilibiliInput(value);
      setBv(value);
      setPages(null);
      setPage(nextParsedInput?.page ?? 1);
      setStatus(
        nextParsedInput && value.trim() !== nextParsedInput.bv
          ? `已识别 ${nextParsedInput.bv}${nextParsedInput.page ? ` P${nextParsedInput.page}` : ""}`
          : ""
      );
      onInputChange?.();
    },
    [onInputChange]
  );

  const selectPage = useCallback(
    (nextPage: number) => {
      setPage(nextPage);
      setStatus(selectedPageStatus);
      onPageChange?.();
    },
    [onPageChange, selectedPageStatus]
  );

  const fetchPages = useCallback(
    async (parsed: ParsedBilibiliInput): Promise<BilibiliPagesResponse | null> => {
      setBusy("pages");
      setError("");
      setStatus("");
      try {
        const response = await fetch(apiUrl("/api/videos/bilibili/pages"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ bv: parsed.bv, page: parsed.page })
        }).then(readJson<BilibiliPagesResponse>);
        setBv(response.bv);
        setPages(response);
        const nextPage = response.pages.some((item) => item.page === response.selected_page)
          ? response.selected_page
          : response.pages[0]?.page ?? 1;
        setPage(nextPage);
        setStatus(
          response.pages.length > 1
            ? multiPageDetectedStatus(response.bv, response.pages.length)
            : `已识别 ${response.bv} P${nextPage}`
        );
        return response;
      } catch (err) {
        setError(err instanceof Error ? err.message : "分 P 读取失败");
        return null;
      } finally {
        setBusy(null);
      }
    },
    [multiPageDetectedStatus, setBusy, setError]
  );

  const refreshPages = useCallback(async () => {
    if (!bv.trim()) {
      setError("请输入 BV 号或 Bilibili 视频地址");
      return;
    }
    const parsed = parseBilibiliInput(bv);
    if (!parsed) {
      setError("请输入 BV 号或合法的 bilibili.com/video/BV... 地址");
      return;
    }
    await fetchPages(parsed);
  }, [bv, fetchPages, setError]);

  const downloadPage = useCallback(
    async (sourceBv: string, selectedPage: number) => {
      setBusy("download");
      setError("");
      setStatus("");
      onBeforeDownload?.();
      try {
        const info = await fetch(apiUrl("/api/videos/bilibili"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ bv: sourceBv, page: selectedPage })
        }).then(readJson<VideoInfo>);
        setBv(sourceBv);
        const nextStatus = onDownloaded(info, { bv: sourceBv, page: selectedPage });
        if (nextStatus !== undefined) {
          setStatus(nextStatus);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : downloadErrorMessage);
      } finally {
        setBusy(null);
      }
    },
    [downloadErrorMessage, onBeforeDownload, onDownloaded, setBusy, setError]
  );

  const downloadSource = useCallback(async () => {
    if (!bv.trim()) {
      setError("请输入 BV 号或 Bilibili 视频地址");
      return;
    }
    const parsed = parseBilibiliInput(bv);
    if (!parsed) {
      setError("请输入 BV 号或合法的 bilibili.com/video/BV... 地址");
      return;
    }

    let selectedPage = availablePages.some((item) => item.page === page)
      ? page
      : availablePages[0]?.page ?? parsed.page ?? 1;
    let selectedBv = parsed.bv;

    if (availablePages.length === 0) {
      const response = await fetchPages(parsed);
      if (!response) {
        return;
      }
      selectedBv = response.bv;
      selectedPage = response.pages.some((item) => item.page === response.selected_page)
        ? response.selected_page
        : response.pages[0]?.page ?? 1;
      if (response.pages.length > 1 && parsed.page === null) {
        setStatus(multiPageDetectedStatus(response.bv, response.pages.length));
        return;
      }
    }

    await downloadPage(selectedBv, selectedPage);
  }, [availablePages, bv, downloadPage, fetchPages, multiPageDetectedStatus, page, setError]);

  return {
    bv,
    page,
    status,
    parsedInput,
    availablePages,
    canUse,
    setStatus,
    updateInput,
    selectPage,
    refreshPages,
    downloadSource
  };
}

function HomePage({ navigateTo }: { navigateTo: NavigateTo }) {
  return (
    <main className="app-shell home-shell">
      <ToolHeader
        currentPage="home"
        title="Video to Any"
        subtitle="选择一个视频工具，进入独立工作台"
        icon={<Video size={24} />}
        navigateTo={navigateTo}
      />

      <section className="home-board">
        <div className="home-hero">
          <div className="pixel-badge">VIDEO TO ANY</div>
          <h2>选择工具</h2>
          <p>每个工具独立处理任务，共用同一套导航、缓存和输出。</p>
        </div>
        <div className="tool-grid">
          <button className="tool-card" type="button" onClick={() => navigateTo("gif")}>
            <span className="tool-card-icon">
              <Film size={30} />
            </span>
            <span className="tool-card-title">视频转 GIF</span>
            <span className="tool-card-copy">裁剪、字幕、变速、循环</span>
            <span className="tool-card-action">进入</span>
          </button>
          <button className="tool-card" type="button" onClick={() => navigateTo("audio")}>
            <span className="tool-card-icon">
              <Music size={30} />
            </span>
            <span className="tool-card-title">BV 提取音频</span>
            <span className="tool-card-copy">下载、试听、按时间点导出</span>
            <span className="tool-card-action">进入</span>
          </button>
          <button className="tool-card" type="button" onClick={() => navigateTo("summary")}>
            <span className="tool-card-icon">
              <FileText size={30} />
            </span>
            <span className="tool-card-title">BV 视频总结</span>
            <span className="tool-card-copy">整体总结、关键时间点、金句提炼</span>
            <span className="tool-card-action">进入</span>
          </button>
          <div className="tool-card disabled">
            <span className="tool-card-icon">
              <Repeat size={30} />
            </span>
            <span className="tool-card-title">更多工具</span>
            <span className="tool-card-copy">为下一种 video to any 输出预留</span>
            <span className="tool-card-action">待添加</span>
          </div>
        </div>
      </section>
      <SiteFooter currentPage="home" navigateTo={navigateTo} />
    </main>
  );
}

function GifPage({ navigateTo }: { navigateTo: NavigateTo }) {
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

  const bilibiliSource = useBilibiliSource({
    busy,
    setBusy: (nextBusy) => setBusy(nextBusy),
    setError,
    onBeforeDownload: () => setResult(null),
    onDownloaded: (info) => {
      setVideoInfo(info);
    },
    selectedPageStatus: "已选择分 P，点击下载所选分 P",
    multiPageDetectedStatus: formatBilibiliMultiPageStatus,
    downloadErrorMessage: "下载失败"
  });
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
  const clipEndLabel = useMemo(() => {
    const start = normalizedStartInput ?? startTime;
    const duration = normalizedClipDurationInput ?? clipDuration;
    return formatTimeInput(roundTime(start + duration));
  }, [clipDuration, normalizedClipDurationInput, normalizedStartInput, startTime]);
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

  const loadUploadedFile = async (file: File) => {
    setBusy("upload");
    setError("");
    bilibiliSource.setStatus("");
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
      <ToolHeader
        currentPage="gif"
        title="视频转 GIF"
        subtitle={videoInfo ? `${videoInfo.filename} · ${videoInfo.width}x${videoInfo.height}` : "制作 GIF 表情"}
        icon={<Film size={24} />}
        navigateTo={navigateTo}
        actions={
          <button className="primary-button" disabled={!canExport} onClick={exportGif}>
            {busy === "export" ? <Loader2 className="spin" size={18} /> : <Download size={18} />}
            导出 GIF
          </button>
        }
      />

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
            <button className="wide-button" type="button" disabled={Boolean(busy)} onClick={() => fileInputRef.current?.click()}>
              {busy === "upload" ? <Loader2 className="spin" size={17} /> : <Video size={17} />}
              上传视频
            </button>
            <div className="bv-row">
              <input
                value={bilibiliSource.bv}
                placeholder="BV1... 或 bilibili 视频 URL"
                onChange={(event) => bilibiliSource.updateInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    void bilibiliSource.downloadSource();
                  }
                }}
              />
              <button
                type="button"
                disabled={!bilibiliSource.canUse}
                onClick={() => void bilibiliSource.refreshPages()}
                aria-label="识别 Bilibili 分 P"
              >
                {busy === "pages" ? <Loader2 className="spin" size={17} /> : <RefreshCw size={17} />}
                识别
              </button>
            </div>
            {bilibiliSource.availablePages.length > 1 ? (
              <label>
                分 P
                <select
                  value={bilibiliSource.page}
                  disabled={Boolean(busy)}
                  onChange={(event) => bilibiliSource.selectPage(Number(event.target.value))}
                >
                  {bilibiliSource.availablePages.map((page) => (
                    <option key={page.page} value={page.page}>
                      {formatBilibiliPageOption(page)}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <button
              className="wide-button secondary"
              type="button"
              disabled={!bilibiliSource.canUse}
              onClick={() => void bilibiliSource.downloadSource()}
            >
              {busy === "download" ? <Loader2 className="spin" size={17} /> : <Download size={17} />}
              {bilibiliSource.availablePages.length > 1 ? "下载所选分 P" : "下载 BV 视频"}
            </button>
            {videoInfo ? (
              <div className="metric source-metric">
                已载入 {videoInfo.filename} · {formatTimeInput(videoInfo.duration)} · {videoInfo.width}x{videoInfo.height}
              </div>
            ) : null}
            {bilibiliSource.status ? <div className="metric">{bilibiliSource.status}</div> : null}
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
            <div className="clip-readout four">
              <div>
                <span>开始</span>
                <strong>{formatTimeInput(normalizedStartInput ?? startTime)}</strong>
              </div>
              <div>
                <span>结束</span>
                <strong>{clipEndLabel}</strong>
              </div>
              <div>
                <span>时长</span>
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
            <button className="wide-button" type="button" disabled={!canExport} onClick={exportGif}>
              {busy === "export" ? <Loader2 className="spin" size={17} /> : <Download size={17} />}
              导出 GIF
            </button>
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
      <SiteFooter currentPage="gif" navigateTo={navigateTo} />
    </main>
  );
}

function AudioExtractorPage({ navigateTo }: { navigateTo: NavigateTo }) {
  const [videoInfo, setVideoInfo] = useState<VideoInfo | null>(null);
  const [startTimeInput, setStartTimeInput] = useState(formatTimeInput(0));
  const [endTimeInput, setEndTimeInput] = useState(formatTimeInput(10));
  const [format, setFormat] = useState<AudioFormat>("mp3");
  const [enhanceAudio, setEnhanceAudio] = useState(false);
  const [busy, setBusy] = useState<"pages" | "download" | "extract" | null>(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ExportResponse | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);

  const previewRef = useRef<HTMLVideoElement | null>(null);
  const previewRangeRef = useRef<ClipRange | null>(null);

  const resetAudioSource = useCallback(() => {
    setVideoInfo(null);
    setPreviewing(false);
    setCurrentTime(0);
    previewRangeRef.current = null;
    setResult(null);
  }, []);

  const bilibiliSource = useBilibiliSource({
    busy,
    setBusy: (nextBusy) => setBusy(nextBusy),
    setError,
    onInputChange: resetAudioSource,
    onPageChange: resetAudioSource,
    onBeforeDownload: resetAudioSource,
    onDownloaded: (info, context) => {
      setVideoInfo(info);
      setStartTimeInput(formatTimeInput(0));
      setEndTimeInput(formatTimeInput(Math.min(10, Math.max(MIN_CLIP_DURATION, info.duration))));
      return `已下载 ${context.bv} P${context.page} · ${formatTimeInput(info.duration)}`;
    },
    selectedPageStatus: "已选择分 P，点击下载视频",
    multiPageDetectedStatus: formatBilibiliMultiPageStatus,
    downloadErrorMessage: "视频下载失败"
  });
  const selectedPageInfo = useMemo(
    () => bilibiliSource.availablePages.find((page) => page.page === bilibiliSource.page) ?? null,
    [bilibiliSource.availablePages, bilibiliSource.page]
  );
  const sourceDuration = videoInfo?.duration ?? selectedPageInfo?.duration ?? undefined;
  const parsedStartInput = useMemo(() => parseTimeInput(startTimeInput), [startTimeInput]);
  const parsedEndInput = useMemo(() => parseTimeInput(endTimeInput), [endTimeInput]);
  const normalizedStartInput =
    parsedStartInput === null ? null : normalizeStartTime(parsedStartInput, sourceDuration);
  const normalizedEndInput =
    parsedEndInput === null || normalizedStartInput === null
      ? null
      : normalizeEndTime(parsedEndInput, normalizedStartInput, sourceDuration);
  const normalizedClipDurationInput =
    normalizedStartInput === null || normalizedEndInput === null
      ? null
      : roundTime(normalizedEndInput - normalizedStartInput);
  const canDownload = bilibiliSource.canUse;
  const canPreview = Boolean(videoInfo && normalizedStartInput !== null && normalizedEndInput !== null && !busy);
  const canExtract = Boolean(videoInfo && normalizedStartInput !== null && normalizedEndInput !== null && !busy);
  const currentTimeLabel = useMemo(() => formatTimeInput(currentTime), [currentTime]);

  useEffect(() => {
    return () => {
      previewRef.current?.pause();
    };
  }, []);

  const updateAudioCurrentTime = useCallback(() => {
    const element = previewRef.current;
    if (element) {
      setCurrentTime(roundTime(element.currentTime));
    }
  }, []);

  const commitClipInputs = useCallback((): ClipRange | null => {
    const nextStartRaw = parseTimeInput(startTimeInput);
    const nextEndRaw = parseTimeInput(endTimeInput);
    if (nextStartRaw === null || nextEndRaw === null) {
      setError("开始时间或结束时间格式不正确");
      return null;
    }
    if (nextEndRaw <= nextStartRaw) {
      setError("结束时间必须晚于开始时间");
      return null;
    }

    const nextStart = normalizeStartTime(nextStartRaw, sourceDuration);
    const nextEnd = normalizeEndTime(nextEndRaw, nextStart, sourceDuration);
    const nextDuration = roundTime(nextEnd - nextStart);
    setStartTimeInput(formatTimeInput(nextStart));
    setEndTimeInput(formatTimeInput(nextEnd));
    setError("");
    return { start: nextStart, end: nextEnd, duration: nextDuration };
  }, [endTimeInput, sourceDuration, startTimeInput]);

  const playAudioClip = () => {
    const element = previewRef.current;
    if (!element || !videoInfo) {
      setError("请先下载视频");
      return;
    }
    const range = commitClipInputs();
    if (!range) {
      return;
    }
    previewRangeRef.current = range;
    element.currentTime = clamp(range.start, 0, videoInfo.duration);
    setError("");
    void element
      .play()
      .then(() => setPreviewing(true))
      .catch(() => {
        previewRangeRef.current = null;
        setPreviewing(false);
        setError("试听播放失败，请检查浏览器播放权限");
      });
  };

  const stopAudioPreview = () => {
    previewRef.current?.pause();
    previewRangeRef.current = null;
    setPreviewing(false);
  };

  const onPreviewTimeUpdate = () => {
    const element = previewRef.current;
    if (element) {
      setCurrentTime(roundTime(element.currentTime));
    }
    const range = previewRangeRef.current;
    if (!element || !range) {
      return;
    }
    if (element.currentTime >= range.end) {
      element.pause();
      element.currentTime = range.start;
      previewRangeRef.current = null;
      setPreviewing(false);
    }
  };

  const setAudioStartFromCurrent = () => {
    if (!videoInfo) {
      return;
    }
    const rawTime = previewRef.current?.currentTime ?? currentTime;
    const nextStart = normalizeStartTime(rawTime, videoInfo.duration);
    const currentEnd = parseTimeInput(endTimeInput);
    const fallbackEnd = Math.min(videoInfo.duration, nextStart + Math.min(10, videoInfo.duration - nextStart));
    const nextEnd =
      currentEnd === null || currentEnd <= nextStart
        ? normalizeEndTime(fallbackEnd, nextStart, videoInfo.duration)
        : normalizeEndTime(currentEnd, nextStart, videoInfo.duration);
    setStartTimeInput(formatTimeInput(nextStart));
    setEndTimeInput(formatTimeInput(nextEnd));
    setError("");
    setResult(null);
  };

  const setAudioEndFromCurrent = () => {
    if (!videoInfo) {
      return;
    }
    const startRaw = parseTimeInput(startTimeInput);
    if (startRaw === null) {
      setError("开始时间格式不正确");
      return;
    }
    const nextStart = normalizeStartTime(startRaw, videoInfo.duration);
    const rawTime = previewRef.current?.currentTime ?? currentTime;
    if (rawTime <= nextStart) {
      setError("结束时间必须晚于开始时间");
      return;
    }
    const nextEnd = normalizeEndTime(rawTime, nextStart, videoInfo.duration);
    setStartTimeInput(formatTimeInput(nextStart));
    setEndTimeInput(formatTimeInput(nextEnd));
    setError("");
    setResult(null);
  };

  const extractAudio = async () => {
    if (!videoInfo) {
      setError("请先下载视频");
      return;
    }
    const range = commitClipInputs();
    if (!range) {
      return;
    }

    setBusy("extract");
    setError("");
    setResult(null);
    try {
      const response = await fetch(apiUrl("/api/audio/extract"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          video_id: videoInfo.id,
          start_time: range.start,
          end_time: range.end,
          format,
          enhance: enhanceAudio
        })
      }).then(readJson<ExportResponse>);
      setResult(response);
      bilibiliSource.setStatus(`已提取 ${videoInfo.filename} · ${format.toUpperCase()}${enhanceAudio ? " · 已增强" : ""}`);
      triggerDownload(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "音频提取失败");
    } finally {
      setBusy(null);
    }
  };

  return (
    <main className="app-shell audio-shell">
      <ToolHeader
        currentPage="audio"
        title="BV 音频片段"
        subtitle={bilibiliSource.status || "从 Bilibili BV 号提取音频片段"}
        icon={<Music size={24} />}
        navigateTo={navigateTo}
        actions={
          <button className="primary-button" type="button" disabled={!canExtract} onClick={() => void extractAudio()}>
            {busy === "extract" ? <Loader2 className="spin" size={18} /> : <Download size={18} />}
            提取音频
          </button>
        }
      />

      <section className="audio-workbench">
        <div className="audio-column audio-column-primary">
          <section className="panel-section audio-panel audio-source-panel">
            <div className="section-title">
              <Music size={18} />
              <h2>视频源</h2>
            </div>
            <div className="bv-row">
              <input
                value={bilibiliSource.bv}
                placeholder="BV1... 或 bilibili 视频 URL"
                onChange={(event) => bilibiliSource.updateInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    void bilibiliSource.downloadSource();
                  }
                }}
              />
              <button
                type="button"
                disabled={!bilibiliSource.canUse}
                onClick={() => void bilibiliSource.refreshPages()}
                aria-label="识别 Bilibili 分 P"
              >
                {busy === "pages" ? <Loader2 className="spin" size={17} /> : <RefreshCw size={17} />}
                识别
              </button>
            </div>
            {bilibiliSource.availablePages.length > 1 ? (
              <label>
                分 P
                <select
                  value={bilibiliSource.page}
                  disabled={Boolean(busy)}
                  onChange={(event) => bilibiliSource.selectPage(Number(event.target.value))}
                >
                  {bilibiliSource.availablePages.map((page) => (
                    <option key={page.page} value={page.page}>
                      {formatBilibiliPageOption(page)}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {bilibiliSource.status ? <div className="metric">{bilibiliSource.status}</div> : null}
            <button
              className="wide-button"
              type="button"
              disabled={!canDownload}
              onClick={() => void bilibiliSource.downloadSource()}
            >
              {busy === "download" ? <Loader2 className="spin" size={17} /> : <Download size={17} />}
              下载视频
            </button>
          </section>

          <section className="panel-section audio-panel audio-clip-panel">
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
                  onChange={(event) => {
                    setStartTimeInput(event.target.value);
                    setResult(null);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.currentTarget.blur();
                    }
                  }}
                />
              </label>
              <label>
                结束
                <input
                  className="time-input"
                  type="text"
                  inputMode="decimal"
                  placeholder="0:10"
                  value={endTimeInput}
                  onBlur={() => {
                    commitClipInputs();
                  }}
                  onChange={(event) => {
                    setEndTimeInput(event.target.value);
                    setResult(null);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.currentTarget.blur();
                    }
                  }}
                />
              </label>
            </div>
            <div className="clip-readout four">
              <div>
                <span>开始</span>
                <strong>{formatTimeInput(normalizedStartInput ?? 0)}</strong>
              </div>
              <div>
                <span>结束</span>
                <strong>{formatTimeInput(normalizedEndInput ?? MIN_CLIP_DURATION)}</strong>
              </div>
              <div>
                <span>时长</span>
                <strong>{formatTimeInput(normalizedClipDurationInput ?? MIN_CLIP_DURATION)}</strong>
              </div>
              <div>
                <span>总长</span>
                <strong>{formatTimeInput(sourceDuration ?? 0)}</strong>
              </div>
            </div>
            <div className="clip-actions two">
              <button className="small-button secondary" type="button" disabled={!videoInfo} onClick={setAudioStartFromCurrent}>
                <SkipBack size={16} />
                设为开始
              </button>
              <button className="small-button secondary" type="button" disabled={!videoInfo} onClick={setAudioEndFromCurrent}>
                <Clock3 size={16} />
                设为结束
              </button>
            </div>
            <div className="range-value">当前 {currentTimeLabel}</div>
          </section>
        </div>

        <div className="audio-column audio-column-secondary">
          <section className="panel-section audio-panel audio-preview-panel">
            <div className="section-title">
              <Play size={18} />
              <h2>试听</h2>
            </div>
            {videoInfo ? (
              <>
                <video
                  ref={previewRef}
                  className="audio-preview-media"
                  src={apiUrl(videoInfo.preview_url)}
                  controls
                  playsInline
                  preload="metadata"
                  onEnded={() => {
                    previewRangeRef.current = null;
                    setPreviewing(false);
                    updateAudioCurrentTime();
                  }}
                  onPause={() => {
                    setPreviewing(false);
                    updateAudioCurrentTime();
                  }}
                  onLoadedMetadata={updateAudioCurrentTime}
                  onSeeked={updateAudioCurrentTime}
                  onTimeUpdate={onPreviewTimeUpdate}
                />
                <div className="audio-preview-actions">
                  <button className="small-button" type="button" disabled={!canPreview} onClick={playAudioClip}>
                    <Play size={16} />
                    {previewing ? "重新试听" : "试听片段"}
                  </button>
                  <button className="small-button secondary" type="button" disabled={!previewing} onClick={stopAudioPreview}>
                    停止
                  </button>
                </div>
                <div className="metric">{videoInfo.filename} · {formatTimeInput(videoInfo.duration)}</div>
              </>
            ) : (
              <div className="audio-empty">请先下载视频，再试听选中的音频片段</div>
            )}
          </section>

          <section className="panel-section audio-panel audio-output-panel">
            <div className="section-title">
              <Download size={18} />
              <h2>输出</h2>
            </div>
            <div className="format-options" role="group" aria-label="音频格式">
              {(["mp3", "m4a", "wav"] as AudioFormat[]).map((item) => (
                <button
                  key={item}
                  className={`format-option ${format === item ? "active" : ""}`}
                  type="button"
                  aria-pressed={format === item}
                  onClick={() => {
                    setFormat(item);
                    setResult(null);
                  }}
                >
                  {item.toUpperCase()}
                </button>
              ))}
            </div>
            <label className="switch-row">
              <input
                type="checkbox"
                checked={enhanceAudio}
                onChange={(event) => {
                  setEnhanceAudio(event.target.checked);
                  setResult(null);
                }}
              />
              增强
            </label>
            <button className="wide-button" type="button" disabled={!canExtract} onClick={() => void extractAudio()}>
              {busy === "extract" ? <Loader2 className="spin" size={17} /> : <Download size={17} />}
              {enhanceAudio ? "增强并下载" : "提取并下载"}
            </button>
          </section>

          <section className="status-row audio-status">
            {error ? (
              <div className="error-box">{error}</div>
            ) : (
              <div className="hint-box">
                <Clock3 size={16} />
                {videoInfo ? "已下载" : "尚未下载"} · {formatTimeInput(normalizedStartInput ?? 0)} - {formatTimeInput(normalizedEndInput ?? 0)} · {format.toUpperCase()}{enhanceAudio ? " · 增强" : ""}
              </div>
            )}
            {result ? (
              <a className="download-link" href={apiUrl(result.download_url)} download>
                <Download size={18} />
                {result.filename} · {formatSize(result.size_bytes)}
              </a>
            ) : null}
          </section>
        </div>
      </section>
      <SiteFooter currentPage="audio" navigateTo={navigateTo} />
    </main>
  );
}

function SummaryPage({ navigateTo }: { navigateTo: NavigateTo }) {
  const [busy, setBusy] = useState<"pages" | "download" | "summary" | null>(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState<SummaryResponse | null>(null);
  const [copied, setCopied] = useState(false);

  const resetResult = useCallback(() => {
    setResult(null);
    setError("");
    setCopied(false);
  }, []);

  const bilibiliSource = useBilibiliSource({
    busy,
    setBusy,
    setError,
    onInputChange: resetResult,
    onPageChange: resetResult,
    onBeforeDownload: resetResult,
    onDownloaded: () => undefined,
    selectedPageStatus: "已选择分 P，点击生成总结",
    multiPageDetectedStatus: (bv, count) => `已识别 ${bv} 的 ${count} 个分 P，请选择后生成总结`,
    downloadErrorMessage: "操作失败"
  });

  const canGenerate = Boolean(bilibiliSource.canUse && !busy);
  const generating = busy === "summary";

  const generate = useCallback(async () => {
    const parsed = parseBilibiliInput(bilibiliSource.bv);
    if (!parsed) {
      setError("请输入 BV 号或合法的 bilibili.com/video/BV... 地址");
      return;
    }
    setBusy("summary");
    setError("");
    setResult(null);
    setCopied(false);
    try {
      const response = await fetch(apiUrl("/api/summary/generate"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bv: parsed.bv, page: bilibiliSource.page })
      }).then(readJson<SummaryResponse>);
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成总结失败");
    } finally {
      setBusy(null);
    }
  }, [bilibiliSource.bv, bilibiliSource.page, setBusy]);

  const copyMarkdown = useCallback(async () => {
    if (!result) {
      return;
    }
    try {
      await navigator.clipboard.writeText(result.markdown);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("复制失败，请手动选择 Markdown 文本复制");
    }
  }, [result]);

  const downloadSubtitle = useCallback(() => {
    if (!result) {
      return;
    }
    const anchor = document.createElement("a");
    anchor.href = apiUrl(result.subtitle_url);
    anchor.download = `${result.bv}_P${result.page}.${result.subtitle_format || "txt"}`;
    anchor.style.display = "none";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
  }, [result]);

  return (
    <main className="app-shell summary-shell">
      <ToolHeader
        currentPage="summary"
        title="BV 视频总结"
        subtitle="输入 BV 号，自动拉取 CC 字幕并生成结构化总结"
        icon={<FileText size={24} />}
        navigateTo={navigateTo}
      />

      <section className="tool-board summary-board">
        <section className="panel-section">
          <div className="section-title">
            <Video size={18} />
            <h2>视频来源</h2>
          </div>
          <div className="bv-row">
            <input
              value={bilibiliSource.bv}
              placeholder="BV1... 或 bilibili 视频 URL"
              onChange={(event) => bilibiliSource.updateInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  void bilibiliSource.refreshPages();
                }
              }}
            />
            <button
              type="button"
              disabled={!bilibiliSource.canUse}
              onClick={() => void bilibiliSource.refreshPages()}
              aria-label="识别 Bilibili 分 P"
            >
              {busy === "pages" ? <Loader2 className="spin" size={17} /> : <RefreshCw size={17} />}
              识别
            </button>
          </div>
          {bilibiliSource.availablePages.length > 1 ? (
            <label>
              分 P
              <select
                value={bilibiliSource.page}
                disabled={Boolean(busy)}
                onChange={(event) => bilibiliSource.selectPage(Number(event.target.value))}
              >
                {bilibiliSource.availablePages.map((page) => (
                  <option key={page.page} value={page.page}>
                    {formatBilibiliPageOption(page)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <button
            className="wide-button"
            type="button"
            disabled={!canGenerate}
            onClick={() => void generate()}
          >
            {generating ? <Loader2 className="spin" size={17} /> : <Sparkles size={17} />}
            {generating ? "正在生成…" : "生成总结"}
          </button>
          {bilibiliSource.status ? <div className="metric">{bilibiliSource.status}</div> : null}
          {error ? <div className="metric summary-error">{error}</div> : null}
          <div className="metric summary-hint">
            提示：仅支持带 CC 字幕（人工或 AI 字幕）的视频；长视频会分段总结后合并。
          </div>
        </section>

        {result ? (
          <section className="panel-section summary-result">
            <div className="summary-head">
              <div className="summary-head-text">
                <h3>{result.title || `${result.bv} P${result.page}`}</h3>
                <div className="summary-meta">
                  {result.up ? <span>UP：{result.up}</span> : null}
                  {result.duration ? <span>时长：{result.duration}</span> : null}
                  <span>{result.bv} · P{result.page}</span>
                  {result.cached ? <span className="cache-tag">已缓存</span> : null}
                </div>
              </div>
              <div className="summary-actions">
                <button type="button" onClick={() => void copyMarkdown()}>
                  {copied ? "已复制" : "复制 Markdown"}
                </button>
                <button type="button" onClick={downloadSubtitle}>
                  <Download size={15} />
                  下载字幕
                </button>
              </div>
            </div>

            <div className="summary-block">
              <div className="section-title">
                <FileText size={16} />
                <h4>视频总结</h4>
              </div>
              <p className="summary-overall">{result.overall_summary}</p>
            </div>

            {result.key_points.length > 0 ? (
              <div className="summary-block">
                <div className="section-title">
                  <Clock3 size={16} />
                  <h4>关键内容点</h4>
                </div>
                <ol className="keypoint-list">
                  {result.key_points.map((kp, idx) => (
                    <li key={`${kp.seconds}-${idx}`}>
                      <a className="keypoint-time" href={kp.url} target="_blank" rel="noreferrer">
                        [{kp.time}]
                      </a>
                      <div className="keypoint-body">
                        <strong>{kp.title}</strong>
                        {kp.detail ? <span>{kp.detail}</span> : null}
                      </div>
                    </li>
                  ))}
                </ol>
              </div>
            ) : null}

            {result.quotes.length > 0 ? (
              <div className="summary-block">
                <div className="section-title">
                  <Quote size={16} />
                  <h4>金句 / 知识点</h4>
                </div>
                <ul className="quote-list">
                  {result.quotes.map((quote, idx) => (
                    <li key={idx}>{quote}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>
        ) : null}
      </section>
      <SiteFooter currentPage="summary" navigateTo={navigateTo} />
    </main>
  );
}

export function App() {
  const [page, setPage] = useState<AppPage>(() => pageFromHash());

  useEffect(() => {
    const syncPage = () => setPage(pageFromHash());
    window.addEventListener("hashchange", syncPage);
    return () => window.removeEventListener("hashchange", syncPage);
  }, []);

  const navigateTo = useCallback((nextPage: AppPage) => {
    window.location.hash = nextPage === "home" ? "#/" : `#/${nextPage}`;
    setPage(nextPage);
  }, []);

  if (page === "gif") {
    return <GifPage navigateTo={navigateTo} />;
  }
  if (page === "audio") {
    return <AudioExtractorPage navigateTo={navigateTo} />;
  }
  if (page === "summary") {
    return <SummaryPage navigateTo={navigateTo} />;
  }
  return <HomePage navigateTo={navigateTo} />;
}
