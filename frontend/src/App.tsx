import {
  Download,
  Film,
  FolderUp,
  Loader2,
  MousePointer2,
  Play,
  RefreshCw,
  Repeat,
  Scissors,
  Type,
  Upload,
  Video
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CropRect, ExportResponse, FontInfo, TextLayer, VideoInfo } from "./types";

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

type DragMode = "create" | "move" | "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

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

function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
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

export function App() {
  const [videoInfo, setVideoInfo] = useState<VideoInfo | null>(null);
  const [crop, setCrop] = useState<CropRect | null>(null);
  const [startTime, setStartTime] = useState(0);
  const [endTime, setEndTime] = useState(3);
  const [fps, setFps] = useState(12);
  const [loop, setLoop] = useState(true);
  const [text, setText] = useState<TextLayer>(defaultText);
  const [bv, setBv] = useState("");
  const [busy, setBusy] = useState<"upload" | "download" | "export" | null>(null);
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

  const canExport = Boolean(videoInfo && crop && endTime > startTime && !busy);

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
    setStartTime(0);
    setEndTime(Math.min(3, Math.max(0.1, videoInfo.duration)));
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

  const loadBilibili = async () => {
    if (!bv.trim()) {
      setError("请输入 BV 号");
      return;
    }
    setBusy("download");
    setError("");
    try {
      const info = await fetch(apiUrl("/api/videos/bilibili"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bv })
      }).then(readJson<VideoInfo>);
      setVideoInfo(info);
    } catch (err) {
      setError(err instanceof Error ? err.message : "下载失败");
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

  const playClip = () => {
    const element = videoRef.current;
    if (!element || !videoInfo) {
      return;
    }
    element.currentTime = clamp(startTime, 0, videoInfo.duration);
    void element.play();
  };

  const onTimeUpdate = () => {
    const element = videoRef.current;
    if (!element || endTime <= startTime) {
      return;
    }
    if (element.currentTime >= endTime) {
      element.pause();
      element.currentTime = startTime;
    }
  };

  const exportGif = async () => {
    if (!videoInfo || !crop) {
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
          start_time: startTime,
          end_time: endTime,
          crop,
          fps,
          loop,
          text
        })
      }).then(readJson<ExportResponse>);
      setResult(response);
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
                placeholder="BV1..."
                onChange={(event) => setBv(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    void loadBilibili();
                  }
                }}
              />
              <button disabled={Boolean(busy)} onClick={() => void loadBilibili()}>
                {busy === "download" ? <Loader2 className="spin" size={17} /> : <Download size={17} />}
              </button>
            </div>
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
                  type="number"
                  min="0"
                  step="0.1"
                  value={startTime}
                  onChange={(event) => setStartTime(Number(event.target.value))}
                />
              </label>
              <label>
                结束
                <input
                  type="number"
                  min="0.1"
                  step="0.1"
                  value={endTime}
                  onChange={(event) => setEndTime(Number(event.target.value))}
                />
              </label>
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
            {error ? <div className="error-box">{error}</div> : <div className="hint-box">默认 12 fps</div>}
            {result ? (
              <a className="download-link" href={apiUrl(result.download_url)} download>
                <Download size={18} />
                {result.filename} · {formatSize(result.size_bytes)}
              </a>
            ) : null}
          </div>
        </section>
      </section>
    </main>
  );
}
