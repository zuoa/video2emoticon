#!/usr/bin/env python3
"""
音频降噪与品质提升脚本
功能：
  1. 自动降噪（去除背景杂音、说话声等）
  2. 去点状电流音/咔哒声（脉冲噪声去除）
  3. 人声增强（EQ 提升清晰度）
  4. 动态压缩（音量均衡化）
  5. 音量标准化
用法：
  python3 audio_enhance.py <输入文件> [输出文件] [选项]
示例：
  python3 audio_enhance.py song.mp3
  python3 audio_enhance.py song.mp3 song_clean.wav --strength strong
  python3 audio_enhance.py song.wav song_clean.wav --strength mild --no-compress
  python3 audio_enhance.py song.wav song_clean.wav --click-sensitivity 2.5
"""

import sys
import os
import argparse
import numpy as np
import soundfile as sf
import noisereduce as nr
from scipy import signal
import subprocess
import tempfile


# ──────────────────────────────────────────────
# 1. 加载音频（支持 mp3/wav/flac/m4a/aac 等）
# ──────────────────────────────────────────────
def load_audio(path):
    """用 ffmpeg 先转成临时 wav，再用 soundfile 读取，支持所有格式"""
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.wav', '.flac', '.ogg'):
        data, sr = sf.read(path, always_2d=False)
        return data, sr
    # 其他格式先用 ffmpeg 解码
    tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
    tmp.close()
    cmd = ['ffmpeg', '-y', '-i', path, '-ar', '44100', '-ac', '1', tmp.name]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 解码失败:\n{result.stderr.decode()}")
    data, sr = sf.read(tmp.name)
    os.unlink(tmp.name)
    return data, sr


