"""Vidéos de test fabriquées par ffmpeg (mire, son, sous-titres, piste audio en plus)."""

import os
import subprocess


def video(path: str, seconds: float, size: str = "320x180", audio_tracks: int = 1, title: str = "") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={seconds}"]
    for i in range(audio_tracks):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={440 + 110 * i}:duration={seconds}"]
    cmd += ["-map", "0:v"]
    for i in range(audio_tracks):
        cmd += ["-map", f"{i + 1}:a"]
    langs = ["fre", "eng", "ger"]
    for i in range(audio_tracks):
        cmd += [f"-metadata:s:a:{i}", f"language={langs[i % 3]}"]
    if title:
        cmd += ["-metadata", f"title={title}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", path]
    subprocess.run(cmd, check=True)
    return path


def sound(path: str, seconds: float) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=duration={seconds}",
                    "-c:a", "libmp3lame", path], check=True)
    return path


def sound_with_cover(path: str, seconds: float) -> str:
    """MP3 avec pochette : mpv la voit comme une piste vidéo « albumart »."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cover = path + ".png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=orange:size=200x200",
                    "-frames:v", "1", cover], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=duration={seconds}",
                    "-i", cover, "-map", "0:a", "-map", "1:v", "-c:a", "libmp3lame", "-c:v", "copy",
                    "-disposition:v", "attached_pic", "-id3v2_version", "3", path], check=True)
    os.remove(cover)
    return path


def subtitles(path: str, text: str = "Bonjour") -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"1\n00:00:00,000 --> 00:00:05,000\n{text}\n")
    return path


def library(root: str) -> dict:
    """Une série (ordre naturel : 2 avant 10), un film long (reprise), un son, un faux fichier."""
    serie = os.path.join(root, "Séries", "La Mer")
    files = {
        "e1": video(os.path.join(serie, "Épisode 1.mp4"), 3),
        "e2": video(os.path.join(serie, "Épisode 2.mp4"), 30, audio_tracks=2),
        "e10": video(os.path.join(serie, "Épisode 10.mp4"), 3),
        "film": video(os.path.join(root, "Films", "Long film.mkv"), 90, size="160x90"),
        "son": sound(os.path.join(root, "Musique", "chanson.mp3"), 3),
        "pochette": sound_with_cover(os.path.join(root, "Musique", "avec pochette.mp3"), 3),
    }
    subtitles(os.path.join(serie, "Épisode 2.fr.srt"))
    fake = os.path.join(root, "Films", "cassé.mp4")
    with open(fake, "w") as fh:
        fh.write("ce n'est pas une vidéo")
    with open(os.path.join(serie, "notes.txt"), "w") as fh:
        fh.write("x")
    files.update(fake=fake, serie=serie, root=root)
    return files


def long_video(path: str, seconds: float, chapters=()) -> str:
    """Vidéo longue mais légère (64 × 36, 1 image / s : quelques secondes à fabriquer), avec des chapitres
    [(début en s, titre)…] — pour « Passer le générique », qui ne propose rien sous 5 minutes."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".nochap.mkv"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=64x36:rate=1:duration={seconds}", "-f", "lavfi", "-i",
                    f"sine=duration={seconds}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "16k", tmp], check=True)
    if not chapters:
        os.replace(tmp, path)
        return path
    meta = path + ".chapters.txt"
    bounds = list(chapters) + [(seconds, None)]
    with open(meta, "w", encoding="utf-8") as fh:
        fh.write(";FFMETADATA1\n")
        for (start, title), (end, _t) in zip(bounds, bounds[1:]):
            fh.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={int(start * 1000)}\nEND={int(end * 1000)}\ntitle={title}\n")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", tmp, "-i", meta, "-map_metadata", "1",
                    "-map_chapters", "1", "-c", "copy", path], check=True)
    os.remove(tmp)
    os.remove(meta)
    return path
