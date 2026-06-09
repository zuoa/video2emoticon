from __future__ import annotations

from functools import lru_cache
from importlib import import_module
from pathlib import Path
from typing import Any


class AudioEnhancementError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _modules() -> tuple[Any, Any, Any, Any, Any]:
    try:
        np = import_module("numpy")
        sf = import_module("soundfile")
        nr = import_module("noisereduce")
        scipy_signal = import_module("scipy.signal")
        scipy_ndimage = import_module("scipy.ndimage")
    except ModuleNotFoundError as exc:
        dependency = exc.name or "audio enhancement package"
        raise AudioEnhancementError(
            f"missing audio enhancement dependency: {dependency}. "
            "Install backend requirements before using enhancement."
        ) from exc
    return np, sf, nr, scipy_signal, scipy_ndimage


def _label_regions(mask: Any) -> tuple[Any, int]:
    np, _, _, _, _ = _modules()
    labeled = np.zeros(len(mask), dtype=int)
    region_id = 0
    in_region = False
    for index, value in enumerate(mask):
        if value and not in_region:
            region_id += 1
            in_region = True
        elif not value:
            in_region = False
        if in_region:
            labeled[index] = region_id
    return labeled, region_id


def _denoise(samples: Any, sample_rate: int, strength: str = "moderate") -> Any:
    _, _, nr, _, _ = _modules()
    if len(samples) == 0:
        return samples

    prop_map = {"mild": 0.5, "moderate": 0.75, "strong": 1.0}
    prop_decrease = prop_map.get(strength, 0.75)
    noise_sample_duration = min(int(sample_rate * 0.5), max(1, len(samples) // 10))
    noise_clip = samples[:noise_sample_duration]

    return nr.reduce_noise(
        y=samples,
        sr=sample_rate,
        y_noise=noise_clip,
        prop_decrease=prop_decrease,
        stationary=False,
        n_fft=2048,
        win_length=None,
        n_jobs=1,
    )


def _declick(samples: Any, sample_rate: int, sensitivity: float = 3.0, window_ms: float = 2.0) -> Any:
    np, _, _, _, scipy_ndimage = _modules()
    if len(samples) == 0:
        return samples

    window = max(int(sample_rate * window_ms / 1000), 16)
    sample_squared = samples**2
    local_rms = np.sqrt(np.maximum(scipy_ndimage.uniform_filter1d(sample_squared, size=window), 1e-12))
    is_click = np.abs(samples) / local_rms > sensitivity

    pad = max(window // 4, 2)
    mask = np.zeros(len(samples), dtype=bool)
    for index in np.where(is_click)[0]:
        start = max(0, index - pad)
        end = min(len(samples), index + pad + 1)
        mask[start:end] = True

    if not mask.any():
        return samples

    fixed = samples.copy()
    labeled, region_count = _label_regions(mask)
    for region_id in range(1, region_count + 1):
        indexes = np.where(labeled == region_id)[0]
        start, end = indexes[0], indexes[-1]
        left_value = samples[start - 1] if start > 0 else 0.0
        right_value = samples[end + 1] if end < len(samples) - 1 else 0.0
        fixed[start : end + 1] = np.linspace(left_value, right_value, end - start + 1)

    return fixed


def _declip(samples: Any, threshold: float = 0.95, iterations: int = 50) -> Any:
    np, _, _, _, _ = _modules()
    if len(samples) == 0:
        return samples

    samples = samples.astype(np.float64)
    peak = np.max(np.abs(samples))
    if peak < 1e-8:
        return samples.astype(np.float32)

    normalized = samples / peak
    clipped = (normalized >= threshold) | (normalized <= -threshold)
    if not clipped.any():
        return samples.astype(np.float32)

    window_size = min(2048, len(normalized))
    if window_size < 8:
        limited = np.clip(normalized, -threshold, threshold)
        return (limited * peak).astype(np.float32)

    hop = max(window_size // 2, 1)
    window = np.hanning(window_size)
    if not window.any():
        window = np.ones(window_size)

    restored = np.zeros_like(normalized)
    weight = np.zeros_like(normalized)
    starts = list(range(0, len(normalized) - window_size + 1, hop))
    final_start = len(normalized) - window_size
    if not starts or starts[-1] != final_start:
        starts.append(final_start)

    for start in starts:
        end = start + window_size
        frame = normalized[start:end].copy()
        frame_clipped = clipped[start:end]
        estimate = frame * window

        for _ in range(iterations):
            spectrum = np.fft.rfft(estimate)
            magnitude = np.abs(spectrum)
            phase = np.angle(spectrum)
            smoothing = np.exp(-np.arange(len(magnitude)) / (len(magnitude) * 2))
            estimate = np.fft.irfft(magnitude * smoothing * np.exp(1j * phase), n=window_size)

            non_clipped = ~frame_clipped
            estimate[non_clipped] = frame[non_clipped] * window[non_clipped]
            if frame_clipped.any():
                limit = threshold * window[frame_clipped]
                estimate[frame_clipped] = np.clip(estimate[frame_clipped], -limit, limit)

        restored[start:end] += estimate
        weight[start:end] += window

    covered = weight >= 1e-8
    restored[covered] = restored[covered] / weight[covered]
    restored[~covered] = normalized[~covered]
    restored = restored * peak
    restored = np.tanh(restored * 0.9) / np.tanh(np.array(0.9)) * peak

    return restored.astype(np.float32)


def _eq_enhance(samples: Any, sample_rate: int) -> Any:
    _, _, _, scipy_signal, _ = _modules()
    high_pass = scipy_signal.butter(4, 80 / (sample_rate / 2), btype="high", output="sos")
    samples = scipy_signal.sosfilt(high_pass, samples)

    mud_band = scipy_signal.butter(2, [200 / (sample_rate / 2), 400 / (sample_rate / 2)], btype="band", output="sos")
    mud = scipy_signal.sosfilt(mud_band, samples)
    samples = samples - 0.15 * mud

    presence_band = scipy_signal.butter(
        2,
        [2000 / (sample_rate / 2), 5000 / (sample_rate / 2)],
        btype="band",
        output="sos",
    )
    presence = scipy_signal.sosfilt(presence_band, samples)
    return samples + 0.2 * presence


def _soft_compress(
    samples: Any,
    sample_rate: int,
    threshold: float = 0.5,
    ratio: float = 3.0,
    attack_ms: float = 5,
    release_ms: float = 50,
) -> Any:
    np, _, _, _, _ = _modules()
    frame_length = 256
    hop_length = 64
    if len(samples) < frame_length:
        return samples

    attack_samples = max(int(sample_rate * attack_ms / 1000), 1)
    release_samples = max(int(sample_rate * release_ms / 1000), 1)

    frame_count = (len(samples) - frame_length) // hop_length + 1
    frames = np.array([samples[index * hop_length : index * hop_length + frame_length] for index in range(frame_count)])
    rms = np.sqrt(np.mean(frames**2, axis=1))
    rms_full = np.interp(np.arange(len(samples)), np.arange(len(rms)) * hop_length, rms)
    rms_full = np.clip(rms_full, 1e-8, None)

    gain = np.ones_like(rms_full)
    for index, level in enumerate(rms_full):
        if level > threshold:
            target_gain = (threshold + (level - threshold) / ratio) / level
        else:
            target_gain = 1.0

        previous_gain = gain[index - 1] if index > 0 else 1.0
        if target_gain < previous_gain:
            alpha = np.exp(-1 / attack_samples)
        else:
            alpha = np.exp(-1 / release_samples)
        gain[index] = alpha * previous_gain + (1 - alpha) * target_gain

    return samples * gain


def _normalize(samples: Any, target_rms: float = 0.15) -> Any:
    np, _, _, _, _ = _modules()
    rms = np.sqrt(np.mean(samples**2))
    if rms < 1e-8:
        return samples
    return samples * (target_rms / rms)


def enhance_audio_file(
    input_path: Path,
    output_path: Path,
    strength: str = "moderate",
    use_eq: bool = True,
    use_compress: bool = True,
    click_sensitivity: float = 3.0,
    clip_threshold: float = 0.95,
    clip_iterations: int = 50,
) -> None:
    np, sf, _, _, _ = _modules()
    samples, sample_rate = sf.read(input_path, always_2d=False)
    if len(samples) == 0:
        raise AudioEnhancementError("audio clip is empty")

    if getattr(samples, "ndim", 1) == 2:
        samples = samples.mean(axis=1)
    else:
        samples = samples.copy()

    samples = _denoise(samples, sample_rate, strength=strength)
    samples = _declip(samples, threshold=clip_threshold, iterations=clip_iterations)
    samples = _declick(samples, sample_rate, sensitivity=click_sensitivity)
    if use_eq:
        samples = _eq_enhance(samples, sample_rate)
    if use_compress:
        samples = _soft_compress(samples, sample_rate)

    samples = _normalize(samples)
    samples = np.clip(samples, -1.0, 1.0).astype(np.float32)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output_path, samples, sample_rate, subtype="PCM_16")
