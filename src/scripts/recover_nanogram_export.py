#!/usr/bin/env python3
"""
Recover and manage nanogram-style posts for this local site.

Features:
- Recover posts (caption + media) from a nanogram export (JSON preferred, HTML fallback).
- Recover comment metadata from liked-comments activity (with known limitations).
- Keep a persistent registry of posts for the site.
- Add/remove posts programmatically.
- Auto-sync managed post data into the app HTML.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


DEFAULT_REGISTRY = Path("build/registry.json")
DEFAULT_APP_HTML = Path("src/nanogram.app.html")
DEFAULT_OUT_DIR = Path("build/assets")
DEFAULT_PROFILE_IMAGE = "assets/img/profile.jpg"
DEFAULT_FALLBACK_THUMBNAIL = "assets/img/post.jpg"
DEFAULT_STATIC_ASSETS_DIR = Path("src/img/assets")
DEFAULT_COMMENTER_ASSETS_DIR = Path("build/assets/img/people")
DEFAULT_METADATA_DIR = Path("build/metadata")
DEFAULT_FORMS_FILE = "forms.json"
DEFAULT_GATE_FORM_ID = "cipherkey"
DEFAULT_COMMENT_AVATAR_EMOJIS = [
    "👶",
    "🧒",
    "👦",
    "👧",
    "🧑",
    "👱",
    "👨",
    "🧔",
    "🧔‍♂️",
    "🧔‍♀️",
    "👨‍🦰",
    "👨‍🦱",
    "👨‍🦳",
    "👨‍🦲",
    "👩",
    "👩‍🦰",
    "🧑‍🦰",
    "👩‍🦱",
    "🧑‍🦱",
    "👩‍🦳",
    "🧑‍🦳",
    "👩‍🦲",
    "🧑‍🦲",
    "👱‍♀️",
    "👱‍♂️",
    "🧓",
    "👴",
    "👵",
    "🧏",
    "🧏‍♂️",
    "🧏‍♀️",
    "👳",
    "👳‍♂️",
    "👳‍♀️",
    "👲",
    "🧕",
    "👼",
    "🗣️",
    "👤",
    "👥",
    "🫂",
]
EXPORT_TZ = dt.timezone(dt.timedelta(hours=-8))

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_TEMPLATE_APP_HTML = (PROJECT_ROOT / "src" / "nanogram.app.html").resolve()

MANAGED_START = "<!-- MANAGED:POSTS_DATA:START -->"
MANAGED_END = "<!-- MANAGED:POSTS_DATA:END -->"

POST_BLOCK_RE = re.compile(
    r'<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">\s*'
    r'<h2 class="_3-95 _2pim _a6-h _a6-i">(.*?)</h2>\s*'
    r'<div class="_3-95 _a6-p">(.*?)</div>\s*'
    r'<div class="_3-94 _a6-o">(.*?)</div>\s*'
    r"</div>",
    re.S,
)

REEL_BLOCK_RE = re.compile(
    r'<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">\s*'
    r'(?:<h2 class="_3-95 _2pim _a6-h _a6-i">(.*?)</h2>\s*)?'
    r'<div class="_3-95 _a6-p">(.*?)</div>\s*'
    r'<div class="_3-94 _a6-o">(.*?)</div>\s*'
    r"</div>",
    re.S,
)

MEDIA_REF_RE = re.compile(r'(?:href|src)="(media/[^"]+)"', re.I)
TAG_RE = re.compile(r"<[^>]+>")

LIKED_COMMENTS_RE = re.compile(
    r'<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">\s*'
    r'<h2 class="_3-95 _2pim _a6-h _a6-i">(.*?)</h2>\s*'
    r'<div class="_a6-p">\s*'
    r"<div>\s*"
    r'<div><a[^>]+href="([^"]+)"[^>]*>.*?</a></div>'
    r"<div>(.*?)</div>",
    re.S,
)

POST_COMMENTS_ACTIVITY_RE = re.compile(
    r"Comment<div><div>(.*?)</div></div></td></tr>"
    r'<tr><td colspan="2" class="_2pin _a6_q">Media Owner<div><div>(.*?)</div></div></td></tr>'
    r'<tr><td class="_2pin _a6_q">Time</td><td class="_2pin _2piu _a6_r">(.*?)</td></tr>',
    re.S,
)


def strip_tags(raw: str) -> str:
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    return html.unescape(TAG_RE.sub("", raw)).strip()


def collapse_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_commenter_key(value: Any) -> str:
    text = collapse_spaces(str(value or "")).lower()
    if text.startswith("@"):
        text = text[1:]
    return text.replace(" ", "")


def looks_like_image_source(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if re.match(r"^(?:data:image/|blob:)", text, re.I):
        return True
    if re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I):
        return True
    if text.startswith(("/", "./", "../")):
        return True
    if "/" in text:
        return True
    return bool(re.search(r"\.(?:png|jpe?g|webp|gif|svg|avif|heic)(?:[?#].*)?$", text, re.I))


def parse_export_datetime(raw: str) -> dt.datetime | None:
    text = collapse_spaces(raw)
    for fmt in ("%b %d, %Y %I:%M %p",):
        try:
            return dt.datetime.strptime(text.upper(), fmt)
        except ValueError:
            continue

    m = re.match(
        r"^([A-Za-z]{3})\s+(\d{1,2}),\s+(\d{4})\s+(\d{1,2}):(\d{2})\s+([ap]m)$",
        text,
        re.I,
    )
    if not m:
        return None

    month, day, year, hour, minute, ampm = m.groups()
    try:
        return dt.datetime.strptime(
            f"{month} {int(day):02d}, {year} {int(hour):02d}:{minute} {ampm.upper()}",
            "%b %d, %Y %I:%M %p",
        )
    except ValueError:
        return None


def parse_export_timestamp(raw: Any) -> dt.datetime | None:
    if isinstance(raw, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(raw), tz=EXPORT_TZ)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.isdigit():
            try:
                return dt.datetime.fromtimestamp(float(text), tz=EXPORT_TZ)
            except (OverflowError, OSError, ValueError):
                return None
        parsed = parse_export_datetime(text)
        if parsed is not None:
            return parsed
    return None


def format_export_datetime(value: dt.datetime | None) -> str:
    if value is None:
        return ""
    export_value = value.astimezone(EXPORT_TZ) if value.tzinfo is not None else value
    return f"{export_value.strftime('%b')} {export_value.day}, {export_value.year} {export_value.strftime('%I:%M %p')}"


def decode_export_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    # JSON exports sometimes contain UTF-8 bytes decoded as latin-1.
    if any(ch in text for ch in ("Ã", "â", "ð")):
        try:
            repaired = text.encode("latin-1").decode("utf-8")
            if repaired:
                return repaired
        except UnicodeError:
            pass
    return text


def load_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def media_kind(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}:
        return "image"
    if ext in {".mp4", ".mov", ".m4v", ".webm"}:
        return "video"
    if ext == ".srt":
        return "subtitle"
    return "other"


def dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def slugify(text: str) -> str:
    text = re.sub(r"instagram", "nanogram", text, flags=re.I)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return slug or "post"


def infer_username(export_dir: Path) -> str | None:
    m = re.match(r"instagram-([^-]+)-\d{4}-\d{2}-\d{2}", export_dir.name)
    if m:
        return m.group(1)
    return None


def normalize_profile_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def normalize_profile_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text else None


def extract_string_map_data_value(entry: Any) -> str | None:
    if isinstance(entry, dict):
        for key in ("value", "text", "display", "name"):
            candidate = normalize_profile_text(entry.get(key))
            if candidate:
                return candidate
    return normalize_profile_text(entry)


def iter_json_nodes(value: Any) -> list[Any]:
    nodes: list[Any] = [value]
    out: list[Any] = []
    while nodes:
        current = nodes.pop()
        out.append(current)
        if isinstance(current, dict):
            nodes.extend(current.values())
        elif isinstance(current, list):
            nodes.extend(current)
    return out


def find_profile_text_in_payload(
    payload: Any,
    *,
    string_map_labels: set[str],
    direct_keys: set[str],
) -> str | None:
    normalized_direct = {normalize_profile_label(key) for key in direct_keys}
    for node in iter_json_nodes(payload):
        if not isinstance(node, dict):
            continue

        string_map = node.get("string_map_data")
        if isinstance(string_map, dict):
            for key, raw in string_map.items():
                if normalize_profile_label(key) not in string_map_labels:
                    continue
                value = extract_string_map_data_value(raw)
                if value:
                    return value

        for key, raw in node.items():
            if normalize_profile_label(key) not in normalized_direct:
                continue
            value = extract_string_map_data_value(raw)
            if value:
                return value
    return None


def list_profile_identity_json_candidates(export_dir: Path) -> list[Path]:
    preferred = [
        export_dir / "personal_information" / "personal_information.json",
        export_dir / "personal_information" / "personal_information_1.json",
        export_dir
        / "your_instagram_activity"
        / "personal_information"
        / "personal_information.json",
        export_dir
        / "your_instagram_activity"
        / "account_information"
        / "personal_information.json",
    ]

    extras = sorted(
        path
        for path in export_dir.rglob("personal_information*.json")
        if path.is_file()
    )

    ordered: list[Path] = []
    seen: set[str] = set()
    for path in preferred + extras:
        key = str(path.resolve())
        if key in seen or not path.exists() or not path.is_file():
            continue
        seen.add(key)
        ordered.append(path)
    return ordered


def extract_profile_identity_from_export(export_dir: Path) -> dict[str, str]:
    username_labels = {
        normalize_profile_label("username"),
        normalize_profile_label("instagram username"),
    }
    full_name_labels = {
        normalize_profile_label("name"),
        normalize_profile_label("full name"),
    }

    for candidate in list_profile_identity_json_candidates(export_dir):
        try:
            payload = load_json_file(candidate)
        except (OSError, json.JSONDecodeError):
            continue

        username = find_profile_text_in_payload(
            payload,
            string_map_labels=username_labels,
            direct_keys={"username", "instagram_username"},
        )
        full_name = find_profile_text_in_payload(
            payload,
            string_map_labels=full_name_labels,
            direct_keys={"full_name", "name", "display_name"},
        )

        if username or full_name:
            out: dict[str, str] = {}
            if username:
                out["username"] = username
            if full_name:
                out["full_name"] = full_name
            return out
    return {}


def relationship_entry_count(value: Any) -> int:
    if isinstance(value, list):
        total = 0
        for item in value:
            if isinstance(item, dict):
                string_list = item.get("string_list_data")
                if isinstance(string_list, list) and string_list:
                    total += len([entry for entry in string_list if isinstance(entry, dict)])
                    continue
            total += 1
        return total
    return 0


def relationship_count_from_json(path: Path, relationship_keys: tuple[str, ...]) -> int | None:
    try:
        payload = load_json_file(path)
    except (OSError, json.JSONDecodeError):
        return None

    if isinstance(payload, dict):
        for key in relationship_keys:
            entries = payload.get(key)
            count = relationship_entry_count(entries)
            if count > 0:
                return count
        if "relationships" in payload and isinstance(payload["relationships"], dict):
            relationships = payload["relationships"]
            for key in relationship_keys:
                entries = relationships.get(key)
                count = relationship_entry_count(entries)
                if count > 0:
                    return count
    elif isinstance(payload, list):
        looks_like_relationship_list = any(
            isinstance(item, dict) and isinstance(item.get("string_list_data"), list)
            for item in payload
        )
        if looks_like_relationship_list:
            count = relationship_entry_count(payload)
            if count > 0:
                return count

    return None


def list_relationship_json_candidates(export_dir: Path, prefix: str) -> list[Path]:
    preferred_dirs = [
        export_dir / "connections" / "followers_and_following",
        export_dir / "followers_and_following",
        export_dir
        / "your_instagram_activity"
        / "connections"
        / "followers_and_following",
    ]

    ordered: list[Path] = []
    seen: set[str] = set()

    for directory in preferred_dirs:
        if not directory.exists():
            continue
        for path in sorted(directory.glob(f"{prefix}*.json")):
            key = str(path.resolve())
            if key in seen or not path.is_file():
                continue
            seen.add(key)
            ordered.append(path)

    for path in sorted(export_dir.rglob(f"{prefix}*.json")):
        key = str(path.resolve())
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        ordered.append(path)
    return ordered


def sum_relationship_counts(export_dir: Path, prefix: str, relationship_keys: tuple[str, ...]) -> int | None:
    total = 0
    seen_any = False
    for path in list_relationship_json_candidates(export_dir, prefix):
        count = relationship_count_from_json(path, relationship_keys)
        if count is None:
            continue
        seen_any = True
        total += count
    if not seen_any:
        return None
    return total


def infer_profile_snapshot(export_dir: Path, explicit_username: str | None) -> dict[str, Any]:
    identity = extract_profile_identity_from_export(export_dir)

    username = normalize_profile_text(explicit_username)
    if not username:
        username = identity.get("username") or infer_username(export_dir)

    full_name = normalize_profile_text(identity.get("full_name"))
    followers_count = sum_relationship_counts(
        export_dir,
        "followers",
        ("relationships_followers", "followers"),
    )
    following_count = sum_relationship_counts(
        export_dir,
        "following",
        ("relationships_following", "following"),
    )

    snapshot: dict[str, Any] = {}
    if username:
        snapshot["username"] = username
    if full_name:
        snapshot["full_name"] = full_name
    if followers_count is not None:
        snapshot["followers_count"] = followers_count
    if following_count is not None:
        snapshot["following_count"] = following_count
    return snapshot


def display_date_from_post(posted_at_iso: str | None, posted_at_raw: str | None) -> str:
    if posted_at_iso:
        try:
            value = dt.datetime.fromisoformat(posted_at_iso)
            if value.tzinfo is not None:
                value = value.astimezone(EXPORT_TZ)
            return f"{value.strftime('%B')} {value.day}"
        except ValueError:
            pass
    if posted_at_raw:
        parsed = parse_export_datetime(posted_at_raw)
        if parsed:
            return f"{parsed.strftime('%B')} {parsed.day}"
    return posted_at_raw or f"{dt.date.today().strftime('%B')} {dt.date.today().day}"


def path_to_web_string(path: Path | str) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(Path.cwd().resolve()).as_posix()
        except ValueError:
            return p.as_posix()
    return p.as_posix()


def default_profile_image_for_out_dir(out_dir: Path) -> str:
    return path_to_web_string(out_dir / "img" / "profile.jpg")


def default_fallback_thumbnail_for_out_dir(out_dir: Path) -> str:
    return path_to_web_string(out_dir / "img" / "post.jpg")


def parse_profile_photos_html(profile_html: Path, export_dir: Path) -> list[dict[str, Any]]:
    if not profile_html.exists():
        return []

    text = profile_html.read_text(encoding="utf-8")
    entries: list[dict[str, Any]] = []
    for idx, match in enumerate(REEL_BLOCK_RE.finditer(text), start=1):
        _caption_html, body_html, time_html = match.groups()
        posted_at = collapse_spaces(strip_tags(time_html))
        posted_at_dt = parse_export_datetime(posted_at)

        refs = dedupe_keep_order(MEDIA_REF_RE.findall(body_html))
        profile_refs = [
            rel
            for rel in refs
            if rel.startswith("media/profile/") and media_kind(rel) == "image"
        ]
        if not profile_refs:
            continue

        chosen_rel = profile_refs[0]
        entries.append(
            {
                "relative_path": chosen_rel,
                "posted_at": posted_at,
                "posted_at_iso": posted_at_dt.isoformat() if posted_at_dt else None,
                "exists": (export_dir / chosen_rel).exists(),
                "order": idx,
            }
        )

    return entries


def parse_profile_photos_json(profile_json: Path, export_dir: Path) -> list[dict[str, Any]]:
    if not profile_json.exists():
        return []
    raw = load_json_file(profile_json)

    entries: list[dict[str, Any]] = []
    source_items: list[Any] = []
    if isinstance(raw, dict):
        pictures = raw.get("ig_profile_picture")
        if isinstance(pictures, list):
            source_items.extend(pictures)
    elif isinstance(raw, list):
        source_items.extend(raw)

    for idx, item in enumerate(source_items, start=1):
        if not isinstance(item, dict):
            continue
        uri = item.get("uri")
        if not isinstance(uri, str) or not uri.strip():
            continue
        rel = uri.strip()
        if media_kind(rel) != "image":
            continue
        posted_at_dt = parse_export_timestamp(item.get("creation_timestamp"))
        posted_at = format_export_datetime(posted_at_dt)
        entries.append(
            {
                "relative_path": rel,
                "posted_at": posted_at,
                "posted_at_iso": posted_at_dt.isoformat() if posted_at_dt else None,
                "exists": (export_dir / rel).exists(),
                "order": idx,
            }
        )

    return entries


def find_latest_profile_image(export_dir: Path) -> Path | None:
    profile_json = export_dir / "your_instagram_activity/media/profile_photos.json"
    profile_html = export_dir / "your_instagram_activity/media/profile_photos.html"
    profile_entries = parse_profile_photos_json(profile_json, export_dir)
    if not profile_entries:
        profile_entries = parse_profile_photos_html(profile_html, export_dir)
    if profile_entries:
        existing_entries = [entry for entry in profile_entries if entry.get("exists")]
        if existing_entries:
            def profile_entry_sort_key(entry: dict[str, Any]) -> tuple[float, int]:
                iso = entry.get("posted_at_iso")
                ts = 0.0
                if isinstance(iso, str) and iso:
                    try:
                        ts = dt.datetime.fromisoformat(iso).timestamp()
                    except ValueError:
                        ts = 0.0
                return (ts, int(entry.get("order", 0)))

            chosen = max(existing_entries, key=profile_entry_sort_key)
            return export_dir / str(chosen["relative_path"])

    profile_root = export_dir / "media" / "profile"
    if not profile_root.exists():
        return None

    image_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
    candidates = [
        p
        for p in profile_root.rglob("*")
        if p.is_file() and p.suffix.lower() in image_suffixes
    ]
    if not candidates:
        return None

    def score(path: Path) -> tuple[int, float, str]:
        date_bucket = 0
        for part in path.parts:
            if re.fullmatch(r"\d{6}", part):
                date_bucket = max(date_bucket, int(part))
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (date_bucket, mtime, path.name)

    return max(candidates, key=score)


def copy_profile_image_from_export(export_dir: Path, destination: Path) -> tuple[str | None, str | None]:
    src = find_latest_profile_image(export_dir)
    if src is None:
        return None, None

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, destination)

    try:
        source_rel = str(src.relative_to(export_dir))
    except ValueError:
        source_rel = str(src)

    return path_to_web_string(destination), source_rel


def copy_static_assets(static_assets_dir: Path, destination_dir: Path) -> int:
    if not static_assets_dir.exists() or not static_assets_dir.is_dir():
        return 0

    copied = 0
    for src in sorted(static_assets_dir.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(static_assets_dir)
        dst = destination_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    return copied


def copy_commenter_assets(commenter_assets_dir: Path, destination_dir: Path) -> tuple[dict[str, str], int]:
    if not commenter_assets_dir.exists() or not commenter_assets_dir.is_dir():
        return {}, 0

    source_root = commenter_assets_dir.resolve()
    target_root = destination_dir.resolve()
    source_and_target_match = source_root == target_root

    copied_count = 0
    copied_map: dict[str, str] = {}
    for src in sorted(commenter_assets_dir.rglob("*")):
        if not src.is_file():
            continue
        if media_kind(src.name) != "image":
            continue
        rel = src.relative_to(commenter_assets_dir)
        dst = source_root / rel if source_and_target_match else (target_root / rel)
        if not source_and_target_match:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied_count += 1

        key = normalize_commenter_key(rel.stem)
        if key and key not in copied_map:
            copied_map[key] = path_to_web_string(dst)
    return copied_map, copied_count


def video_thumbnail_path(video_path: Path) -> Path:
    return video_path.with_name(f"{video_path.stem}_thumb.jpg")


def generate_video_thumbnail(video_path: Path, thumbnail_path: Path) -> bool:
    if not video_path.exists() or video_path.suffix.lower() not in {".mp4", ".mov", ".m4v", ".webm"}:
        return False

    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)

    # Try slightly into the clip first (better than black intro frame), then fallback to first frame.
    commands = [
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            "00:00:00.500",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(thumbnail_path),
        ],
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(thumbnail_path),
        ],
    ]

    for cmd in commands:
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if completed.returncode == 0 and thumbnail_path.exists():
            return True

    return False


def build_media_list_from_uris(
    uris: list[str],
    export_dir: Path,
) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    for rel in dedupe_keep_order(uris):
        kind = media_kind(rel)
        if kind == "other":
            continue
        media.append(
            {
                "relative_path": rel,
                "kind": kind,
                "exists": (export_dir / rel).exists(),
            }
        )
    return media


def extract_media_uris_from_media_entry(media_entry: dict[str, Any]) -> list[str]:
    uris: list[str] = []
    uri = media_entry.get("uri")
    if isinstance(uri, str) and uri.strip():
        uris.append(uri.strip())

    media_metadata = media_entry.get("media_metadata")
    if isinstance(media_metadata, dict):
        video_metadata = media_metadata.get("video_metadata")
        if isinstance(video_metadata, dict):
            subtitles = video_metadata.get("subtitles")
            if isinstance(subtitles, dict):
                subtitle_uri = subtitles.get("uri")
                if isinstance(subtitle_uri, str) and subtitle_uri.strip():
                    uris.append(subtitle_uri.strip())

    return uris


def extract_media_uris_from_media_list(media_list: Any) -> list[str]:
    uris: list[str] = []
    if not isinstance(media_list, list):
        return uris
    for media_entry in media_list:
        if not isinstance(media_entry, dict):
            continue
        uris.extend(extract_media_uris_from_media_entry(media_entry))
    return dedupe_keep_order(uris)


def parse_posts_html(posts_html: Path, export_dir: Path) -> list[dict[str, Any]]:
    text = posts_html.read_text(encoding="utf-8")
    posts: list[dict[str, Any]] = []
    for match in POST_BLOCK_RE.finditer(text):
        caption_html, body_html, time_html = match.groups()
        caption = strip_tags(caption_html)
        posted_at = collapse_spaces(strip_tags(time_html))
        posted_at_dt = parse_export_datetime(posted_at)
        refs = dedupe_keep_order(MEDIA_REF_RE.findall(body_html))

        posts.append(
            {
                "caption": caption,
                "posted_at": posted_at,
                "posted_at_iso": posted_at_dt.isoformat() if posted_at_dt else None,
                "source_file": str(posts_html.relative_to(export_dir)),
                "media": build_media_list_from_uris(refs, export_dir),
                "other_people_comments": [],
            }
        )
    return posts


def parse_posts_json(posts_json: Path, export_dir: Path) -> list[dict[str, Any]]:
    raw = load_json_file(posts_json)
    if not isinstance(raw, list):
        return []

    posts: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue

        media_list = entry.get("media")
        refs = extract_media_uris_from_media_list(media_list)
        media = build_media_list_from_uris(refs, export_dir)

        caption = decode_export_text(entry.get("title"))
        if not caption and isinstance(media_list, list):
            for media_entry in media_list:
                if not isinstance(media_entry, dict):
                    continue
                media_title = decode_export_text(media_entry.get("title"))
                if media_title:
                    caption = media_title
                    break

        posted_at_dt = parse_export_timestamp(entry.get("creation_timestamp"))
        if posted_at_dt is None and isinstance(media_list, list):
            for media_entry in media_list:
                if not isinstance(media_entry, dict):
                    continue
                posted_at_dt = parse_export_timestamp(media_entry.get("creation_timestamp"))
                if posted_at_dt is not None:
                    break
        posted_at = format_export_datetime(posted_at_dt)

        posts.append(
            {
                "caption": caption,
                "posted_at": posted_at,
                "posted_at_iso": posted_at_dt.isoformat() if posted_at_dt else None,
                "source_file": str(posts_json.relative_to(export_dir)),
                "media": media,
                "other_people_comments": [],
            }
        )
    return posts


def parse_reels_html(reels_html: Path, export_dir: Path) -> list[dict[str, Any]]:
    text = reels_html.read_text(encoding="utf-8")
    reels: list[dict[str, Any]] = []
    for match in REEL_BLOCK_RE.finditer(text):
        caption_html, body_html, time_html = match.groups()
        caption = strip_tags(caption_html or "")
        posted_at = collapse_spaces(strip_tags(time_html))
        posted_at_dt = parse_export_datetime(posted_at)
        refs = dedupe_keep_order(MEDIA_REF_RE.findall(body_html))
        media = build_media_list_from_uris(refs, export_dir)
        if not media:
            continue

        reels.append(
            {
                "caption": caption,
                "posted_at": posted_at,
                "posted_at_iso": posted_at_dt.isoformat() if posted_at_dt else None,
                "source_file": str(reels_html.relative_to(export_dir)),
                "media": media,
                "other_people_comments": [],
            }
        )
    return reels


def parse_reels_json(reels_json: Path, export_dir: Path) -> list[dict[str, Any]]:
    raw = load_json_file(reels_json)
    reels_block: list[Any] = []
    if isinstance(raw, dict):
        block = raw.get("ig_reels_media")
        if isinstance(block, list):
            reels_block = block
    elif isinstance(raw, list):
        reels_block = raw

    reels: list[dict[str, Any]] = []
    for entry in reels_block:
        if not isinstance(entry, dict):
            continue
        media_list = entry.get("media")
        if not isinstance(media_list, list):
            continue

        refs = extract_media_uris_from_media_list(media_list)
        media = build_media_list_from_uris(refs, export_dir)
        if not media:
            continue

        caption = decode_export_text(entry.get("title"))
        if not caption:
            for media_entry in media_list:
                if not isinstance(media_entry, dict):
                    continue
                media_title = decode_export_text(media_entry.get("title"))
                if media_title:
                    caption = media_title
                    break

        posted_at_dt = parse_export_timestamp(entry.get("creation_timestamp"))
        if posted_at_dt is None:
            for media_entry in media_list:
                if not isinstance(media_entry, dict):
                    continue
                posted_at_dt = parse_export_timestamp(media_entry.get("creation_timestamp"))
                if posted_at_dt is not None:
                    break
        posted_at = format_export_datetime(posted_at_dt)

        reels.append(
            {
                "caption": caption,
                "posted_at": posted_at,
                "posted_at_iso": posted_at_dt.isoformat() if posted_at_dt else None,
                "source_file": str(reels_json.relative_to(export_dir)),
                "media": media,
                "other_people_comments": [],
            }
        )
    return reels


def parse_liked_comments_html(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    items: list[dict[str, Any]] = []
    for commenter_html, url, time_html in LIKED_COMMENTS_RE.findall(text):
        commenter = collapse_spaces(strip_tags(commenter_html))
        liked_at = collapse_spaces(strip_tags(time_html))
        liked_at_dt = parse_export_datetime(liked_at)
        items.append(
            {
                "commenter": commenter,
                "post_url": url,
                "liked_at": liked_at,
                "liked_at_iso": liked_at_dt.isoformat() if liked_at_dt else None,
            }
        )
    return items


def parse_liked_comments_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = load_json_file(path)
    if not isinstance(raw, dict):
        return []

    records = raw.get("likes_comment_likes")
    if not isinstance(records, list):
        return []

    items: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        commenter = decode_export_text(record.get("title")) or "user"
        entries = record.get("string_list_data")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            url = entry.get("href")
            if not isinstance(url, str) or not url.strip():
                continue
            liked_at_dt = parse_export_timestamp(entry.get("timestamp"))
            liked_at = format_export_datetime(liked_at_dt)
            items.append(
                {
                    "commenter": commenter,
                    "post_url": url.strip(),
                    "liked_at": liked_at,
                    "liked_at_iso": liked_at_dt.isoformat() if liked_at_dt else None,
                }
            )
    return items


def parse_post_comments_activity_html(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    items: list[dict[str, str]] = []
    for raw_comment, raw_owner, raw_time in POST_COMMENTS_ACTIVITY_RE.findall(text):
        items.append(
            {
                "comment_text": collapse_spaces(strip_tags(raw_comment)),
                "media_owner": collapse_spaces(strip_tags(raw_owner)),
                "time": collapse_spaces(strip_tags(raw_time)),
            }
        )
    return items


def parse_post_comments_activity_json(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    raw = load_json_file(path)
    if not isinstance(raw, list):
        return []

    items: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        string_map = entry.get("string_map_data")
        if not isinstance(string_map, dict):
            continue

        comment_text = ""
        media_owner = ""
        comment_field = string_map.get("Comment")
        if isinstance(comment_field, dict):
            comment_text = decode_export_text(comment_field.get("value"))
        owner_field = string_map.get("Media Owner")
        if isinstance(owner_field, dict):
            media_owner = decode_export_text(owner_field.get("value"))

        time_field = string_map.get("Time")
        timestamp: Any = None
        if isinstance(time_field, dict):
            timestamp = time_field.get("timestamp")
            if timestamp is None:
                timestamp = time_field.get("value")
        posted_at_dt = parse_export_timestamp(timestamp)
        time_text = format_export_datetime(posted_at_dt)

        items.append(
            {
                "comment_text": collapse_spaces(comment_text),
                "media_owner": collapse_spaces(media_owner),
                "time": collapse_spaces(time_text),
            }
        )
    return items


def assign_liked_comments_to_posts(
    posts: list[dict[str, Any]],
    liked_comments: list[dict[str, Any]],
    max_days: int = 7,
) -> list[dict[str, Any]]:
    unresolved: list[dict[str, Any]] = []
    parsed_post_times: list[tuple[int, dt.datetime]] = []
    for idx, post in enumerate(posts):
        iso = post.get("posted_at_iso")
        if not iso:
            continue
        try:
            parsed_post_times.append((idx, dt.datetime.fromisoformat(iso)))
        except ValueError:
            continue

    for item in liked_comments:
        liked_at_iso = item.get("liked_at_iso")
        if not liked_at_iso or not parsed_post_times:
            unresolved.append(item)
            continue
        try:
            liked_dt = dt.datetime.fromisoformat(liked_at_iso)
        except ValueError:
            unresolved.append(item)
            continue

        best_idx: int | None = None
        best_diff = float("inf")
        for idx, post_dt in parsed_post_times:
            diff_hours = abs((liked_dt - post_dt).total_seconds()) / 3600.0
            if diff_hours < best_diff:
                best_diff = diff_hours
                best_idx = idx

        if best_idx is None or best_diff > (max_days * 24):
            unresolved.append(item)
            continue

        enriched = dict(item)
        enriched["mapping"] = {
            "method": "nearest_post_time",
            "time_delta_hours": round(best_diff, 2),
        }
        posts[best_idx]["other_people_comments"].append(enriched)

    return unresolved


def select_posts(
    posts: list[dict[str, Any]],
    post_index: int | None,
    caption_filter: str | None,
) -> list[tuple[int, dict[str, Any]]]:
    indexed = list(enumerate(posts, start=1))
    if post_index is not None:
        indexed = [p for p in indexed if p[0] == post_index]
    if caption_filter:
        needle = caption_filter.casefold()
        indexed = [p for p in indexed if needle in p[1]["caption"].casefold()]
    return indexed


def sort_posts_export_paths(paths: list[Path]) -> list[Path]:
    def sort_key(path: Path) -> tuple[int, str]:
        m = re.match(r"posts_(\d+)\.(json|html)$", path.name)
        if not m:
            return (10_000_000, path.name)
        return (int(m.group(1)), path.name)

    return sorted(paths, key=sort_key)


def stable_post_id(post: dict[str, Any], kind: str) -> str:
    media_items = post.get("media")
    media_refs: list[str] = []
    if isinstance(media_items, list):
        for item in media_items:
            if not isinstance(item, dict):
                continue
            rel = item.get("relative_path")
            if isinstance(rel, str) and rel.strip():
                media_refs.append(rel.strip())

    payload = {
        "kind": kind,
        "source_file": str(post.get("source_file") or ""),
        "posted_at_iso": str(post.get("posted_at_iso") or ""),
        "media": media_refs,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    prefix = "post" if kind == "post" else "reel"
    return f"{prefix}_{digest}"


def parse_metadata_comments(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    comments: dict[str, str] = {}
    for key, raw_text in value.items():
        username = str(key).strip()
        text = str(raw_text).strip() if not isinstance(raw_text, str) else raw_text.strip()
        if not username or not text:
            continue
        comments[username] = text
    return comments


def parse_metadata_comment_avatars(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    avatars: dict[str, str] = {}
    for key, raw_avatar in value.items():
        username = str(key).strip()
        avatar = str(raw_avatar).strip() if not isinstance(raw_avatar, str) else raw_avatar.strip()
        if not username or not avatar:
            continue
        avatars[username] = avatar
    return avatars


def normalize_visibility(value: Any) -> str:
    text = str(value).strip().lower() if value is not None else ""
    if text in {"public", "private"}:
        return text
    return "private"


def metadata_collection_dir_name(kind: str) -> str:
    return "reels" if str(kind).strip().lower() == "reel" else "posts"


def metadata_paths_for_id(metadata_dir: Path, post_id: str, kind: str) -> tuple[Path, Path]:
    scoped_path = metadata_dir / metadata_collection_dir_name(kind) / f"{post_id}.json"
    legacy_path = metadata_dir / f"{post_id}.json"
    return scoped_path, legacy_path


def resolve_metadata_path(metadata_dir: Path, post_id: str, kind: str) -> Path:
    scoped_path, legacy_path = metadata_paths_for_id(metadata_dir, post_id, kind)
    if scoped_path.exists():
        return scoped_path
    if legacy_path.exists():
        return legacy_path
    return scoped_path


def form_id_from_post_id(post_id: str) -> str:
    raw = str(post_id or "").strip()
    if raw.startswith("post_"):
        suffix = raw[len("post_"):].strip()
        return f"post-{suffix}" if suffix else "post"
    if raw.startswith("reel_"):
        suffix = raw[len("reel_"):].strip()
        return f"reel-{suffix}" if suffix else "reel"
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw).strip("-").lower()
    return f"post-{cleaned}" if cleaned else "post-unknown"


def normalize_form_action_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except Exception:
        return text
    host = parsed.netloc.lower()
    path = parsed.path or ""
    if host == "docs.google.com" and "/forms/d/e/" in path:
        normalized_path = re.sub(r"/viewform/?$", "/formResponse", path, flags=re.I)
        scheme = parsed.scheme or "https"
        return urlunsplit((scheme, parsed.netloc, normalized_path, "", ""))
    return text


def default_gate_form_config() -> dict[str, str]:
    return {
        "type": "login",
        "form-id": DEFAULT_GATE_FORM_ID,
        "form-link": "",
        "variable": "",
    }


def default_posts_form_config() -> dict[str, str]:
    return {
        "type": "posts",
        "form-link": "",
        "post-id-var": "",
        "alias-var": "",
        "avatar-var": "",
        "comment-var": "",
    }


def parse_forms_payload(payload: Any) -> tuple[dict[str, str], dict[str, str], bool, bool]:
    gate = default_gate_form_config()
    posts_form = default_posts_form_config()
    gate_found = False
    posts_form_found = False
    legacy_posts_candidate: dict[str, str] | None = None

    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue

            entry_type = str(item.get("type") or "").strip().lower()
            form_id = str(item.get("form-id") or "").strip()
            if entry_type == "login" or form_id == DEFAULT_GATE_FORM_ID:
                gate_found = True
                gate["form-link"] = normalize_form_action_url(item.get("form-link"))
                gate["variable"] = str(item.get("variable") or "").strip()
                continue

            has_new_posts_keys = (
                ("post-id-var" in item)
                or ("comment-var" in item)
                or ("alias-var" in item)
                or ("avatar-var" in item)
                or ("emoji-var" in item)
            )
            if (entry_type == "posts" or has_new_posts_keys) and not posts_form_found:
                posts_form_found = True
                posts_form["form-link"] = normalize_form_action_url(item.get("form-link"))
                posts_form["post-id-var"] = str(item.get("post-id-var") or "").strip()
                posts_form["alias-var"] = str(item.get("alias-var") or "").strip()
                posts_form["avatar-var"] = str(item.get("avatar-var") or item.get("emoji-var") or "").strip()
                posts_form["comment-var"] = str(item.get("comment-var") or "").strip()
                continue

            if posts_form_found:
                continue

            # Backward compatibility with old schema entries:
            # {"form-id":"post-...","form-link":"...","variable":"entry.X"}
            legacy_form_link = normalize_form_action_url(item.get("form-link"))
            legacy_comment_var = str(item.get("comment-var") or item.get("variable") or "").strip()
            legacy_post_id_var = str(item.get("post-id-var") or "").strip()
            legacy_alias_var = str(item.get("alias-var") or "").strip()
            legacy_avatar_var = str(item.get("avatar-var") or item.get("emoji-var") or "").strip()
            if form_id and form_id != DEFAULT_GATE_FORM_ID:
                legacy_posts_candidate = {
                    "form-link": legacy_form_link,
                    "post-id-var": legacy_post_id_var,
                    "alias-var": legacy_alias_var,
                    "avatar-var": legacy_avatar_var,
                    "comment-var": legacy_comment_var,
                }
            elif legacy_form_link or legacy_comment_var or legacy_post_id_var or legacy_alias_var or legacy_avatar_var:
                legacy_posts_candidate = {
                    "form-link": legacy_form_link,
                    "post-id-var": legacy_post_id_var,
                    "alias-var": legacy_alias_var,
                    "avatar-var": legacy_avatar_var,
                    "comment-var": legacy_comment_var,
                }

    if (not posts_form_found) and legacy_posts_candidate is not None:
        posts_form_found = True
        posts_form["form-link"] = legacy_posts_candidate["form-link"]
        posts_form["post-id-var"] = legacy_posts_candidate["post-id-var"]
        posts_form["alias-var"] = legacy_posts_candidate["alias-var"]
        posts_form["avatar-var"] = legacy_posts_candidate["avatar-var"]
        posts_form["comment-var"] = legacy_posts_candidate["comment-var"]

    return gate, posts_form, gate_found, posts_form_found


def forms_config_entries(gate: dict[str, str], posts_form: dict[str, str]) -> list[dict[str, str]]:
    return [
        {
            "type": "login",
            "form-id": DEFAULT_GATE_FORM_ID,
            "form-link": str(gate.get("form-link") or "").strip(),
            "variable": str(gate.get("variable") or "").strip(),
        },
        {
            "type": "posts",
            "form-link": str(posts_form.get("form-link") or "").strip(),
            "post-id-var": str(posts_form.get("post-id-var") or "").strip(),
            "alias-var": str(posts_form.get("alias-var") or "").strip(),
            "avatar-var": str(posts_form.get("avatar-var") or "").strip(),
            "comment-var": str(posts_form.get("comment-var") or "").strip(),
        },
    ]


def ensure_forms_config(metadata_dir: Path) -> tuple[Path, dict[str, str], dict[str, str], bool, int]:
    forms_path = metadata_dir / DEFAULT_FORMS_FILE
    gate = default_gate_form_config()
    posts_form = default_posts_form_config()
    gate_found = False
    posts_form_found = False

    if forms_path.exists():
        try:
            loaded = json.loads(forms_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            loaded = []
        gate, posts_form, gate_found, posts_form_found = parse_forms_payload(loaded)

    normalized_entries = forms_config_entries(gate, posts_form)
    rendered = json.dumps(normalized_entries, ensure_ascii=False, indent=2) + "\n"

    wrote = True
    if forms_path.exists():
        try:
            existing = forms_path.read_text(encoding="utf-8")
            wrote = existing != rendered
        except OSError:
            wrote = True
    if wrote:
        forms_path.write_text(rendered, encoding="utf-8")

    added_entries = (0 if gate_found else 1) + (0 if posts_form_found else 1)
    return forms_path, gate, posts_form, wrote, added_entries


def apply_forms_to_registry(registry: dict[str, Any], posts_form: dict[str, str]) -> bool:
    shared_form_link = normalize_form_action_url(posts_form.get("form-link"))
    shared_post_id_var = str(posts_form.get("post-id-var") or "").strip()
    shared_alias_var = str(posts_form.get("alias-var") or "").strip()
    shared_avatar_var = str(posts_form.get("avatar-var") or posts_form.get("emoji-var") or "").strip()
    shared_comment_var = str(posts_form.get("comment-var") or "").strip()

    changed = False
    for feed in ("posts", "reels"):
        items = registry.get(feed)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            post_id = str(item.get("post_id") or item.get("id") or "").strip()
            if not post_id:
                continue
            form_id = form_id_from_post_id(post_id)
            if item.get("form_id") != form_id:
                item["form_id"] = form_id
                changed = True
            if item.get("form_link") != shared_form_link:
                item["form_link"] = shared_form_link
                changed = True
            if item.get("post_id_var") != shared_post_id_var:
                item["post_id_var"] = shared_post_id_var
                changed = True
            if item.get("comment_var") != shared_comment_var:
                item["comment_var"] = shared_comment_var
                changed = True
            if item.get("alias_var") != shared_alias_var:
                item["alias_var"] = shared_alias_var
                changed = True
            if item.get("avatar_var") != shared_avatar_var:
                item["avatar_var"] = shared_avatar_var
                changed = True
            if "emoji_var" in item:
                item.pop("emoji_var", None)
                changed = True
            if "form_variable" in item:
                item.pop("form_variable", None)
                changed = True
    return changed


def normalize_comment_avatar_emojis(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_text = value.strip()
        if not raw_text:
            return list(DEFAULT_COMMENT_AVATAR_EMOJIS)
        parsed_items: Any = None
        if raw_text.startswith("["):
            try:
                parsed_items = json.loads(raw_text)
            except json.JSONDecodeError:
                parsed_items = None
        if isinstance(parsed_items, list):
            raw_items = parsed_items
        else:
            raw_items = re.split(r"[\n,|]+", raw_text)
    else:
        return list(DEFAULT_COMMENT_AVATAR_EMOJIS)

    seen: set[str] = set()
    normalized: list[str] = []
    for item in raw_items:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized if normalized else list(DEFAULT_COMMENT_AVATAR_EMOJIS)


def pick_deterministic_avatar_emoji(
    commenter: str,
    text: str,
    avatar_emojis: list[str],
) -> str:
    pool = avatar_emojis if avatar_emojis else list(DEFAULT_COMMENT_AVATAR_EMOJIS)
    if not pool:
        return "🧑"

    seed = f"{commenter}|{text}".encode("utf-8", errors="ignore")
    digest = hashlib.sha256(seed).digest()
    index = int.from_bytes(digest[:4], byteorder="big", signed=False) % len(pool)
    return pool[index]


def create_metadata_template_if_missing(
    metadata_dir: Path,
    post_id: str,
    caption: str,
    kind: str,
    source_file: str,
    seed_comment_usernames: list[str],
) -> tuple[Path, bool]:
    metadata_path = resolve_metadata_path(metadata_dir, post_id, kind)
    if metadata_path.exists():
        return metadata_path, False

    seed_comments: dict[str, str] = {}
    for username in seed_comment_usernames:
        key = str(username).strip()
        if not key or key in seed_comments:
            continue
        seed_comments[key] = "(Comment text unavailable in export)"

    template = {
        "post_id": post_id,
        "kind": kind,
        "caption": caption,
        "source_file": source_file,
        "visibility": "private",
        "like_count": None,
        "comments": seed_comments,
        "_note": (
            "Set comments as {\"username\": \"comment text\"}. "
            "Templates were seeded from commenter usernames available in export."
        ),
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(template, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata_path, True


def load_post_metadata(
    metadata_dir: Path,
    post_id: str,
    caption: str,
    kind: str,
    source_file: str,
    allow_template_create: bool,
    seed_comment_usernames: list[str],
) -> tuple[dict[str, Any], Path, bool]:
    metadata_path = resolve_metadata_path(metadata_dir, post_id, kind)
    created = False
    if allow_template_create and not metadata_path.exists():
        metadata_path, created = create_metadata_template_if_missing(
            metadata_dir=metadata_dir,
            post_id=post_id,
            caption=caption,
            kind=kind,
            source_file=source_file,
            seed_comment_usernames=seed_comment_usernames,
        )

    metadata: dict[str, Any] = {
        "visibility": "private",
        "comments": {},
        "comment_avatars": {},
        "like_count": None,
    }

    if not metadata_path.exists():
        return metadata, metadata_path, False

    try:
        loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            visibility_raw = loaded.get("visibility")
            metadata["visibility"] = normalize_visibility(visibility_raw)
            metadata["comments"] = parse_metadata_comments(loaded.get("comments"))
            metadata["comment_avatars"] = parse_metadata_comment_avatars(
                loaded.get("comment_avatars")
            )
            metadata["like_count"] = coerce_non_negative_int(loaded.get("like_count"))
    except (json.JSONDecodeError, OSError):
        pass

    return metadata, metadata_path, created


def write_post_output(
    export_dir: Path,
    out_posts_dir: Path,
    index_1based: int,
    post: dict[str, Any],
    post_id: str,
    metadata_dir: Path,
    allow_metadata_create: bool,
    post_kind: str,
    collection_name: str = "posts",
) -> dict[str, Any]:
    timestamp_slug = "unknown-time"
    if post.get("posted_at_iso"):
        try:
            ts = dt.datetime.fromisoformat(post["posted_at_iso"])
            if ts.tzinfo is not None:
                ts = ts.astimezone(EXPORT_TZ)
            timestamp_slug = ts.strftime("%Y-%m-%d_%H%M")
        except ValueError:
            pass

    caption_slug = slugify(post["caption"])
    if len(caption_slug) > 80:
        caption_slug = caption_slug[:80].rstrip("-")
    if not caption_slug:
        caption_slug = "post"
    folder_name = f"{index_1based:02d}_{caption_slug}_{timestamp_slug}"
    post_dir = out_posts_dir / folder_name
    media_dir = post_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    seed_comment_usernames: list[str] = []
    raw_other_comments = post.get("other_people_comments")
    if isinstance(raw_other_comments, list):
        for item in raw_other_comments:
            if not isinstance(item, dict):
                continue
            commenter = str(item.get("commenter") or "").strip()
            if commenter:
                seed_comment_usernames.append(commenter)

    metadata, metadata_file_path, metadata_template_created = load_post_metadata(
        metadata_dir=metadata_dir,
        post_id=post_id,
        caption=str(post.get("caption") or ""),
        kind=post_kind,
        source_file=str(post.get("source_file") or ""),
        allow_template_create=allow_metadata_create,
        seed_comment_usernames=seed_comment_usernames,
    )
    manual_like_count = coerce_non_negative_int(metadata.get("like_count"))
    manual_comments = (
        metadata.get("comments")
        if isinstance(metadata.get("comments"), dict)
        else {}
    )
    manual_comment_avatars = (
        metadata.get("comment_avatars")
        if isinstance(metadata.get("comment_avatars"), dict)
        else {}
    )
    visibility = normalize_visibility(metadata.get("visibility"))

    recovered_media: list[dict[str, Any]] = []
    media_counter = 0
    for media in post["media"]:
        media_counter += 1
        src = export_dir / media["relative_path"]
        suffix = src.suffix.lower()
        out_name = f"{media_counter:02d}_{src.stem}{suffix}"
        dst = media_dir / out_name
        copied = False
        if src.exists():
            shutil.copy2(src, dst)
            copied = True
        item: dict[str, Any] = {
            "kind": media["kind"],
            "source_relative_path": media["relative_path"],
            "recovered_relative_path": str(
                Path(collection_name) / folder_name / "media" / out_name
            ),
            "copied": copied,
        }
        if copied and media["kind"] == "video":
            thumb_path = video_thumbnail_path(dst)
            if generate_video_thumbnail(dst, thumb_path):
                item["thumbnail_relative_path"] = str(
                    Path(collection_name) / folder_name / "media" / thumb_path.name
                )
        recovered_media.append(item)

    description = [
        f"Caption: {post['caption']}",
        f"Posted at: {post['posted_at']}",
        f"Post ID: {post_id}",
        f"Metadata file: {metadata_file_path}",
        f"Media files recovered: {sum(1 for m in recovered_media if m['copied'])}/{len(recovered_media)}",
        f"Visibility: {visibility}",
        f"Metadata like_count: {manual_like_count if manual_like_count is not None else ''}",
        f"Metadata comments: {len(manual_comments)}",
        f"Metadata comment avatars: {len(manual_comment_avatars)}",
    ]
    (post_dir / "description.txt").write_text("\n".join(description) + "\n", encoding="utf-8")

    post_json = {
        "post_id": post_id,
        "visibility": visibility,
        "caption": post["caption"],
        "posted_at": post["posted_at"],
        "posted_at_iso": post.get("posted_at_iso"),
        "source_file": post["source_file"],
        "media": recovered_media,
        "comments": manual_comments,
        "comment_avatars": manual_comment_avatars,
        "other_people_comments": post.get("other_people_comments", []),
        "note": (
            "Other-people comments are inferred from liked-comments activity. "
            "This export does not include full comment text for those entries."
        ),
    }
    if manual_like_count is not None:
        post_json["like_count"] = manual_like_count

    (post_dir / "post.json").write_text(
        json.dumps(post_json, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return {
        "index": index_1based,
        "folder": str(Path(collection_name) / folder_name),
        "caption": post["caption"],
        "posted_at": post["posted_at"],
        "media_count": len(recovered_media),
        "post_id": post_id,
        "visibility": visibility,
        "metadata_file": path_to_web_string(metadata_file_path),
        "metadata_template_created": metadata_template_created,
        "metadata_comment_count": len(manual_comments),
        "metadata_like_count": manual_like_count,
        "comments_by_others_count": len(post.get("other_people_comments", [])),
        "post_json": post_json,
    }


def default_registry() -> dict[str, Any]:
    return {
        "version": 1,
        "profile": {
            "username": "profile_user",
            "full_name": "Profile User",
            "posts_count": 0,
            "followers_count": 0,
            "following_count": 0,
            "profile_image": DEFAULT_PROFILE_IMAGE,
        },
        "comment_avatar_emojis": list(DEFAULT_COMMENT_AVATAR_EMOJIS),
        "posts": [],
        "reels": [],
    }


def normalize_registry(data: dict[str, Any]) -> dict[str, Any]:
    registry = default_registry()
    if not isinstance(data, dict):
        return registry

    profile = data.get("profile")
    if isinstance(profile, dict):
        username = profile.get("username")
        full_name = profile.get("full_name")
        posts_count = profile.get("posts_count")
        followers_count = profile.get("followers_count")
        following_count = profile.get("following_count")
        profile_image = profile.get("profile_image")
        if isinstance(username, str) and username.strip():
            registry["profile"]["username"] = username.strip()
        if isinstance(full_name, str) and full_name.strip():
            registry["profile"]["full_name"] = full_name.strip()
        normalized_posts = coerce_non_negative_int(posts_count)
        if normalized_posts is not None:
            registry["profile"]["posts_count"] = normalized_posts
        normalized_followers = coerce_non_negative_int(followers_count)
        if normalized_followers is not None:
            registry["profile"]["followers_count"] = normalized_followers
        normalized_following = coerce_non_negative_int(following_count)
        if normalized_following is not None:
            registry["profile"]["following_count"] = normalized_following
        if isinstance(profile_image, str) and profile_image.strip():
            registry["profile"]["profile_image"] = profile_image.strip()

    posts = data.get("posts")
    if isinstance(posts, list):
        registry["posts"] = [p for p in posts if isinstance(p, dict)]
    reels = data.get("reels")
    if isinstance(reels, list):
        registry["reels"] = [r for r in reels if isinstance(r, dict)]

    version = data.get("version")
    if isinstance(version, int) and version > 0:
        registry["version"] = version
    registry["comment_avatar_emojis"] = normalize_comment_avatar_emojis(
        data.get("comment_avatar_emojis")
    )

    return registry


def load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_registry()
    raw = json.loads(path.read_text(encoding="utf-8"))
    return normalize_registry(raw)


def save_registry(path: Path, registry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_managed_json_payload(registry: dict[str, Any]) -> str:
    payload = {
        "profile": registry.get("profile", {}),
        "comment_avatar_emojis": normalize_comment_avatar_emojis(
            registry.get("comment_avatar_emojis")
        ),
        "posts": registry.get("posts", []),
        "reels": registry.get("reels", []),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return text.replace("</", "<\\/")


def build_managed_block(registry: dict[str, Any]) -> str:
    payload = render_managed_json_payload(registry)
    return (
        f"  {MANAGED_START}\n"
        "  <script id=\"sitePostsData\" type=\"application/json\">\n"
        f"{payload}\n"
        "  </script>\n"
        f"  {MANAGED_END}"
    )


def count_total_profile_posts(registry: dict[str, Any]) -> int:
    posts = registry.get("posts", [])
    reels = registry.get("reels", [])
    posts_count = len(posts) if isinstance(posts, list) else 0
    reels_count = len(reels) if isinstance(reels, list) else 0
    return posts_count + reels_count


def sync_profile_stats_post_count(html_text: str, total_posts: int) -> str:
    pattern = re.compile(
        r"(<li>\s*<strong[^>]*>)(\d+)(</strong>\s*posts\s*</li>)",
        re.I,
    )
    return pattern.sub(rf"\g<1>{total_posts}\g<3>", html_text, count=1)


def sync_app_html_from_registry(app_html: Path, registry: dict[str, Any]) -> None:
    resolved_target = app_html.expanduser().resolve(strict=False)
    if resolved_target == SOURCE_TEMPLATE_APP_HTML:
        raise RuntimeError(
            "Refusing to sync managed data into src/nanogram.app.html. "
            "Use --app-html with a generated/build path."
        )

    if not app_html.exists():
        raise FileNotFoundError(f"Missing nanogram HTML file: {app_html}")

    text = app_html.read_text(encoding="utf-8")
    block = build_managed_block(registry)

    block_re = re.compile(
        rf"{re.escape(MANAGED_START)}.*?{re.escape(MANAGED_END)}",
        re.S,
    )

    if block_re.search(text):
        # Use callable replacement so JSON escape sequences (e.g. "\\n")
        # are not interpreted by the regex engine.
        updated = block_re.sub(lambda _m: block, text, count=1)
    else:
        anchor = "</body>"
        if anchor not in text:
            raise RuntimeError(
                "Could not find </body> in nanogram HTML to insert managed posts block."
            )
        updated = text.replace(anchor, f"\n{block}\n\n{anchor}", 1)

    updated = sync_profile_stats_post_count(
        updated,
        count_total_profile_posts(registry),
    )

    app_html.write_text(updated, encoding="utf-8")


def ensure_unique_post_id(
    registry: dict[str, Any],
    preferred: str | None = None,
    used_ids: set[str] | None = None,
) -> str:
    existing = {str(post.get("id", "")) for post in registry.get("posts", [])}
    existing.update({str(reel.get("id", "")) for reel in registry.get("reels", [])})
    if used_ids:
        existing.update(used_ids)

    if preferred:
        candidate = slugify(preferred).replace("-", "_")
        if candidate and candidate not in existing:
            if used_ids is not None:
                used_ids.add(candidate)
            return candidate

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"post_{stamp}"
    candidate = base
    counter = 2
    while candidate in existing:
        candidate = f"{base}_{counter}"
        counter += 1
    if used_ids is not None:
        used_ids.add(candidate)
    return candidate


def parse_comment_arg(raw: str) -> dict[str, Any]:
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) < 2:
        raise ValueError(
            "Comment format must be: user|text|time|likes (time/likes optional)"
        )

    user = parts[0] or "user"
    text = parts[1]
    time = parts[2] if len(parts) >= 3 else ""
    likes = parts[3] if len(parts) >= 4 else "1 like"
    return {
        "user": user,
        "text": text,
        "time": time,
        "likes": likes,
    }


def build_manual_comment_entry(
    commenter: str,
    text: str,
    commenter_avatar_map: dict[str, str],
    avatar_emojis: list[str],
    allow_special_comment_images: bool,
    metadata_avatar: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "user": commenter,
        "text": text,
        "time": "",
        "likes": "1 like",
        "show_reply": True,
        "show_heart": True,
    }
    avatar = str(metadata_avatar or "").strip()
    if avatar and looks_like_image_source(avatar) and not allow_special_comment_images:
        avatar = ""
    if not avatar and allow_special_comment_images:
        avatar = commenter_avatar_map.get(normalize_commenter_key(commenter)) or ""
    if not avatar:
        avatar = pick_deterministic_avatar_emoji(commenter, text, avatar_emojis)
    entry["avatar"] = avatar
    return entry


def coerce_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        intval = int(value)
        return intval if intval >= 0 else None
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        if text.isdigit():
            intval = int(text)
            return intval if intval >= 0 else None
    return None


def collect_media_refs(posts: list[dict[str, Any]]) -> set[str]:
    refs: set[str] = set()
    for post in posts:
        for key in ("src", "thumbnail", "poster"):
            value = post.get(key)
            if isinstance(value, str) and value.strip():
                refs.add(value.strip())
        media_items = post.get("media_items")
        if isinstance(media_items, list):
            for item in media_items:
                if not isinstance(item, dict):
                    continue
                for key in ("src", "thumbnail", "poster"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        refs.add(value.strip())
    return refs


def collect_primary_src_refs(items: list[dict[str, Any]]) -> set[str]:
    refs: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        src = canonical_media_ref(item.get("src"))
        if src:
            refs.add(src)
    return refs


def canonical_media_ref(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    text = re.sub(r"^\./+", "", text)
    if text.startswith("src/"):
        text = text[len("src/") :]
    return text


def item_identity_key(item: dict[str, Any]) -> str:
    post_id = str(item.get("post_id") or "").strip()
    if post_id:
        return f"post_id:{post_id}"

    item_id = str(item.get("id") or "").strip()
    if item_id:
        return f"id:{item_id}"

    src = canonical_media_ref(item.get("src"))
    if src:
        return f"src:{src}"

    return ""


def dedupe_feed_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    removed = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item_identity_key(item)
        if key and key in seen:
            removed += 1
            continue
        if key:
            seen.add(key)
        deduped.append(item)
    return deduped, removed


def merge_recovered_metrics_into_existing(
    existing: dict[str, Any],
    recovered: dict[str, Any],
) -> bool:
    changed = False
    for key in ("posted_at_iso", "posted_at"):
        incoming = recovered.get(key)
        if isinstance(incoming, str) and incoming.strip():
            if existing.get(key) != incoming:
                existing[key] = incoming
                changed = True

    for key in ("like_count", "comment_count", "visibility", "post_id"):
        incoming = recovered.get(key)
        if incoming is None:
            continue
        if existing.get(key) != incoming:
            existing[key] = incoming
            changed = True

    incoming_comments = recovered.get("comments")
    if isinstance(incoming_comments, list):
        if existing.get("comments") != incoming_comments:
            existing["comments"] = incoming_comments
            changed = True

    incoming_likes = recovered.get("likes")
    if isinstance(incoming_likes, str):
        likes_text = incoming_likes.strip()
        if likes_text:
            existing_likes = str(existing.get("likes", "")).strip()
            default_likes_lines = {"Liked by friends and others", "Liked by others"}
            should_replace = (likes_text not in default_likes_lines) or (not existing_likes)
            if should_replace and existing_likes != likes_text:
                existing["likes"] = likes_text
                changed = True
    return changed


def maybe_delete_file(path_str: str) -> bool:
    path = Path(path_str)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()

    try:
        path.relative_to(Path.cwd().resolve())
    except ValueError:
        return False

    if path.exists() and path.is_file():
        path.unlink()
        return True
    return False


def build_site_post_from_recovered(
    recovered_entry: dict[str, Any],
    out_dir: Path,
    fallback_thumbnail: str,
    post_id: str,
    commenter_avatar_map: dict[str, str],
    avatar_emojis: list[str],
    allow_special_comment_images: bool,
) -> dict[str, Any] | None:
    post_json = recovered_entry.get("post_json")
    if not isinstance(post_json, dict):
        return None

    recovered_media = post_json.get("media", [])
    if not isinstance(recovered_media, list):
        return None

    usable_media = [
        m
        for m in recovered_media
        if isinstance(m, dict)
        and m.get("copied")
        and m.get("kind") in {"image", "video"}
        and isinstance(m.get("recovered_relative_path"), str)
    ]
    if not usable_media:
        return None

    comments: list[dict[str, Any]] = []
    manual_comments = parse_metadata_comments(post_json.get("comments"))
    metadata_comment_avatars = parse_metadata_comment_avatars(post_json.get("comment_avatars"))
    for commenter, text in manual_comments.items():
        comments.append(
            build_manual_comment_entry(
                commenter,
                text,
                commenter_avatar_map,
                avatar_emojis,
                allow_special_comment_images,
                metadata_avatar=metadata_comment_avatars.get(commenter),
            )
        )

    like_count = coerce_non_negative_int(post_json.get("like_count"))
    visibility = normalize_visibility(post_json.get("visibility"))
    likes_text = (
        str(post_json.get("likes_text", "")).strip()
        if isinstance(post_json.get("likes_text"), str)
        else ""
    )

    caption = str(post_json.get("caption") or "")
    alt_base = collapse_spaces(caption) or post_id
    date_text = display_date_from_post(
        post_json.get("posted_at_iso"),
        str(post_json.get("posted_at") or ""),
    )

    image_sources = [
        path_to_web_string(out_dir / str(media["recovered_relative_path"]))
        for media in usable_media
        if media.get("kind") == "image"
    ]
    fallback_thumb_src = fallback_thumbnail
    primary_image_src = image_sources[0] if image_sources else fallback_thumb_src

    media_items: list[dict[str, Any]] = []
    for index, media in enumerate(usable_media, start=1):
        kind = str(media.get("kind"))
        src = path_to_web_string(out_dir / str(media["recovered_relative_path"]))
        thumbnail_rel = media.get("thumbnail_relative_path")
        thumbnail_src = (
            path_to_web_string(out_dir / str(thumbnail_rel))
            if isinstance(thumbnail_rel, str) and thumbnail_rel.strip()
            else src
        )
        item_alt = alt_base if len(usable_media) == 1 else f"{alt_base} ({index})"
        if kind == "video":
            poster = thumbnail_src
            thumbnail = thumbnail_src
            ratio = 9 / 16
            item_type = "video"
        else:
            poster = src
            thumbnail = src
            ratio = 1
            item_type = "image"
        media_items.append(
            {
                "type": item_type,
                "src": src,
                "thumbnail": thumbnail,
                "poster": poster,
                "alt": item_alt,
                "ratio": ratio,
            }
        )

    primary_item = media_items[0]
    has_video = any(item["type"] == "video" for item in media_items)
    if not likes_text:
        likes_text = "Liked by friends and others"
    audio_text = "Original audio" if has_video else ""

    site_id = str(post_json.get("post_id") or "").strip() or post_id
    result = {
        "id": site_id,
        "type": primary_item["type"],
        "src": primary_item["src"],
        "thumbnail": primary_item["thumbnail"],
        "poster": primary_item["poster"],
        "alt": primary_item["alt"],
        "caption": caption,
        "visibility": visibility,
        "post_id": site_id,
        "posted_at": str(post_json.get("posted_at") or ""),
        "posted_at_iso": str(post_json.get("posted_at_iso") or ""),
        "likes": likes_text,
        "date": date_text,
        "audio": audio_text,
        "ratio": primary_item["ratio"],
        "comment_count": len(comments),
        "media_items": media_items,
        "comments": comments,
    }
    if like_count is not None:
        result["like_count"] = like_count
    return result


def build_site_reel_from_recovered(
    recovered_entry: dict[str, Any],
    out_dir: Path,
    reel_id: str,
    commenter_avatar_map: dict[str, str],
    avatar_emojis: list[str],
    allow_special_comment_images: bool,
) -> dict[str, Any] | None:
    post_json = recovered_entry.get("post_json")
    if not isinstance(post_json, dict):
        return None

    recovered_media = post_json.get("media", [])
    if not isinstance(recovered_media, list):
        return None

    usable_video: dict[str, Any] | None = None
    for media in recovered_media:
        if (
            isinstance(media, dict)
            and media.get("copied")
            and media.get("kind") == "video"
            and isinstance(media.get("recovered_relative_path"), str)
        ):
            usable_video = media
            break
    if usable_video is None:
        return None

    src = path_to_web_string(out_dir / str(usable_video["recovered_relative_path"]))
    thumbnail_rel = usable_video.get("thumbnail_relative_path")
    thumbnail_src = (
        path_to_web_string(out_dir / str(thumbnail_rel))
        if isinstance(thumbnail_rel, str) and thumbnail_rel.strip()
        else src
    )
    caption = str(post_json.get("caption") or "")
    manual_comments_map = parse_metadata_comments(post_json.get("comments"))
    metadata_comment_avatars = parse_metadata_comment_avatars(post_json.get("comment_avatars"))
    comments: list[dict[str, Any]] = [
        build_manual_comment_entry(
            commenter,
            text,
            commenter_avatar_map,
            avatar_emojis,
            allow_special_comment_images,
            metadata_avatar=metadata_comment_avatars.get(commenter),
        )
        for commenter, text in manual_comments_map.items()
    ]
    like_count = coerce_non_negative_int(post_json.get("like_count"))
    visibility = normalize_visibility(post_json.get("visibility"))
    likes_text = (
        str(post_json.get("likes_text", "")).strip()
        if isinstance(post_json.get("likes_text"), str)
        else ""
    )
    if not likes_text:
        likes_text = "Liked by friends and others"

    alt_text = collapse_spaces(caption) or reel_id
    date_text = display_date_from_post(
        post_json.get("posted_at_iso"),
        str(post_json.get("posted_at") or ""),
    )

    site_id = str(post_json.get("post_id") or "").strip() or reel_id
    result = {
        "id": site_id,
        "type": "video",
        "src": src,
        "thumbnail": thumbnail_src,
        "poster": thumbnail_src,
        "alt": alt_text,
        "caption": caption,
        "visibility": visibility,
        "post_id": site_id,
        "posted_at": str(post_json.get("posted_at") or ""),
        "posted_at_iso": str(post_json.get("posted_at_iso") or ""),
        "likes": likes_text,
        "date": date_text,
        "audio": "Original audio",
        "ratio": 9 / 16,
        "comment_count": len(comments),
        "media_items": [
            {
                "type": "video",
                "src": src,
                "thumbnail": thumbnail_src,
                "poster": thumbnail_src,
                "alt": alt_text,
                "ratio": 9 / 16,
            }
        ],
        "comments": comments,
    }
    if like_count is not None:
        result["like_count"] = like_count
    return result


def cmd_recover(args: argparse.Namespace) -> int:
    export_dir: Path = args.export_dir
    out_dir: Path = args.out_dir
    out_img_dir = out_dir / "img"
    out_img_dir.mkdir(parents=True, exist_ok=True)
    copied_static_assets = copy_static_assets(DEFAULT_STATIC_ASSETS_DIR, out_img_dir)
    if copied_static_assets == 0 and not DEFAULT_STATIC_ASSETS_DIR.exists():
        print(
            f"Warning: static asset source directory missing: {DEFAULT_STATIC_ASSETS_DIR}",
            file=sys.stderr,
        )
    profile_snapshot = infer_profile_snapshot(export_dir, args.username)
    username = (
        profile_snapshot.get("username")
        if isinstance(profile_snapshot.get("username"), str)
        else None
    )

    avatar_emoji_choices = (
        normalize_comment_avatar_emojis(args.avatar_emojis)
        if args.avatar_emojis is not None
        else list(DEFAULT_COMMENT_AVATAR_EMOJIS)
    )
    if args.avatar_emojis is None:
        try:
            if args.registry.exists():
                avatar_emoji_choices = normalize_comment_avatar_emojis(
                    load_registry(args.registry).get("comment_avatar_emojis")
                )
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            avatar_emoji_choices = list(DEFAULT_COMMENT_AVATAR_EMOJIS)

    special_comments_source_exists = (
        args.commenter_assets_dir.exists() and args.commenter_assets_dir.is_dir()
    )
    special_comments_available = args.use_special_comments and special_comments_source_exists
    special_comments_target_dir = out_dir / "img" / "people"
    commenter_avatar_map: dict[str, str] = {}
    copied_special_commenter_assets = 0
    if special_comments_available:
        commenter_avatar_map, copied_special_commenter_assets = copy_commenter_assets(
            args.commenter_assets_dir,
            special_comments_target_dir,
        )
    elif args.use_special_comments:
        print(
            (
                "Warning: special comment avatars are enabled but source directory is missing: "
                f"{args.commenter_assets_dir}. Falling back to emoji avatars."
            ),
            file=sys.stderr,
        )

    media_activity_dir = export_dir / "your_instagram_activity/media"
    posts_json_files = sort_posts_export_paths(list(media_activity_dir.glob("posts_*.json")))
    posts_html_files = sort_posts_export_paths(list(media_activity_dir.glob("posts_*.html")))
    posts: list[dict[str, Any]] = []
    if posts_json_files:
        for posts_json in posts_json_files:
            posts.extend(parse_posts_json(posts_json, export_dir))
    elif posts_html_files:
        for posts_html in posts_html_files:
            posts.extend(parse_posts_html(posts_html, export_dir))
    else:
        raise FileNotFoundError(
            "Missing posts export file: expected your_instagram_activity/media/posts_*.json "
            "or posts_*.html"
        )
    if not posts:
        raise RuntimeError("No posts found in export posts files.")

    reels: list[dict[str, Any]] = []
    if args.include_reels:
        reels_json_file = export_dir / "your_instagram_activity/media/reels.json"
        reels_html_file = export_dir / "your_instagram_activity/media/reels.html"
        if reels_json_file.exists():
            reels = parse_reels_json(reels_json_file, export_dir)
        elif reels_html_file.exists():
            reels = parse_reels_html(reels_html_file, export_dir)

    liked_comments_json_file = export_dir / "your_instagram_activity/likes/liked_comments.json"
    liked_comments_html_file = export_dir / "your_instagram_activity/likes/liked_comments.html"
    if liked_comments_json_file.exists():
        liked_comments = parse_liked_comments_json(liked_comments_json_file)
    else:
        liked_comments = parse_liked_comments_html(liked_comments_html_file)
    unresolved_liked = assign_liked_comments_to_posts(posts, liked_comments)

    comments_activity_json_file = export_dir / "your_instagram_activity/comments/post_comments_1.json"
    comments_activity_html_file = export_dir / "your_instagram_activity/comments/post_comments_1.html"
    if comments_activity_json_file.exists():
        post_comments_activity = parse_post_comments_activity_json(comments_activity_json_file)
    else:
        post_comments_activity = parse_post_comments_activity_html(comments_activity_html_file)
    your_comments_on_your_media: list[dict[str, str]] = []
    if username:
        your_comments_on_your_media = [
            item
            for item in post_comments_activity
            if item.get("media_owner", "").casefold() == username.casefold()
        ]

    selected = select_posts(posts, args.post_index, args.post_caption)
    if not selected:
        raise RuntimeError("No posts matched the provided selection filters.")

    out_posts_dir = out_dir / "posts"
    out_posts_dir.mkdir(parents=True, exist_ok=True)
    out_reels_dir = out_dir / "reels"
    if args.include_reels:
        out_reels_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir: Path = args.metadata_dir
    metadata_dir_existed = metadata_dir.exists()
    if metadata_dir_existed and not metadata_dir.is_dir():
        raise NotADirectoryError(f"--metadata-dir exists but is not a directory: {metadata_dir}")
    if not metadata_dir_existed:
        metadata_dir.mkdir(parents=True, exist_ok=True)
    allow_metadata_create = not metadata_dir_existed

    recovered_summary: list[dict[str, Any]] = []
    for index_1based, post in selected:
        post_id = stable_post_id(post, "post")
        recovered_summary.append(
            write_post_output(
                export_dir,
                out_posts_dir,
                index_1based,
                post,
                post_id=post_id,
                metadata_dir=metadata_dir,
                allow_metadata_create=allow_metadata_create,
                post_kind="post",
                collection_name="posts",
            )
        )

    recovered_reels_summary: list[dict[str, Any]] = []
    if args.include_reels:
        for index_1based, reel in enumerate(reels, start=1):
            reel_id = stable_post_id(reel, "reel")
            recovered_reels_summary.append(
                write_post_output(
                    export_dir,
                    out_reels_dir,
                    index_1based,
                    reel,
                    post_id=reel_id,
                    metadata_dir=metadata_dir,
                    allow_metadata_create=allow_metadata_create,
                    post_kind="reel",
                    collection_name="reels",
                )
            )
    forms_path, gate_form_config, posts_form_config, forms_written, forms_added = ensure_forms_config(
        metadata_dir
    )
    forms_entries_total = len(forms_config_entries(gate_form_config, posts_form_config))

    fallback_thumbnail = (
        args.fallback_thumbnail.strip()
        if isinstance(args.fallback_thumbnail, str) and args.fallback_thumbnail.strip()
        else default_fallback_thumbnail_for_out_dir(out_dir)
    )
    if not args.fallback_thumbnail:
        default_thumb_path = out_img_dir / "post.jpg"
        if not default_thumb_path.exists():
            first_image_rel: str | None = None
            for recovered_item in recovered_summary:
                post_json = recovered_item.get("post_json")
                if not isinstance(post_json, dict):
                    continue
                media_items = post_json.get("media")
                if not isinstance(media_items, list):
                    continue
                for media in media_items:
                    if not isinstance(media, dict):
                        continue
                    if media.get("kind") != "image" or not media.get("copied"):
                        continue
                    rel = media.get("recovered_relative_path")
                    if isinstance(rel, str) and rel.strip():
                        first_image_rel = rel
                        break
                if first_image_rel:
                    break
            if first_image_rel:
                source_path = out_dir / first_image_rel
                if source_path.exists():
                    shutil.copy2(source_path, default_thumb_path)

    manifest = {
        "export_dir": str(export_dir),
        "metadata_dir": str(metadata_dir),
        "metadata_dir_preexisting": metadata_dir_existed,
        "metadata_templates_mode": "create_missing_templates" if allow_metadata_create else "read_only",
        "forms_file": str(forms_path),
        "forms_entries_total": forms_entries_total,
        "forms_entries_added": forms_added,
        "generated_at_utc": "1970-01-01T00:00:00Z",
        "username_hint": username,
        "posts_found": len(posts),
        "posts_recovered": len(recovered_summary),
        "reels_found": len(reels),
        "reels_recovered": len(recovered_reels_summary),
        "recovered_posts": [
            {
                "index": item["index"],
                "folder": item["folder"],
                "post_id": item.get("post_id"),
                "metadata_file": item.get("metadata_file"),
                "metadata_template_created": item.get("metadata_template_created"),
                "visibility": item.get("visibility"),
                "metadata_like_count": item.get("metadata_like_count"),
                "metadata_comment_count": item.get("metadata_comment_count"),
                "caption": item["caption"],
                "posted_at": item["posted_at"],
                "media_count": item["media_count"],
                "comments_by_others_count": item["comments_by_others_count"],
            }
            for item in recovered_summary
        ],
        "recovered_reels": [
            {
                "index": item["index"],
                "folder": item["folder"],
                "post_id": item.get("post_id"),
                "metadata_file": item.get("metadata_file"),
                "metadata_template_created": item.get("metadata_template_created"),
                "visibility": item.get("visibility"),
                "metadata_like_count": item.get("metadata_like_count"),
                "metadata_comment_count": item.get("metadata_comment_count"),
                "caption": item["caption"],
                "posted_at": item["posted_at"],
                "media_count": item["media_count"],
            }
            for item in recovered_reels_summary
        ],
        "liked_comments_total": len(liked_comments),
        "liked_comments_unresolved_mapping": unresolved_liked,
        "post_comments_activity_total": len(post_comments_activity),
        "your_comments_on_your_media": your_comments_on_your_media,
        "limitations": [
            "Nanogram export files do not include full text for comments by other people in a post-linked format.",
            "Other-people comments here come from liked_comments (commenter + URL + liked timestamp).",
            "Post mapping for these comments is inferred by nearest post timestamp.",
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    posts_added_to_registry = 0
    reels_added_to_registry = 0
    posts_skipped_existing = 0
    reels_skipped_existing = 0
    posts_updated_existing = 0
    reels_updated_existing = 0
    profile_image_ref: str | None = None
    profile_image_source: str | None = None
    if args.copy_profile_image:
        profile_image_dst = (
            args.profile_image_dst
            if isinstance(args.profile_image_dst, Path)
            else (out_img_dir / "profile.jpg")
        )
        copied_ref, source_rel = copy_profile_image_from_export(export_dir, profile_image_dst)
        if copied_ref:
            profile_image_ref = copied_ref
            profile_image_source = source_rel
    synced_html = False
    if args.add_to_registry:
        registry = load_registry(args.registry)
        registry_changed = False
        avatar_emojis = (
            normalize_comment_avatar_emojis(args.avatar_emojis)
            if args.avatar_emojis is not None
            else normalize_comment_avatar_emojis(registry.get("comment_avatar_emojis"))
        )
        profile = registry.get("profile")
        if not isinstance(profile, dict):
            profile = {}
            registry["profile"] = profile
            registry_changed = True
        if registry.get("comment_avatar_emojis") != avatar_emojis:
            registry["comment_avatar_emojis"] = avatar_emojis
            registry_changed = True

        if username:
            if str(profile.get("username", "")).strip() != username:
                profile["username"] = username
                registry_changed = True
        full_name = profile_snapshot.get("full_name")
        if isinstance(full_name, str) and full_name.strip():
            if str(profile.get("full_name", "")).strip() != full_name.strip():
                profile["full_name"] = full_name.strip()
                registry_changed = True

        followers_count = coerce_non_negative_int(profile_snapshot.get("followers_count"))
        if followers_count is not None:
            if coerce_non_negative_int(profile.get("followers_count")) != followers_count:
                profile["followers_count"] = followers_count
                registry_changed = True

        following_count = coerce_non_negative_int(profile_snapshot.get("following_count"))
        if following_count is not None:
            if coerce_non_negative_int(profile.get("following_count")) != following_count:
                profile["following_count"] = following_count
                registry_changed = True

        if profile_image_ref:
            if str(profile.get("profile_image", "")).strip() != profile_image_ref:
                profile["profile_image"] = profile_image_ref
            # Keep registry/html in sync with the latest copied file even if path is unchanged.
            registry_changed = True

        used_ids: set[str] = set()
        existing_posts_list = registry.get("posts", [])
        if not isinstance(existing_posts_list, list):
            existing_posts_list = []
            registry["posts"] = existing_posts_list
            registry_changed = True
        existing_reels_list = registry.get("reels", [])
        if not isinstance(existing_reels_list, list):
            existing_reels_list = []
            registry["reels"] = existing_reels_list
            registry_changed = True

        deduped_posts, removed_post_dupes = dedupe_feed_items(existing_posts_list)
        if removed_post_dupes:
            existing_posts_list = deduped_posts
            registry["posts"] = existing_posts_list
            registry_changed = True

        deduped_reels, removed_reel_dupes = dedupe_feed_items(existing_reels_list)
        if removed_reel_dupes:
            existing_reels_list = deduped_reels
            registry["reels"] = existing_reels_list
            registry_changed = True

        existing_post_srcs = collect_primary_src_refs(existing_posts_list)
        existing_reel_srcs = collect_primary_src_refs(existing_reels_list)
        existing_post_ids = {
            str(item.get("post_id") or item.get("id") or "").strip()
            for item in existing_posts_list
            if isinstance(item, dict)
        }
        existing_reel_ids = {
            str(item.get("post_id") or item.get("id") or "").strip()
            for item in existing_reels_list
            if isinstance(item, dict)
        }

        existing_posts_by_src: dict[str, dict[str, Any]] = {}
        existing_posts_by_id: dict[str, dict[str, Any]] = {}
        for item in existing_posts_list:
            if not isinstance(item, dict):
                continue
            post_identity = str(item.get("post_id") or item.get("id") or "").strip()
            if post_identity:
                existing_posts_by_id[post_identity] = item
            src = canonical_media_ref(item.get("src"))
            if src:
                existing_posts_by_src[src] = item

        existing_reels_by_src: dict[str, dict[str, Any]] = {}
        existing_reels_by_id: dict[str, dict[str, Any]] = {}
        for item in existing_reels_list:
            if not isinstance(item, dict):
                continue
            reel_identity = str(item.get("post_id") or item.get("id") or "").strip()
            if reel_identity:
                existing_reels_by_id[reel_identity] = item
            src = canonical_media_ref(item.get("src"))
            if src:
                existing_reels_by_src[src] = item

        new_site_posts: list[dict[str, Any]] = []
        for recovered_item in recovered_summary:
            recovered_id = ensure_unique_post_id(
                registry,
                preferred=f"recovered_{recovered_item['index']}_{slugify(recovered_item['caption'])}",
                used_ids=used_ids,
            )
            site_post = build_site_post_from_recovered(
                recovered_item,
                out_dir,
                fallback_thumbnail,
                recovered_id,
                commenter_avatar_map,
                avatar_emoji_choices,
                special_comments_available,
            )
            if site_post:
                post_identity = str(site_post.get("post_id") or site_post.get("id") or "").strip()
                src = canonical_media_ref(site_post.get("src"))
                if post_identity and post_identity in existing_post_ids:
                    existing_item = existing_posts_by_id.get(post_identity)
                    if existing_item and merge_recovered_metrics_into_existing(existing_item, site_post):
                        posts_updated_existing += 1
                        registry_changed = True
                    else:
                        posts_skipped_existing += 1
                    continue
                if src and src in existing_post_srcs:
                    existing_item = existing_posts_by_src.get(src)
                    if existing_item and merge_recovered_metrics_into_existing(existing_item, site_post):
                        posts_updated_existing += 1
                        registry_changed = True
                    else:
                        posts_skipped_existing += 1
                    continue
                if src:
                    existing_post_srcs.add(src)
                    existing_posts_by_src[src] = site_post
                if post_identity:
                    existing_post_ids.add(post_identity)
                    existing_posts_by_id[post_identity] = site_post
                new_site_posts.append(site_post)

        new_site_reels: list[dict[str, Any]] = []
        if args.include_reels:
            for recovered_reel in recovered_reels_summary:
                recovered_id = ensure_unique_post_id(
                    registry,
                    preferred=f"reel_{recovered_reel['index']}_{slugify(recovered_reel['caption'])}",
                    used_ids=used_ids,
                )
                site_reel = build_site_reel_from_recovered(
                    recovered_reel,
                    out_dir,
                    recovered_id,
                    commenter_avatar_map,
                    avatar_emoji_choices,
                    special_comments_available,
                )
                if site_reel:
                    reel_identity = str(site_reel.get("post_id") or site_reel.get("id") or "").strip()
                    src = canonical_media_ref(site_reel.get("src"))
                    if reel_identity and reel_identity in existing_reel_ids:
                        existing_item = existing_reels_by_id.get(reel_identity)
                        if existing_item and merge_recovered_metrics_into_existing(existing_item, site_reel):
                            reels_updated_existing += 1
                            registry_changed = True
                        else:
                            reels_skipped_existing += 1
                        continue
                    if src and src in existing_reel_srcs:
                        existing_item = existing_reels_by_src.get(src)
                        if existing_item and merge_recovered_metrics_into_existing(existing_item, site_reel):
                            reels_updated_existing += 1
                            registry_changed = True
                        else:
                            reels_skipped_existing += 1
                        continue
                    if src:
                        existing_reel_srcs.add(src)
                        existing_reels_by_src[src] = site_reel
                    if reel_identity:
                        existing_reel_ids.add(reel_identity)
                        existing_reels_by_id[reel_identity] = site_reel
                    new_site_reels.append(site_reel)

        if new_site_posts:
            if args.insert == "prepend":
                registry["posts"] = new_site_posts + registry.get("posts", [])
            else:
                registry["posts"].extend(new_site_posts)
            posts_added_to_registry = len(new_site_posts)
            registry_changed = True
        if new_site_reels:
            if args.insert == "prepend":
                registry["reels"] = new_site_reels + registry.get("reels", [])
            else:
                registry["reels"].extend(new_site_reels)
            reels_added_to_registry = len(new_site_reels)
            registry_changed = True

        if apply_forms_to_registry(registry, posts_form_config):
            registry_changed = True

        total_profile_posts = (
            len(registry.get("posts", []))
            + len(registry.get("reels", []))
        )
        if coerce_non_negative_int(profile.get("posts_count")) != total_profile_posts:
            profile["posts_count"] = total_profile_posts
            registry_changed = True

        if registry_changed:
            save_registry(args.registry, registry)
            if args.sync_html:
                sync_app_html_from_registry(args.app_html, registry)
                synced_html = True

    print(f"Recovered {len(recovered_summary)} post(s) to: {out_dir}")
    if args.include_reels:
        print(f"Recovered {len(recovered_reels_summary)} reel(s) to: {out_dir}")
    print(f"Manifest: {out_dir / 'manifest.json'}")
    if args.use_special_comments:
        print(
            f"Special comments: enabled (source {'found' if special_comments_source_exists else 'missing'})"
        )
        print(
            f"Special commenter avatar mappings: {len(commenter_avatar_map)} "
            f"(copied: {copied_special_commenter_assets}) "
            f"from {args.commenter_assets_dir} -> {special_comments_target_dir}"
        )
    else:
        print("Special comments: disabled (username comments use emoji avatars)")
    created_templates = sum(
        1 for item in (recovered_summary + recovered_reels_summary) if item.get("metadata_template_created")
    )
    metadata_mode_text = (
        "read-only post/reel metadata (pre-existing directory)"
        if metadata_dir_existed
        else "generated templates"
    )
    print(
        f"Metadata directory: {metadata_dir} "
        f"(mode: {metadata_mode_text}, new templates: {created_templates})"
    )
    print(
        f"Forms config: {forms_path} "
        f"(entries: {forms_entries_total}, added: {forms_added}, "
        f"updated: {'yes' if forms_written else 'no'})"
    )
    print("Post IDs (use these as <postid>.json names in metadata/posts or metadata/reels):")
    for item in recovered_summary:
        caption = collapse_spaces(str(item.get("caption", "")))
        if len(caption) > 100:
            caption = caption[:97] + "..."
        print(
            f"  POST {item.get('post_id')}: {caption}"
            f" [{item.get('metadata_file')}]"
        )
    for item in recovered_reels_summary:
        caption = collapse_spaces(str(item.get("caption", "")))
        if len(caption) > 100:
            caption = caption[:97] + "..."
        print(
            f"  REEL {item.get('post_id')}: {caption}"
            f" [{item.get('metadata_file')}]"
        )
    if args.add_to_registry:
        print(f"Added {posts_added_to_registry} recovered post(s) into registry: {args.registry}")
        if posts_updated_existing:
            print(f"Updated metrics on {posts_updated_existing} existing post(s) in registry.")
        if posts_skipped_existing:
            print(f"Skipped {posts_skipped_existing} post(s) already present in registry.")
        if removed_post_dupes:
            print(f"Removed {removed_post_dupes} duplicate post(s) from registry.")
        if args.include_reels:
            print(f"Added {reels_added_to_registry} recovered reel(s) into registry: {args.registry}")
            if reels_updated_existing:
                print(f"Updated metrics on {reels_updated_existing} existing reel(s) in registry.")
            if reels_skipped_existing:
                print(f"Skipped {reels_skipped_existing} reel(s) already present in registry.")
            if removed_reel_dupes:
                print(f"Removed {removed_reel_dupes} duplicate reel(s) from registry.")
        if profile_image_ref:
            print(
                "Updated profile image from export"
                + (f" ({profile_image_source})" if profile_image_source else "")
                + f" -> {profile_image_ref}"
            )
        if args.sync_html and synced_html:
            print(f"Updated managed posts block in: {args.app_html}")
    return 0


def build_manual_post_from_args(args: argparse.Namespace, registry: dict[str, Any]) -> dict[str, Any]:
    media_path = Path(args.media)
    if not media_path.exists():
        raise FileNotFoundError(f"Media file not found: {media_path}")

    inferred_kind = media_kind(media_path.as_posix())
    if inferred_kind not in {"image", "video"}:
        raise ValueError(
            f"Unsupported media type for post: {media_path}. "
            "Use an image or video file."
        )

    post_kind = args.kind or inferred_kind
    if post_kind not in {"image", "video"}:
        raise ValueError("--kind must be 'image' or 'video'")

    post_id = args.post_id or ensure_unique_post_id(registry)

    media_src = media_path
    thumbnail_src = Path(args.thumbnail) if args.thumbnail else None
    poster_src = Path(args.poster) if args.poster else None

    if post_kind == "image":
        thumbnail_src = media_src
        poster_src = media_src
    else:
        if thumbnail_src is None and poster_src is None:
            thumbnail_src = media_src
            poster_src = None
        elif thumbnail_src is None:
            thumbnail_src = poster_src

    if args.copy_media:
        target_dir = args.media_root / post_id
        target_dir.mkdir(parents=True, exist_ok=True)

        media_dst = target_dir / media_src.name
        shutil.copy2(media_src, media_dst)
        media_ref = path_to_web_string(media_dst)

        thumb_ref = media_ref
        poster_ref = media_ref if post_kind == "image" else ""

        if post_kind == "video":
            thumb_path = thumbnail_src or media_src

            thumb_dst = target_dir / thumb_path.name
            if thumb_path.resolve() != media_src.resolve():
                shutil.copy2(thumb_path, thumb_dst)
            else:
                thumb_dst = media_dst

            thumb_ref = path_to_web_string(thumb_dst)
            if poster_src:
                poster_path = poster_src
                poster_dst = target_dir / poster_path.name
                if poster_path.resolve() not in {media_src.resolve(), thumb_path.resolve()}:
                    shutil.copy2(poster_path, poster_dst)
                elif poster_path.resolve() == thumb_path.resolve():
                    poster_dst = thumb_dst
                else:
                    poster_dst = media_dst
                poster_ref = path_to_web_string(poster_dst)
    else:
        media_ref = path_to_web_string(media_src)
        thumb_ref = path_to_web_string(thumbnail_src or media_src)
        if post_kind == "video":
            poster_ref = path_to_web_string(poster_src) if poster_src else ""
        else:
            poster_ref = path_to_web_string(poster_src or thumbnail_src or media_src)

    comments: list[dict[str, Any]] = []
    for raw_comment in args.comment:
        comments.append(parse_comment_arg(raw_comment))

    today = dt.date.today()
    default_date = f"{today.strftime('%B')} {today.day}"
    date_text = args.date or default_date

    if args.likes:
        likes_text = args.likes
    else:
        likes_text = "Liked by friends and others"

    ratio = args.ratio if args.ratio and args.ratio > 0 else (9 / 16 if post_kind == "video" else 1)

    return {
        "id": post_id,
        "type": post_kind,
        "src": media_ref,
        "thumbnail": thumb_ref,
        "poster": poster_ref,
        "alt": args.alt or f"Post {post_id}",
        "caption": args.caption,
        "likes": likes_text,
        "date": date_text,
        "audio": args.audio if post_kind == "video" else "",
        "ratio": ratio,
        "media_items": [
            {
                "type": post_kind,
                "src": media_ref,
                "thumbnail": thumb_ref,
                "poster": poster_ref,
                "alt": args.alt or f"Post {post_id}",
                "ratio": ratio,
            }
        ],
        "comments": comments,
    }


def cmd_add(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    post = build_manual_post_from_args(args, registry)
    target_feed = args.feed

    if args.insert == "prepend":
        registry[target_feed] = [post] + registry.get(target_feed, [])
    else:
        registry[target_feed].append(post)

    save_registry(args.registry, registry)
    if args.sync_html:
        sync_app_html_from_registry(args.app_html, registry)

    print(f"Added {target_feed[:-1]} '{post['id']}' to registry: {args.registry}")
    if args.sync_html:
        print(f"Updated managed posts block in: {args.app_html}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    target_feed = args.feed
    items = registry.get(target_feed, [])
    if not items:
        raise RuntimeError(f"No {target_feed} in registry to remove.")

    remove_idx: int | None = None
    if args.post_id:
        for idx, post in enumerate(items):
            if str(post.get("id", "")) == args.post_id:
                remove_idx = idx
                break
        if remove_idx is None:
            raise RuntimeError(f"No {target_feed[:-1]} found with id: {args.post_id}")
    elif args.index is not None:
        if args.index < 1 or args.index > len(items):
            raise RuntimeError(f"--index must be between 1 and {len(items)}")
        remove_idx = args.index - 1
    else:
        raise RuntimeError("Provide either --id or --index to remove an entry.")

    removed = items.pop(remove_idx)

    deleted_files = 0
    if args.delete_media:
        remaining_refs = collect_media_refs(registry.get("posts", []) + registry.get("reels", []))
        removed_refs = collect_media_refs([removed])
        for ref in removed_refs:
            if ref in remaining_refs:
                continue
            if maybe_delete_file(ref):
                deleted_files += 1

    save_registry(args.registry, registry)
    if args.sync_html:
        sync_app_html_from_registry(args.app_html, registry)

    print(f"Removed {target_feed[:-1]} '{removed.get('id', 'unknown')}' from registry.")
    if args.delete_media:
        print(f"Deleted unreferenced media files: {deleted_files}")
    if args.sync_html:
        print(f"Updated managed posts block in: {args.app_html}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    posts = registry.get("posts", [])
    reels = registry.get("reels", [])

    print(f"Registry: {args.registry}")
    print(f"Posts: {len(posts)}")
    for idx, post in enumerate(posts, start=1):
        post_id = str(post.get("id", ""))
        post_type = str(post.get("type", ""))
        date = str(post.get("date", ""))
        caption = str(post.get("caption", "")).replace("\n", " ")
        if len(caption) > 70:
            caption = caption[:67] + "..."
        print(f"P{idx:03d}  {post_id:24s}  {post_type:5s}  {date:14s}  {caption}")

    print(f"Reels: {len(reels)}")
    for idx, reel in enumerate(reels, start=1):
        reel_id = str(reel.get("id", ""))
        reel_type = str(reel.get("type", ""))
        date = str(reel.get("date", ""))
        caption = str(reel.get("caption", "")).replace("\n", " ")
        if len(caption) > 70:
            caption = caption[:67] + "..."
        print(f"R{idx:03d}  {reel_id:24s}  {reel_type:5s}  {date:14s}  {caption}")

    return 0


def cmd_rebuild_index(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)

    profile = registry.get("profile", {})
    if args.username:
        profile["username"] = args.username
    if args.profile_image:
        profile["profile_image"] = args.profile_image
    registry["profile"] = profile

    save_registry(args.registry, registry)
    sync_app_html_from_registry(args.app_html, registry)

    print(f"Rebuilt managed posts block in: {args.app_html}")
    print(f"Using registry: {args.registry}")
    return 0


def refresh_video_thumbnails_in_entry(entry: dict[str, Any]) -> tuple[bool, int, int, int]:
    changed = False
    generated = 0
    missing_source = 0
    failed_generate = 0

    media_items = entry.get("media_items")
    targets: list[dict[str, Any]] = []
    if isinstance(media_items, list):
        for item in media_items:
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "")).strip() == "video":
                targets.append(item)
    elif str(entry.get("type", "")).strip() == "video":
        targets.append(entry)

    for target in targets:
        src = target.get("src")
        if not isinstance(src, str) or not src.strip():
            continue
        src_path = Path(src)
        if not src_path.is_absolute():
            src_path = (Path.cwd() / src_path).resolve()
        if not src_path.exists():
            missing_source += 1
            continue

        thumb_path = video_thumbnail_path(src_path)
        if not thumb_path.exists():
            if generate_video_thumbnail(src_path, thumb_path):
                generated += 1
            else:
                failed_generate += 1
                continue

        thumb_ref = path_to_web_string(thumb_path)
        if target.get("thumbnail") != thumb_ref:
            target["thumbnail"] = thumb_ref
            changed = True
        if target.get("poster") != thumb_ref:
            target["poster"] = thumb_ref
            changed = True

    if isinstance(media_items, list) and media_items:
        first_item = media_items[0]
        if isinstance(first_item, dict):
            first_thumb = first_item.get("thumbnail")
            first_poster = first_item.get("poster")
            if isinstance(first_thumb, str) and first_thumb.strip() and entry.get("thumbnail") != first_thumb:
                entry["thumbnail"] = first_thumb
                changed = True
            if isinstance(first_poster, str) and first_poster.strip() and entry.get("poster") != first_poster:
                entry["poster"] = first_poster
                changed = True

    return changed, generated, missing_source, failed_generate


def cmd_refresh_thumbnails(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)

    total_changed = 0
    total_generated = 0
    total_missing = 0
    total_failed = 0

    for feed in ("posts", "reels"):
        items = registry.get(feed, [])
        if not isinstance(items, list):
            continue
        for entry in items:
            if not isinstance(entry, dict):
                continue
            changed, generated, missing_source, failed_generate = refresh_video_thumbnails_in_entry(entry)
            if changed:
                total_changed += 1
            total_generated += generated
            total_missing += missing_source
            total_failed += failed_generate

    save_registry(args.registry, registry)
    if args.sync_html:
        sync_app_html_from_registry(args.app_html, registry)

    print(f"Refreshed video thumbnails in registry: {args.registry}")
    print(f"Entries updated: {total_changed}")
    print(f"Thumbnail images generated: {total_generated}")
    print(f"Missing video sources: {total_missing}")
    print(f"Thumbnail generation failures: {total_failed}")
    if args.sync_html:
        print(f"Updated managed posts block in: {args.app_html}")
    return 0


def add_recover_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "recover",
        help="Recover posts/media/captions/comments metadata from a nanogram export.",
    )
    parser.add_argument(
        "--export-dir",
        required=True,
        type=Path,
        help="Path to the nanogram export root directory.",
    )
    parser.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        type=Path,
        help="Output directory for recovered data.",
    )
    parser.add_argument(
        "--post-index",
        type=int,
        default=None,
        help="Recover only a specific 1-based post index from posts export files.",
    )
    parser.add_argument(
        "--post-caption",
        default=None,
        help="Recover only posts whose caption contains this substring (case-insensitive).",
    )
    parser.add_argument(
        "--include-reels",
        dest="include_reels",
        action="store_true",
        default=True,
        help="Also recover reels from your_instagram_activity/media/reels.json (or reels.html).",
    )
    parser.add_argument(
        "--no-include-reels",
        dest="include_reels",
        action="store_false",
        help="Skip reel recovery.",
    )
    parser.add_argument(
        "--username",
        default=None,
        help="Nanogram username; used for comment-activity reporting.",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY,
        help="Path to site posts registry JSON.",
    )
    parser.add_argument(
        "--metadata-dir",
        type=Path,
        default=DEFAULT_METADATA_DIR,
        help=(
            "Directory containing per-post metadata files named <postid>.json under "
            "<metadata-dir>/posts and <metadata-dir>/reels. "
            "If the directory does not exist, it is created with templates for each post/reel "
            "plus a forms.json with two entries: one for cipherkey and one shared by all posts/reels. "
            "Existing post/reel metadata files are read-only; forms.json is normalized to this schema."
        ),
    )
    parser.add_argument(
        "--commenter-assets-dir",
        type=Path,
        default=DEFAULT_COMMENTER_ASSETS_DIR,
        help=(
            "Directory of commenter profile images. "
            "When special comments are enabled, images are copied into <out-dir>/img/people "
            "and mapped by filename stem to commenter usernames."
        ),
    )
    parser.add_argument(
        "--use-special-comments",
        dest="use_special_comments",
        action="store_true",
        default=True,
        help=(
            "Enable special commenter pictures from --commenter-assets-dir. "
            "If unavailable, commenter avatars fall back to deterministic emoji."
        ),
    )
    parser.add_argument(
        "--no-use-special-comments",
        dest="use_special_comments",
        action="store_false",
        help="Disable special commenter pictures; always use emoji avatars for username comments.",
    )
    parser.add_argument(
        "--app-html",
        type=Path,
        default=DEFAULT_APP_HTML,
        help="Target nanogram HTML file to update managed posts data.",
    )
    parser.add_argument(
        "--avatar-emojis",
        default=None,
        help=(
            "Avatar emoji choices for comment identity prompt. "
            "Accepts JSON array string (e.g. '[\"😀\",\"🙂\"]') or a comma/newline separated list."
        ),
    )
    parser.add_argument(
        "--fallback-thumbnail",
        default=None,
        help="Thumbnail fallback path for recovered video posts (default: <out-dir>/img/post.jpg).",
    )
    parser.add_argument(
        "--copy-profile-image",
        dest="copy_profile_image",
        action="store_true",
        default=True,
        help="Copy latest export profile image (media/profile/*/*) into --profile-image-dst and update registry profile image path.",
    )
    parser.add_argument(
        "--no-copy-profile-image",
        dest="copy_profile_image",
        action="store_false",
        help="Do not copy/update profile image from export.",
    )
    parser.add_argument(
        "--profile-image-dst",
        type=Path,
        default=None,
        help="Destination path for copied profile image when --copy-profile-image is enabled (default: <out-dir>/img/profile.jpg).",
    )
    parser.add_argument(
        "--insert",
        choices=["prepend", "append"],
        default="prepend",
        help="Where to insert recovered posts in registry.",
    )
    parser.add_argument(
        "--add-to-registry",
        dest="add_to_registry",
        action="store_true",
        default=True,
        help="Add recovered posts to the registry.",
    )
    parser.add_argument(
        "--no-add-to-registry",
        dest="add_to_registry",
        action="store_false",
        help="Do not add recovered posts to registry.",
    )
    parser.add_argument(
        "--sync-html",
        dest="sync_html",
        action="store_true",
        default=True,
        help="Sync managed post data into nanogram HTML.",
    )
    parser.add_argument(
        "--no-sync-html",
        dest="sync_html",
        action="store_false",
        help="Skip syncing nanogram HTML.",
    )
    parser.set_defaults(handler=cmd_recover)


def add_add_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("add", help="Add one post to the site registry.")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--app-html", type=Path, default=DEFAULT_APP_HTML)
    parser.add_argument("--feed", choices=["posts", "reels"], default="posts")
    parser.add_argument("--media", required=True, help="Path to image/video file.")
    parser.add_argument("--kind", choices=["image", "video"], default=None)
    parser.add_argument("--thumbnail", default=None, help="Thumbnail image path.")
    parser.add_argument("--poster", default=None, help="Poster image path.")
    parser.add_argument("--caption", default="", help="Post caption.")
    parser.add_argument("--date", default=None, help="Display date, e.g. 'January 5'.")
    parser.add_argument("--likes", default=None, help="Likes line text.")
    parser.add_argument("--audio", default="Original audio", help="Audio label for video posts.")
    parser.add_argument("--ratio", type=float, default=None, help="Media width/height ratio.")
    parser.add_argument("--alt", default="", help="Alt text.")
    parser.add_argument("--id", dest="post_id", default=None, help="Post id (optional).")
    parser.add_argument(
        "--comment",
        action="append",
        default=[],
        help="Repeatable: user|text|time|likes",
    )
    parser.add_argument(
        "--insert",
        choices=["prepend", "append"],
        default="prepend",
        help="Where to insert the post in registry.",
    )
    parser.add_argument(
        "--copy-media",
        action="store_true",
        help="Copy media into --media-root/<post_id>/ before adding.",
    )
    parser.add_argument(
        "--media-root",
        type=Path,
        default=Path("build/assets/posts"),
        help="Managed media directory when --copy-media is used.",
    )
    parser.add_argument(
        "--sync-html",
        dest="sync_html",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--no-sync-html",
        dest="sync_html",
        action="store_false",
    )
    parser.set_defaults(handler=cmd_add)


def add_remove_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("remove", help="Remove a post from the site registry.")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--app-html", type=Path, default=DEFAULT_APP_HTML)
    parser.add_argument("--feed", choices=["posts", "reels"], default="posts")
    parser.add_argument("--id", dest="post_id", default=None, help="Post id to remove.")
    parser.add_argument("--index", type=int, default=None, help="1-based position to remove.")
    parser.add_argument(
        "--delete-media",
        action="store_true",
        help="Delete media files only if no remaining post references them.",
    )
    parser.add_argument(
        "--sync-html",
        dest="sync_html",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--no-sync-html",
        dest="sync_html",
        action="store_false",
    )
    parser.set_defaults(handler=cmd_remove)


def add_list_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("list", help="List posts currently in registry.")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.set_defaults(handler=cmd_list)


def add_rebuild_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "rebuild-index",
        help="Rewrite managed posts JSON block in nanogram HTML from registry.",
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--app-html", type=Path, default=DEFAULT_APP_HTML)
    parser.add_argument("--username", default=None)
    parser.add_argument("--profile-image", default=None)
    parser.set_defaults(handler=cmd_rebuild_index)


def add_refresh_thumbnails_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "refresh-thumbnails",
        help="Generate/update static thumbnails for video media in registry and sync nanogram HTML.",
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--app-html", type=Path, default=DEFAULT_APP_HTML)
    parser.add_argument(
        "--sync-html",
        dest="sync_html",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--no-sync-html",
        dest="sync_html",
        action="store_false",
    )
    parser.set_defaults(handler=cmd_refresh_thumbnails)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recover nanogram export data and manage posts in nanogram.app.html.",
    )
    subparsers = parser.add_subparsers(dest="command")

    add_recover_subparser(subparsers)
    add_add_subparser(subparsers)
    add_remove_subparser(subparsers)
    add_list_subparser(subparsers)
    add_rebuild_subparser(subparsers)
    add_refresh_thumbnails_subparser(subparsers)

    return parser


def normalize_command_argv(argv: list[str]) -> list[str]:
    if len(argv) <= 1:
        return argv

    first = argv[1]
    known = {"recover", "add", "remove", "list", "rebuild-index", "refresh-thumbnails"}

    if first in {"-h", "--help"}:
        return argv

    if first.startswith("-") or first not in known:
        return [argv[0], "recover", *argv[1:]]

    return argv


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = argv if argv is not None else sys.argv
    normalized = normalize_command_argv(raw_argv)

    args = parser.parse_args(normalized[1:])
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1

    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
