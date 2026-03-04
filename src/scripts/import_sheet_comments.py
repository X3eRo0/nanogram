#!/usr/bin/env python3
"""
Import comments from a CSV (local file or Google Sheets CSV URL) into metadata files.

Expected CSV fields:
- post-id
- avatar
- name
- actual comment

The importer updates:
- metadata/<posts|reels>/<post_id>.json -> comments[name] = comment
- metadata/<posts|reels>/<post_id>.json -> comment_avatars[name] = avatar (if provided)

Private sheets are supported via Google Sheets API mode:
- --google-sheet-id
- --google-access-token (or env var)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen


DEFAULT_METADATA_DIR = Path("build/metadata")
DEFAULT_ENCODING = "utf-8-sig"


def canonical_post_id_for_metadata(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("post-"):
        return "post_" + text[len("post-") :].strip()
    if text.startswith("reel-"):
        return "reel_" + text[len("reel-") :].strip()
    return text


def post_id_candidates(value: str) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []

    candidates: list[str] = []

    def push(item: str) -> None:
        token = str(item or "").strip()
        if token and token not in candidates:
            candidates.append(token)

    push(raw)
    canonical = canonical_post_id_for_metadata(raw)
    push(canonical)

    if canonical.startswith("post_"):
        push("post-" + canonical[len("post_") :].strip())
    elif canonical.startswith("reel_"):
        push("reel-" + canonical[len("reel_") :].strip())

    return candidates


def infer_post_kind(post_id: str) -> str:
    text = canonical_post_id_for_metadata(post_id)
    if text.startswith("reel_"):
        return "reel"
    return "post"


def canonicalize_header(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import comments from CSV into metadata post/reel JSON files."
    )
    parser.add_argument(
        "--csv",
        default=None,
        help="Path to CSV file or URL (Google Sheets URL accepted).",
    )
    parser.add_argument(
        "--google-sheet-id",
        default=None,
        help="Private/public Google Sheet ID. When set, rows are fetched via Google Sheets API.",
    )
    parser.add_argument(
        "--google-sheet-range",
        default="A:Z",
        help="A1 range for Google Sheets API mode (default: A:Z). First row must be headers.",
    )
    parser.add_argument(
        "--google-access-token",
        default=None,
        help=(
            "OAuth access token for Google Sheets API mode (Bearer token). "
            "If omitted, --google-access-token-env is used."
        ),
    )
    parser.add_argument(
        "--google-access-token-env",
        default="GOOGLE_ACCESS_TOKEN",
        help=(
            "Environment variable containing OAuth access token for Google Sheets API mode "
            "(default: GOOGLE_ACCESS_TOKEN)."
        ),
    )
    parser.add_argument(
        "--metadata-dir",
        type=Path,
        default=DEFAULT_METADATA_DIR,
        help="Metadata root directory (default: build/metadata).",
    )
    parser.add_argument(
        "--encoding",
        default=DEFAULT_ENCODING,
        help="CSV text encoding (default: utf-8-sig).",
    )
    parser.add_argument(
        "--post-id-column",
        default="post-id",
        help="Header name for post ID (default: post-id).",
    )
    parser.add_argument(
        "--avatar-column",
        default="avatar",
        help="Header name for avatar emoji (default: avatar).",
    )
    parser.add_argument(
        "--name-column",
        default="name",
        help="Header name for commenter name/alias (default: name).",
    )
    parser.add_argument(
        "--comment-column",
        default="actual comment",
        help="Header name for comment text (default: actual comment).",
    )
    parser.add_argument(
        "--default-name",
        default="Anonymous",
        help="Fallback name when CSV name field is empty (default: Anonymous).",
    )
    parser.add_argument(
        "--create-missing",
        action="store_true",
        help="Create metadata file if a post-id metadata file does not already exist.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report changes without writing files.",
    )
    args = parser.parse_args()
    if not args.csv and not args.google_sheet_id:
        parser.error("Provide one input source: --csv <path|url> or --google-sheet-id <id>.")
    if args.google_sheet_id and not args.google_access_token:
        env_name = str(args.google_access_token_env or "").strip()
        if env_name:
            env_value = os.getenv(env_name, "").strip()
            if env_value:
                args.google_access_token = env_value
    if args.google_sheet_id and not args.google_access_token:
        env_name = str(args.google_access_token_env or "").strip() or "GOOGLE_ACCESS_TOKEN"
        parser.error(
            "--google-sheet-id requires --google-access-token "
            f"or env var {env_name}."
        )
    return args


def maybe_google_sheet_to_csv_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    host = parsed.netloc.lower()
    path = parsed.path
    if "docs.google.com" not in host or "/spreadsheets/d/" not in path:
        return raw_url

    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", path)
    if not match:
        return raw_url
    sheet_id = match.group(1)

    query = parse_qs(parsed.query)
    gid = ""
    if "gid" in query and query["gid"]:
        gid = str(query["gid"][0]).strip()
    if not gid and parsed.fragment:
        frag_q = parse_qs(parsed.fragment)
        if "gid" in frag_q and frag_q["gid"]:
            gid = str(frag_q["gid"][0]).strip()
        else:
            frag_match = re.search(r"gid=([0-9]+)", parsed.fragment)
            if frag_match:
                gid = frag_match.group(1)

    params: dict[str, str] = {"format": "csv"}
    if gid:
        params["gid"] = gid
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?{urlencode(params)}"


def read_csv_text(source: str, encoding: str) -> str:
    source = source.strip()
    if source.startswith("http://") or source.startswith("https://"):
        url = maybe_google_sheet_to_csv_url(source)
        req = Request(
            url=url,
            headers={"User-Agent": "nanogram-comment-importer/1.0"},
        )
        try:
            with urlopen(req, timeout=30) as response:
                raw = response.read()
        except HTTPError as exc:
            hint = ""
            if exc.code in {401, 403} and "docs.google.com/spreadsheets" in url:
                hint = (
                    " Sheet appears private; use --google-sheet-id with "
                    "--google-access-token (or env var)."
                )
            raise RuntimeError(
                f"Failed to download CSV ({exc.code} {exc.reason}).{hint}"
            ) from exc
        return raw.decode(encoding)

    path = Path(source)
    return path.read_text(encoding=encoding)


def fetch_google_sheet_as_csv(sheet_id: str, sheet_range: str, access_token: str) -> str:
    sheet_id = str(sheet_id or "").strip()
    sheet_range = str(sheet_range or "").strip() or "A:Z"
    token = str(access_token or "").strip()
    if not sheet_id:
        raise ValueError("google-sheet-id is empty")
    if not token:
        raise ValueError("google-access-token is empty")

    encoded_sheet_id = quote(sheet_id, safe="")
    encoded_range = quote(sheet_range, safe="!$':(),_-")
    url = (
        "https://sheets.googleapis.com/v4/spreadsheets/"
        f"{encoded_sheet_id}/values/{encoded_range}?majorDimension=ROWS"
    )
    req = Request(
        url=url,
        headers={
            "User-Agent": "nanogram-comment-importer/1.0",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urlopen(req, timeout=30) as response:
            raw = response.read()
    except HTTPError as exc:
        hint = ""
        if exc.code in {401, 403}:
            hint = " Check token validity/scopes and that the sheet is shared to that identity."
        raise RuntimeError(
            f"Google Sheets API request failed ({exc.code} {exc.reason}).{hint}"
        ) from exc
    payload = json.loads(raw.decode("utf-8"))

    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        message = str(payload["error"].get("message") or "Google Sheets API error")
        raise ValueError(message)

    rows = payload.get("values")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Google Sheets API returned no rows for the selected range.")

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    for row in rows:
        if isinstance(row, list):
            writer.writerow([str(cell) for cell in row])
        else:
            writer.writerow([str(row)])
    return out.getvalue()


def find_matching_header(fieldnames: list[str], desired: str, aliases: list[str]) -> str | None:
    canon_to_raw: dict[str, str] = {}
    for name in fieldnames:
        canon = canonicalize_header(name)
        if canon and canon not in canon_to_raw:
            canon_to_raw[canon] = name

    candidates = [desired] + aliases
    for candidate in candidates:
        canon = canonicalize_header(candidate)
        if canon in canon_to_raw:
            return canon_to_raw[canon]
    return None


def resolve_columns(reader: csv.DictReader, args: argparse.Namespace) -> dict[str, str]:
    if not reader.fieldnames:
        raise ValueError("CSV appears to have no header row.")
    headers = [str(name) for name in reader.fieldnames if name is not None]
    columns = {
        "post_id": find_matching_header(
            headers, args.post_id_column, ["post_id", "post id", "postid", "post"]
        ),
        "avatar": find_matching_header(
            headers, args.avatar_column, ["emoji", "avatar-emoji", "avatar emoji"]
        ),
        "name": find_matching_header(
            headers, args.name_column, ["alias", "username", "user", "display name"]
        ),
        "comment": find_matching_header(
            headers, args.comment_column, ["comment", "text", "message", "actual-comment"]
        ),
    }

    missing_required = []
    for key in ("post_id", "comment"):
        if not columns.get(key):
            missing_required.append(key)
    if missing_required:
        raise ValueError(
            "CSV missing required column(s): "
            + ", ".join(missing_required)
            + ". Use --post-id-column/--comment-column if your headers differ."
        )
    return {k: v for k, v in columns.items() if v}


def resolve_metadata_file(metadata_dir: Path, post_id: str) -> Path | None:
    for candidate_post_id in post_id_candidates(post_id):
        candidates = [
            metadata_dir / "posts" / f"{candidate_post_id}.json",
            metadata_dir / "reels" / f"{candidate_post_id}.json",
            metadata_dir / f"{candidate_post_id}.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
    return None


def build_new_metadata_file(metadata_dir: Path, post_id: str) -> Path:
    path = metadata_path_for_post_id(metadata_dir, post_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = default_metadata_payload(post_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def metadata_path_for_post_id(metadata_dir: Path, post_id: str) -> Path:
    canonical_post_id = canonical_post_id_for_metadata(post_id)
    kind = infer_post_kind(canonical_post_id)
    bucket = "reels" if kind == "reel" else "posts"
    return metadata_dir / bucket / f"{canonical_post_id}.json"


def default_metadata_payload(post_id: str) -> dict[str, Any]:
    canonical_post_id = canonical_post_id_for_metadata(post_id)
    kind = infer_post_kind(canonical_post_id)
    return {
        "post_id": canonical_post_id,
        "kind": kind,
        "caption": "",
        "source_file": "",
        "visibility": "private",
        "like_count": None,
        "comments": {},
        "comment_avatars": {},
        "_note": "Imported from CSV by import_sheet_comments.py",
    }


def parse_existing_metadata(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def ensure_unique_name(comments: dict[str, str], desired_name: str, desired_comment: str) -> str:
    base = desired_name
    if base not in comments:
        return base
    if str(comments.get(base, "")).strip() == desired_comment.strip():
        return base
    index = 2
    while True:
        candidate = f"{base} ({index})"
        if candidate not in comments:
            return candidate
        if str(comments.get(candidate, "")).strip() == desired_comment.strip():
            return candidate
        index += 1


def import_csv_rows(args: argparse.Namespace) -> int:
    if args.google_sheet_id:
        csv_text = fetch_google_sheet_as_csv(
            sheet_id=args.google_sheet_id,
            sheet_range=args.google_sheet_range,
            access_token=args.google_access_token,
        )
    else:
        csv_text = read_csv_text(str(args.csv or ""), args.encoding)
    reader = csv.DictReader(io.StringIO(csv_text))
    columns = resolve_columns(reader, args)

    metadata_dir = args.metadata_dir
    metadata_dir.mkdir(parents=True, exist_ok=True)

    scanned = 0
    updated_rows = 0
    written_files = 0
    missing_post_ids: set[str] = set()
    modified_paths: set[Path] = set()

    for row in reader:
        scanned += 1
        if not isinstance(row, dict):
            continue

        post_id = str(row.get(columns["post_id"], "")).strip()
        comment_text = str(row.get(columns["comment"], "")).strip()
        if not post_id or not comment_text:
            continue

        raw_name = str(row.get(columns.get("name", ""), "")).strip() if columns.get("name") else ""
        raw_avatar = str(row.get(columns.get("avatar", ""), "")).strip() if columns.get("avatar") else ""
        name = raw_name or str(args.default_name).strip() or "Anonymous"

        metadata_path = resolve_metadata_file(metadata_dir, post_id)
        created_payload: dict[str, Any] | None = None
        if metadata_path is None:
            if not args.create_missing:
                missing_post_ids.add(post_id)
                continue
            metadata_path = metadata_path_for_post_id(metadata_dir, post_id)
            if args.dry_run:
                created_payload = default_metadata_payload(post_id)
            else:
                metadata_path = build_new_metadata_file(metadata_dir, post_id)

        payload = created_payload if created_payload is not None else parse_existing_metadata(metadata_path)
        comments_value = payload.get("comments")
        comments: dict[str, str] = {}
        if isinstance(comments_value, dict):
            for key, value in comments_value.items():
                user = str(key).strip()
                text = str(value).strip()
                if user and text:
                    comments[user] = text

        resolved_name = ensure_unique_name(comments, name, comment_text)
        if comments.get(resolved_name) != comment_text:
            comments[resolved_name] = comment_text

        avatars_value = payload.get("comment_avatars")
        avatars: dict[str, str] = {}
        if isinstance(avatars_value, dict):
            for key, value in avatars_value.items():
                user = str(key).strip()
                avatar = str(value).strip()
                if user and avatar:
                    avatars[user] = avatar

        if raw_avatar:
            avatars[resolved_name] = raw_avatar

        payload["comments"] = comments
        if avatars:
            payload["comment_avatars"] = avatars

        updated_rows += 1
        modified_paths.add(metadata_path)
        if not args.dry_run:
            metadata_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    written_files = len(modified_paths)

    print(f"CSV rows scanned: {scanned}")
    print(f"Rows imported: {updated_rows}")
    print(f"Metadata files updated: {written_files}")
    if missing_post_ids:
        print(
            "Missing metadata for post-id(s): "
            + ", ".join(sorted(missing_post_ids)[:20])
            + (" ..." if len(missing_post_ids) > 20 else "")
        )
        print("Use --create-missing to create missing metadata files.")

    return 0


def main() -> int:
    args = parse_args()
    try:
        return import_csv_rows(args)
    except Exception as exc:  # pragma: no cover - CLI error path
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
