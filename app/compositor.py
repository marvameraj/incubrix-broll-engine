import os
import subprocess
import cv2
import numpy as np
from app.errors import AppError


RATIO_DIMS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}


def detect_subject_center_x(video_path: str) -> float:
    """Returns normalized horizontal center [0.0..1.0] of primary subject for smart 9:16 crop."""
    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    centers = []

    for frac in (0.2, 0.5, 0.8):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total_frames * frac))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.1, 4)
        if len(faces) > 0:
            x, _, fw, _ = max(faces, key=lambda b: b[2] * b[3])
            centers.append((x + fw / 2.0) / w)
        else:
            edges = cv2.Canny(gray, 80, 180)
            col_weights = edges.sum(axis=0).astype(float)
            if col_weights.sum() > 0:
                xs = np.arange(w)
                centers.append(float((xs * col_weights).sum() / col_weights.sum()) / w)

    cap.release()
    return float(np.median(centers)) if centers else 0.5


def build_srt_file(scenes: list[dict], srt_path: str):
    def fmt_time(sec: float) -> str:
        ms = int(round((sec - int(sec)) * 1000))
        s = int(sec) % 60
        m = (int(sec) // 60) % 60
        h = int(sec) // 3600
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, sc in enumerate(scenes, start=1):
            f.write(f"{i}\n{fmt_time(sc['start_s'])} --> {fmt_time(sc['end_s'])}\n{sc['text'].strip()}\n\n")


def normalize_scene_segment(
    raw_clip_path: str,
    target_duration: float,
    aspect_ratio: str,
    out_segment_path: str,
):
    if aspect_ratio not in RATIO_DIMS:
        raise AppError("UNSUPPORTED_ASPECT_RATIO", f"Unsupported aspect ratio: {aspect_ratio}", status_code=400)

    out_w, out_h = RATIO_DIMS[aspect_ratio]
    subject_cx = detect_subject_center_x(raw_clip_path)

    vf = (
        f"scale='if(gt(a,{out_w}/{out_h}),-2,{out_w})':'if(gt(a,{out_w}/{out_h}),{out_h},-2)',"
        f"crop={out_w}:{out_h}:'max(0,min(iw-{out_w},(iw*{subject_cx:.3f})-({out_w}/2)))':0,"
        f"fps=30,eq=contrast=1.03:brightness=0.01,format=yuv420p"
    )

    subprocess.run(
        [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", raw_clip_path,
            "-t", f"{target_duration:.3f}",
            "-vf", vf,
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            out_segment_path,
        ],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def assemble_final_video(
    segment_paths: list[str],
    audio_path: str,
    srt_path: str,
    burn_in_captions: bool,
    out_mp4_path: str,
):
    concat_txt = out_mp4_path + ".concat.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for seg in segment_paths:
            abs_seg = os.path.abspath(seg).replace("\\", "/")
            f.write(f"file '{abs_seg}'\n")

    if burn_in_captions:
        escaped_srt = os.path.abspath(srt_path).replace("\\", "/").replace(":", "\\:")
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_txt,
                "-i", audio_path,
                "-vf", f"subtitles='{escaped_srt}':force_style='FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2'",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-shortest", out_mp4_path,
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_txt,
                "-i", audio_path,
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-shortest", out_mp4_path,
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    os.remove(concat_txt)