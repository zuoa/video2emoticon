#!/usr/bin/env python3
"""
remove_speech.py — 自动检测并删除歌曲中的说话片段

用法：
  # 自动检测并处理
  python remove_speech.py input.mp3 output.mp3

  # 调整灵敏度（越低抓越多，默认 0.80）
  python remove_speech.py input.mp3 output.mp3 --sensitivity 0.75

  # 只检测，不处理（看看会抓到哪些）
  python remove_speech.py input.mp3 --detect-only

  # 手动指定时间段（精准模式，格式：开始-结束，单位秒）
  python remove_speech.py input.mp3 output.mp3 --segments 32.5-35.0 96.2-100.5

  # 调整 crossfade 时长（默认 0.3 秒）
  python remove_speech.py input.mp3 output.mp3 --fade 0.2

依赖：numpy, scipy, soundfile, ffmpeg（用于非 WAV/FLAC 格式）
"""

import argparse
import os
import subprocess
import sys
import tempfile

import numpy as np
import soundfile as sf
from scipy.fftpack import dct
from scipy.ndimage import uniform_filter1d
from scipy.signal import resample_poly


# ── 音频加载 ──────────────────────────────────────────────────────────

def _load_audio(path, sr=22050, mono=True):
    """加载音频文件并重采样到目标采样率。"""
    try:
        y, file_sr = sf.read(path, dtype='float64')
    except Exception:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            tmp_path = tmp.name
        subprocess.run(
            ['ffmpeg', '-y', '-i', path, '-f', 'wav', tmp_path],
            check=True, capture_output=True
        )
        y, file_sr = sf.read(tmp_path, dtype='float64')
        os.remove(tmp_path)

    if mono and y.ndim > 1:
        y = y.mean(axis=1)

    if file_sr != sr:
        if y.ndim == 1:
            y = resample_poly(y, sr, file_sr)
        else:
            y = np.stack(
                [resample_poly(y[:, c], sr, file_sr) for c in range(y.shape[1])],
                axis=1,
            )

    return y, sr


# ── 特征提取 ──────────────────────────────────────────────────────────