# ──────────────────────────────────────────────
# 2. 降噪
# ──────────────────────────────────────────────
def denoise(y, sr, strength='moderate'):
    """
    使用 noisereduce 进行频谱降噪。
    strength: mild | moderate | strong
    """
    prop_map = {'mild': 0.5, 'moderate': 0.75, 'strong': 1.0}
    prop = prop_map.get(strength, 0.75)

    # 取前 0.5 秒作为噪声样本（如果静音开头更准确）
    noise_sample_duration = min(int(sr * 0.5), len(y) // 10)
    noise_clip = y[:noise_sample_duration]

    y_denoised = nr.reduce_noise(
        y=y,
        sr=sr,
        y_noise=noise_clip,
        prop_decrease=prop,
        stationary=False,  # 非稳态噪声（含说话声）
        n_fft=2048,
        win_length=None,
        n_jobs=1,
    )
    return y_denoised


# ──────────────────────────────────────────────
# 3. 去点状电流音 / 咔哒声（脉冲噪声去除）
# ──────────────────────────────────────────────
def declicker(y, sensitivity=3.0, window_ms=2.0, sr=44100):
    """
    检测并修复脉冲噪声（点状电流音、咔哒声、噼啪声）。

    原理：
      1. 计算局部 RMS，找出幅度异常突出的采样点（脉冲）
      2. 对检测到的脉冲区域用线性插值修复

    参数：
      sensitivity  检测灵敏度，越小检测越激进（默认 3.0，
                   电流音多时可调低到 2.0~2.5）
      window_ms    局部 RMS 窗口大小（毫秒）
    """
    win = max(int(sr * window_ms / 1000), 16)

    # 用滑动窗口计算局部 RMS（用 uniform_filter 近似，速度快）
    from scipy.ndimage import uniform_filter1d
    y_sq = y ** 2
    local_rms = np.sqrt(np.maximum(uniform_filter1d(y_sq, size=win), 1e-12))

    # 单点绝对幅度 vs 局部 RMS 的比值超过阈值 → 脉冲
    ratio = np.abs(y) / local_rms
    is_click = ratio > sensitivity

    # 膨胀掩码：脉冲前后各扩展几个采样，避免修复不完整
    pad = max(win // 4, 2)
    mask = np.zeros(len(y), dtype=bool)
    click_idx = np.where(is_click)[0]
    for idx in click_idx:
        lo = max(0, idx - pad)
        hi = min(len(y), idx + pad + 1)
        mask[lo:hi] = True

    if not mask.any():
        return y  # 没有检测到脉冲，直接返回

    # 线性插值修复：找到每段连续脉冲区域，用两端采样值插值
    y_fixed = y.copy()
    labeled, n_regions = _label_regions(mask)
    repaired = 0
    for region_id in range(1, n_regions + 1):
        idxs = np.where(labeled == region_id)[0]
        lo, hi = idxs[0], idxs[-1]
        # 取区域两端的"干净"采样作为插值端点
        left_val = y[lo - 1] if lo > 0 else 0.0
        right_val = y[hi + 1] if hi < len(y) - 1 else 0.0
        y_fixed[lo:hi + 1] = np.linspace(left_val, right_val, hi - lo + 1)
        repaired += 1

    print(f"      检测到并修复了 {repaired} 处脉冲噪声")
    return y_fixed


def _label_regions(mask):
    """将布尔掩码中的连续 True 区域标记为不同 ID（类似 scipy.ndimage.label）"""
    labeled = np.zeros(len(mask), dtype=int)
    region_id = 0
    in_region = False
    for i, v in enumerate(mask):
        if v and not in_region:
            region_id += 1
            in_region = True
        elif not v:
            in_region = False
        if in_region:
            labeled[i] = region_id
    return labeled, region_id


# ──────────────────────────────────────────────
# 4. 削波修复（破音/爆音）
# ──────────────────────────────────────────────
def declip(y, threshold=0.95, iterations=50):
    """
    修复削波（clipping）导致的破音/爆音。

    原理（一致性投影迭代，Consistent Iterative Clipping Restoration）：
      1. 检测波形中被截平的区域（幅度 >= threshold）
      2. 在频域对这些区域反复迭代：
         - 对削波区域"松开"约束，允许频谱自由估计
         - 对非削波区域保持原始幅度不变
         - 迭代收敛后得到平滑的波形估计

    参数：
      threshold   削波检测阈值（默认 0.95，即幅度超过最大值 95% 视为削波）
      iterations  迭代次数（默认 50，破音严重时可调高到 100）
    """
    y = y.astype(np.float64)
    peak = np.max(np.abs(y))
    if peak < 1e-8:
        return y.astype(np.float32)

    # 归一化到 [-1, 1] 方便处理
    y_norm = y / peak
    clip_level = threshold

    # 检测削波区域
    clipped_pos = y_norm >= clip_level  # 正向削波
    clipped_neg = y_norm <= -clip_level  # 负向削波
    clipped = clipped_pos | clipped_neg
    n_clipped = np.sum(clipped)

    if n_clipped == 0:
        print("      未检测到削波，跳过")
        return y.astype(np.float32)

    clip_ratio = n_clipped / len(y) * 100
    print(f"      检测到削波采样点 {n_clipped} 个（占 {clip_ratio:.1f}%），开始修复…")

    # 用 FFT 窗口做迭代修复
    # 窗口大小取 2048，overlap 50%
    win_size = 2048
    hop = win_size // 2
    window = np.hanning(win_size)

    y_out = np.zeros_like(y_norm)
    weight = np.zeros_like(y_norm)

    # 对每个帧迭代修复
    n_frames = (len(y_norm) - win_size) // hop + 1
    for frame_idx in range(n_frames):
        start = frame_idx * hop
        end = start + win_size
        if end > len(y_norm):
            break

        frame = y_norm[start:end].copy()
        frame_clipped = clipped[start:end]
        frame_windowed = frame * window

        # 迭代投影
        estimate = frame_windowed.copy()
        for _ in range(iterations):
            # 频域平滑（取幅度谱，保留相位）
            spec = np.fft.rfft(estimate)
            mag = np.abs(spec)
            phase = np.angle(spec)
            # 轻微高频衰减让估计更平滑
            mag_smooth = mag * np.exp(-np.arange(len(mag)) / (len(mag) * 2))
            estimate = np.fft.irfft(mag_smooth * np.exp(1j * phase), n=win_size)

            # 投影约束：非削波区域强制恢复原始值
            non_clip = ~frame_clipped
            estimate[non_clip] = frame_windowed[non_clip]
            # 削波区域：限制幅度不超过 clip_level（考虑窗函数加权）
            win_clip = window[frame_clipped]
            est_clip = estimate[frame_clipped]
            limit = clip_level * win_clip
            est_clip = np.clip(est_clip, -limit, limit)
            estimate[frame_clipped] = est_clip

        # overlap-add 合成
        y_out[start:end] += estimate
        weight[start:end] += window

    # 归一化 overlap
    weight = np.maximum(weight, 1e-8)
    y_out = y_out / weight

    # 还原原始幅度，并做软限幅（-3dB 软膝，避免产生新的削波）
    y_out = y_out * peak
    y_out = np.tanh(y_out * 0.9) / np.tanh(np.array(0.9)) * peak

    return y_out.astype(np.float32)


# ──────────────────────────────────────────────
# 5. 人声 EQ 增强
# ──────────────────────────────────────────────
def eq_enhance(y, sr, strength=1.0):
    """
    针对人声频段的 EQ 调整：
    - 高通 80 Hz   → 去除低频轰鸣
    - 轻微提升 2–5 kHz → 增加清晰度/齿音
    - 轻微削减 200–400 Hz → 减少浑浊感

    strength: 0~1，整体力度系数（0.5 = 效果减半，0 = 不做 EQ）
    """
    if strength <= 0:
        return y

    # 高通滤波（去低频）
    sos_hp = signal.butter(4, 80 / (sr / 2), btype='high', output='sos')
    y = signal.sosfilt(sos_hp, y)

    # 削减 200–400 Hz（浑浊频段）
    sos_mud = signal.butter(2, [200 / (sr / 2), 400 / (sr / 2)],
                            btype='band', output='sos')
    mud = signal.sosfilt(sos_mud, y)
    y = y - (0.15 * strength) * mud

    # 提升 2–5 kHz（人声清晰度）
    sos_pres = signal.butter(2, [2000 / (sr / 2), 5000 / (sr / 2)],
                             btype='band', output='sos')
    presence = signal.sosfilt(sos_pres, y)
    y = y + (0.2 * strength) * presence

    return y


# ──────────────────────────────────────────────
# 6. 动态压缩（软压缩）
# ──────────────────────────────────────────────
def soft_compress(y, threshold=0.5, ratio=3.0, attack_ms=5, release_ms=50, sr=44100):
    """
    简单的前馈 RMS 压缩器，用于均衡音量起伏。
    """
    attack_samples = int(sr * attack_ms / 1000)
    release_samples = int(sr * release_ms / 1000)

    # RMS 包络（纯 numpy，不依赖 librosa）
    frame_len = 256
    hop_len = 64
    n_frames = (len(y) - frame_len) // hop_len + 1
    frames = np.array([y[i * hop_len: i * hop_len + frame_len] for i in range(n_frames)])
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    # 内插回采样长度
    rms_full = np.interp(
        np.arange(len(y)),
        np.arange(len(rms)) * hop_len,
        rms
    )
    rms_full = np.clip(rms_full, 1e-8, None)

    gain = np.ones_like(rms_full)
    for i in range(len(rms_full)):
        level = rms_full[i]
        if level > threshold:
            target_gain = (threshold + (level - threshold) / ratio) / level
        else:
            target_gain = 1.0
        if target_gain < gain[i - 1] if i > 0 else 1.0:
            alpha = np.exp(-1 / attack_samples)
        else:
            alpha = np.exp(-1 / release_samples)
        gain[i] = alpha * (gain[i - 1] if i > 0 else 1.0) + (1 - alpha) * target_gain

    return y * gain


# ──────────────────────────────────────────────
# 7. 音量标准化（RMS 归一化到 -14 LUFS 近似）
# ──────────────────────────────────────────────
def normalize(y, target_rms=0.15):
    rms = np.sqrt(np.mean(y ** 2))
    if rms < 1e-8:
        return y
    return y * (target_rms / rms)


# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────
def process(input_path, output_path, strength='moderate', use_eq=True, use_compress=True,
            click_sensitivity=3.0, clip_threshold=0.95, clip_iterations=50, eq_strength=0.5):
    print(f"[1/7] 加载音频: {input_path}")
    y, sr = load_audio(input_path)

    stereo = y.ndim == 2
    if stereo:
        y_mono = y.mean(axis=1)
    else:
        y_mono = y.copy()

    print(f"      采样率={sr} Hz, 时长={len(y_mono) / sr:.1f}s, 立体声={stereo}")

    print(f"[2/7] 降噪 (强度={strength}) …")
    y_mono = denoise(y_mono, sr, strength)

    print(f"[3/7] 削波修复/破音处理 (阈值={clip_threshold}) …")
    y_mono = declip(y_mono, threshold=clip_threshold, iterations=clip_iterations)

    print(f"[4/7] 去点状电流音 (灵敏度={click_sensitivity}) …")
    y_mono = declicker(y_mono, sensitivity=click_sensitivity, sr=sr)

    if use_eq:
        print(f"[5/7] EQ 人声增强 (力度={eq_strength}) …")
        y_mono = eq_enhance(y_mono, sr, strength=eq_strength)
    else:
        print("[5/7] EQ 已跳过")

    if use_compress:
        print("[6/7] 动态压缩 …")
        y_mono = soft_compress(y_mono, sr=sr)
    else:
        print("[6/7] 压缩已跳过")

    print("[7/7] 音量标准化 & 保存 …")
    y_mono = normalize(y_mono)
    y_mono = np.clip(y_mono, -1.0, 1.0).astype(np.float32)

    sf.write(output_path, y_mono, sr, subtype='PCM_16')
    print(f"\n✅ 完成！输出文件: {output_path}")


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="音频降噪与品质提升工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('input', help='输入音频文件 (mp3/wav/flac/m4a 等)')
    parser.add_argument('output', nargs='?', help='输出文件路径 (默认: <输入名>_clean.wav)')
    parser.add_argument('--strength', choices=['mild', 'moderate', 'strong'],
                        default='moderate', help='降噪强度 (默认: moderate)')
    parser.add_argument('--click-sensitivity', type=float, default=3.0,
                        help='电流音检测灵敏度，越小越激进 (默认: 3.0，建议范围 2.0~4.0)')
    parser.add_argument('--clip-threshold', type=float, default=0.95,
                        help='削波检测阈值 (默认: 0.95，破音严重时可调低到 0.85)')
    parser.add_argument('--clip-iterations', type=int, default=50,
                        help='削波修复迭代次数 (默认: 50，破音严重时可调高到 100)')
    parser.add_argument('--no-eq', action='store_true', help='跳过 EQ 增强')
    parser.add_argument('--eq-strength', type=float, default=0.5,
                        help='EQ 增强力度 0~1 (默认: 0.5)')
    parser.add_argument('--no-compress', action='store_true', help='跳过动态压缩')

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"错误：找不到文件 {args.input}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        output = args.output
    else:
        base, _ = os.path.splitext(args.input)
        output = base + '_clean.wav'

    process(
        args.input,
        output,
        strength=args.strength,
        use_eq=not args.no_eq,
        use_compress=not args.no_compress,
        click_sensitivity=args.click_sensitivity,
        clip_threshold=args.clip_threshold,
        clip_iterations=args.clip_iterations,
        eq_strength=args.eq_strength,
    )


if __name__ == '__main__':
    main()