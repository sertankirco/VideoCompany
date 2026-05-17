"""
SFX waveform analysis and frame-accurate synchronization.

Finds the peak amplitude sample in an audio file and converts it to a
video frame index. The render engine uses lead_frames to schedule SFX
playback so that the peak hits exactly on the cut frame — never after it.
"""

import numpy as np


def find_peak_frame(sfx_path: str, video_fps: int = 60) -> int:
    """
    Returns the video frame index that corresponds to the waveform peak
    of the given audio file.

    Args:
        sfx_path: Absolute or relative path to a WAV/MP3/AIFF file.
        video_fps: Target video frame rate (default: 60).

    Returns:
        Frame index (0-based) of the loudest sample.
    """
    import librosa

    y, sr = librosa.load(sfx_path, sr=None, mono=True)
    peak_sample = int(np.argmax(np.abs(y)))
    peak_time = peak_sample / sr
    return int(peak_time * video_fps)


def get_sfx_lead_frames(sfx_path: str, video_fps: int = 60) -> int:
    """
    Returns how many video frames before a cut the SFX must begin playing
    so that its waveform peak lands on the exact cut frame.

    Example:
        If SFX peak is at frame 3 (from its own start), the engine must
        start the SFX 3 frames before the cut → sfx_start_frame = cut_frame - 3.

    Args:
        sfx_path: Path to the SFX audio file.
        video_fps: Target video frame rate (default: 60).

    Returns:
        Number of lead frames required.
    """
    return find_peak_frame(sfx_path, video_fps)


def analyze_sfx_library(assets_dir: str, video_fps: int = 60) -> dict:
    """
    Scans assets_dir for audio files and returns a dict mapping
    filename → lead_frames for all discovered SFX.

    Args:
        assets_dir: Directory containing SFX files.
        video_fps: Target video frame rate (default: 60).

    Returns:
        Dict[str, int] — {'/path/to/sfx.wav': lead_frames, ...}
    """
    import os

    supported = ('.wav', '.mp3', '.aiff', '.aif', '.flac')
    result = {}
    for fname in sorted(os.listdir(assets_dir)):
        if fname.lower().endswith(supported):
            full_path = os.path.join(assets_dir, fname)
            try:
                result[full_path] = get_sfx_lead_frames(full_path, video_fps)
            except Exception as exc:
                print(f"[sfx_sync] Could not analyze {fname}: {exc}")
    return result