def _detect_pitch(y, sr, hop_length=512, fmin=65.0, fmax=2093.0):
    """基于 FFT 自相关的基频检测（替代 librosa.pyin）。"""
    frame_len = 2048
    n_frames = 1 + (len(y) - frame_len) // hop_length
    if n_frames <= 0:
        return np.zeros(1), np.zeros(1, dtype=bool), np.zeros(1)

    min_lag = max(int(sr / fmax), 2)
    max_lag = min(int(sr / fmin), frame_len // 2)

    fft_size = 1
    while fft_size < 2 * frame_len:
        fft_size *= 2

    # 构造帧矩阵
    idx = (np.arange(n_frames) * hop_length)[:, None] + np.arange(frame_len)[None, :]
    frames = y[idx]
    frames -= frames.mean(axis=1, keepdims=True)

    # 批量 FFT 自相关
    F = np.fft.rfft(frames, fft_size, axis=1)
    corr = np.fft.irfft(F * np.conj(F), axis=1)[:, :frame_len]
    energy = np.where(corr[:, 0:1] < 1e-10, 1.0, corr[:, 0:1])
    corr /= energy

    # 在有效 lag 范围内找峰值
    search = corr[:, min_lag:max_lag + 1]
    peak_idx = np.argmax(search, axis=1)
    peak_val = np.max(search, axis=1)

    voiced_prob = np.clip(peak_val, 0, 1).astype(float)
    lag = (peak_idx + min_lag).astype(float)

    # 抛物线插值提高精度
    can_interp = (peak_idx > 0) & (peak_idx < search.shape[1] - 1)
    if np.any(can_interp):
        pi = peak_idx[can_interp]
        a = search[can_interp, pi - 1]
        b = search[can_interp, pi]
        g = search[can_interp, pi + 1]
        denom = a - 2 * b + g
        shift = np.where(np.abs(denom) > 1e-10, 0.5 * (a - g) / denom, 0.0)
        lag[can_interp] += shift

    f0 = np.where(peak_val > 0.1, sr / np.maximum(lag, 1), 0.0)
    voiced_flag = voiced_prob > 0.3

    return f0, voiced_flag, voiced_prob


def _mel_filterbank(sr, n_fft, n_mels=128):
    """构造 Mel 滤波器组。"""
    fmin, fmax = 0.0, sr / 2.0
    mel_lo = 2595.0 * np.log10(1.0 + fmin / 700.0)
    mel_hi = 2595.0 * np.log10(1.0 + fmax / 700.0)
    mels = np.linspace(mel_lo, mel_hi, n_mels + 2)
    freqs = 700.0 * (10.0 ** (mels / 2595.0) - 1.0)
    bins = np.floor((n_fft + 1) * freqs / sr).astype(int)

    fb = np.zeros((n_mels, n_fft // 2 + 1))
    for i in range(n_mels):
        left, center, right = bins[i], bins[i + 1], bins[i + 2]
        if center > left:
            k = np.arange(left, center)
            fb[i, k] = (k - left) / (center - left)
        if right > center:
            k = np.arange(center, right)
            fb[i, k] = (right - k) / (right - center)
    return fb


def _mfcc(y, sr, n_mfcc=20, hop_length=512, n_fft=2048):
    """计算 MFCC 特征（替代 librosa.feature.mfcc）。"""
    n_frames = 1 + (len(y) - n_fft) // hop_length
    if n_frames <= 0:
        return np.zeros((n_mfcc, 1))

    window = np.hanning(n_fft)
    idx = (np.arange(n_frames) * hop_length)[:, None] + np.arange(n_fft)[None, :]
    frames = y[idx] * window
    power = np.abs(np.fft.rfft(frames, axis=1)) ** 2

    fb = _mel_filterbank(sr, n_fft)
    mel_spec = power @ fb.T
    mel_spec = np.maximum(mel_spec, 1e-10)
    log_mel = np.log(mel_spec)
    return dct(log_mel, type=2, axis=1, norm='ortho')[:, :n_mfcc].T


def _zcr(y, hop_length=512, frame_length=2048):
    """计算过零率（替代 librosa.feature.zero_crossing_rate）。"""
    n_frames = 1 + (len(y) - frame_length) // hop_length
    if n_frames <= 0:
        return np.zeros(1)
    idx = (np.arange(n_frames) * hop_length)[:, None] + np.arange(frame_length)[None, :]
    frames = y[idx]
    signs = np.sign(frames)
    crossings = np.sum(np.abs(np.diff(signs, axis=1)) > 0, axis=1)
    return crossings / frame_length


def _rms(y, hop_length=512, frame_length=2048):
    """计算 RMS 能量（替代 librosa.feature.rms）。"""
    n_frames = 1 + (len(y) - frame_length) // hop_length
    if n_frames <= 0:
        return np.zeros(1)
    idx = (np.arange(n_frames) * hop_length)[:, None] + np.arange(frame_length)[None, :]
    frames = y[idx]
    return np.sqrt(np.mean(frames ** 2, axis=1))


# ── 检测 & 处理 ───────────────────────────────────────────────────────

def detect_speech(audio_path, sensitivity=0.80, min_dur=0.5):
    print(f"[1/2] 分析中: {audio_path}")
    y, sr = _load_audio(audio_path, sr=22050, mono=True)
    hop_length = 512
    frame_len = 2048
    n_frames = 1 + (len(y) - frame_len) // hop_length
    times = np.arange(n_frames) * hop_length / sr

    # 基频 + 浊音概率
    f0, voiced_flag, voiced_prob = _detect_pitch(y, sr, hop_length)
    smooth_vp = uniform_filter1d(voiced_prob, size=20)

    # MFCC
    mfcc = _mfcc(y, sr, n_mfcc=20, hop_length=hop_length)

    # 对齐各特征帧数
    min_len = min(len(smooth_vp), mfcc.shape[1], n_frames)
    smooth_vp = smooth_vp[:min_len]
    times = times[:min_len]
    mfcc = mfcc[:, :min_len]
    voiced_flag = voiced_flag[:min_len]
    f0 = f0[:min_len]

    mfcc_dist = np.sqrt(np.sum((mfcc - mfcc.mean(axis=1, keepdims=True)) ** 2, axis=0))
    mfcc_dist_norm = mfcc_dist / (mfcc_dist.max() + 1e-8)

    # 局部 voiced ratio 下降
    long_voiced = uniform_filter1d(voiced_flag.astype(float), size=int(8.0 * sr / hop_length))
    short_voiced = uniform_filter1d(voiced_flag.astype(float), size=int(0.8 * sr / hop_length))
    voiced_drop = np.clip(long_voiced - short_voiced, 0, 1)

    # 音高跳变
    f0_diff = np.abs(np.diff(np.where(voiced_flag, f0, np.nan), prepend=f0[0]))
    f0_jump_norm = uniform_filter1d(np.nan_to_num(f0_diff), size=10)
    f0_jump_norm /= (f0_jump_norm.max() + 1e-8)

    # 过零率
    zcr_arr = _zcr(y, hop_length, frame_len)[:min_len]
    zcr_norm = zcr_arr / (zcr_arr.max() + 1e-8)

    # 音量
    rms_arr = _rms(y, hop_length, frame_len)[:min_len]
    active = rms_arr / (rms_arr.max() + 1e-8) > 0.04

    # 综合打分
    score = (
        (1 - smooth_vp) * 0.35 +
        mfcc_dist_norm * 0.25 +
        voiced_drop * 0.20 +
        f0_jump_norm * 0.10 +
        zcr_norm * 0.10
    )

    threshold = np.percentile(score[active], sensitivity * 100)
    speech_mask = (score > threshold) & active

    # 合并连续片段
    segments = []
    in_seg, start = False, 0
    for i in range(len(speech_mask)):
        if speech_mask[i] and not in_seg:
            in_seg, start = True, times[i]
        elif not speech_mask[i] and in_seg:
            if times[i] - start >= min_dur:
                segments.append([float(start), float(times[i])])
            in_seg = False
    if in_seg:
        segments.append([float(start), float(times[-1])])

    # 合并间隔 < 0.6s 的相邻段
    merged = []
    for seg in segments:
        if merged and seg[0] - merged[-1][1] < 0.6:
            merged[-1][1] = seg[1]
        else:
            merged.append(seg)

    return merged


def splice(audio_path, segments, output_path, fade_sec=0.3):
    print(f"[2/2] 处理中，共 {len(segments)} 个片段...")
    y, sr = _load_audio(audio_path, sr=44100, mono=False)
    # soundfile 返回 (samples, channels)，转成 (channels, samples) 与原逻辑一致
    if y.ndim == 1:
        y = np.stack([y, y])
    else:
        y = y.T

    fade_f = int(fade_sec * sr)

    for start_s, end_s in sorted(segments, reverse=True):
        start_f = int(start_s * sr)
        end_f = int(end_s * sr)
        pre, post = y[:, :start_f], y[:, end_f:]

        if pre.shape[1] >= fade_f and post.shape[1] >= fade_f:
            t = np.linspace(0, np.pi / 2, fade_f)
            crossfade = pre[:, -fade_f:] * np.cos(t) + post[:, :fade_f] * np.sin(t)
            y = np.concatenate([pre[:, :-fade_f], crossfade, post[:, fade_f:]], axis=1)
        else:
            y = np.concatenate([pre, post], axis=1)

    tmp_wav = output_path + ".tmp.wav"
    sf.write(tmp_wav, y.T, sr)

    if output_path.lower().endswith(".mp3"):
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_wav,
             "-codec:a", "libmp3lame", "-qscale:a", "2", output_path],
            check=True, capture_output=True
        )
        os.remove(tmp_wav)
    else:
        os.rename(tmp_wav, output_path)

    print(f"✓ 已保存: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="自动删除歌曲中的说话片段",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("input", help="输入音频文件（mp3/wav/flac 等）")
    parser.add_argument("output", nargs="?", help="输出文件路径（--detect-only 时可省略）")
    parser.add_argument("--sensitivity", type=float, default=0.80,
                        help="检测灵敏度 0.70~0.92，越低抓越多（默认 0.80）")
    parser.add_argument("--min-dur", type=float, default=0.5,
                        help="最短说话片段秒数（默认 0.5）")
    parser.add_argument("--fade", type=float, default=0.3,
                        help="crossfade 时长秒数（默认 0.3）")
    parser.add_argument("--detect-only", action="store_true",
                        help="只检测，不处理，打印时间段后退出")
    parser.add_argument("--segments", nargs="+", metavar="START-END",
                        help="手动指定时间段（秒），如 32.5-35.0 96.2-100.5")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"错误：找不到文件 {args.input}", file=sys.stderr)
        sys.exit(1)

    if args.segments:
        segments = []
        for seg in args.segments:
            try:
                s, e = seg.split("-")
                segments.append([float(s), float(e)])
            except ValueError:
                print(f"格式错误：{seg}，应为 开始-结束，如 32.5-35.0", file=sys.stderr)
                sys.exit(1)
    else:
        segments = detect_speech(args.input, args.sensitivity, args.min_dur)

    total = sum(e - s for s, e in segments)
    print(f"\n检测到 {len(segments)} 个说话片段（共 {total:.1f} 秒）：")
    for i, (s, e) in enumerate(segments, 1):
        print(f"  [{i}] {s:.1f}s - {e:.1f}s  ({e-s:.1f}秒)")

    if args.detect_only:
        print("\n（--detect-only 模式，不做处理）")
        return

    if not args.output:
        print("错误：请指定输出文件，或使用 --detect-only", file=sys.stderr)
        sys.exit(1)

    if not segments:
        print("未检测到说话片段，直接复制原文件")
        import shutil
        shutil.copy(args.input, args.output)
        return

    splice(args.input, segments, args.output, args.fade)


if __name__ == "__main__":
    main()
