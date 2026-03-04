#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CFG_CONFIG_PATH=""
CFG_THEME_NAME=""
CFG_EXPORT_DIR=""
CFG_OUTPUT_DIR=""
CFG_METADATA_DIR=""
CFG_APP_HTML=""
CFG_GATE_HTML=""
CFG_RECOVER_SCRIPT=""
CFG_BUNDLE_SCRIPT=""
CFG_REGISTRY_PATH=""
CFG_ASSETS_DIR=""
CFG_DOMAIN_NAME=""
CFG_USE_SPECIAL_COMMENTS=""
CFG_COMMENT_AVATAR_EMOJIS=""
CFG_THEME_DIR=""
CFG_DEBUG_ITERATIONS=""
CFG_RELEASE_ITERATIONS=""
CFG_LOGIN_PASSWORD=""
CFG_TEST_PASSWORD=""
CFG_IMPORT_COMMENTS_SCRIPT="$SCRIPT_DIR/src/scripts/import_sheet_comments.py"

IMPORT_COMMENTS_CSV=""
IMPORT_COMMENTS_GOOGLE_SHEET_ID=""
IMPORT_COMMENTS_GOOGLE_SHEET_RANGE=""
IMPORT_COMMENTS_GOOGLE_ACCESS_TOKEN=""
IMPORT_COMMENTS_GOOGLE_ACCESS_TOKEN_ENV=""
IMPORT_COMMENTS_ENCODING=""
IMPORT_COMMENTS_POST_ID_COLUMN=""
IMPORT_COMMENTS_AVATAR_COLUMN=""
IMPORT_COMMENTS_NAME_COLUMN=""
IMPORT_COMMENTS_COMMENT_COLUMN=""
IMPORT_COMMENTS_DEFAULT_NAME=""
IMPORT_COMMENTS_CREATE_MISSING=0
IMPORT_COMMENTS_DRY_RUN=0
PARSE_CONSUMED=0
COMMENT_IMPORT_CMD=()

LIVE_SERVER_PID=""
LIVE_SERVER_LOG=""

timestamp_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log() {
  local level="$1"
  shift
  local fd=1
  if [[ "$level" == "ERROR" ]]; then
    fd=2
  fi
  printf "%s %-5s %s\n" "$(timestamp_utc)" "$level" "$*" >&"$fd"
}

info() {
  log "INFO" "$*"
}

warn() {
  log "WARN" "$*"
}

error() {
  log "ERROR" "$*"
}

fatal() {
  error "$*"
  exit 1
}

print_section() {
  local title="$1"
  printf "\n%s\n" "$title"
  printf "%s\n" "$(printf '%*s' "${#title}" "" | tr ' ' '-')"
}

print_kv() {
  local key="$1"
  local value="$2"
  printf "  %-22s %s\n" "$key" "$value"
}

usage() {
  cat <<'USAGE'
Usage:
  ./nanogram.sh init-config [--output <path>] [--force]
  ./nanogram.sh release --config <path>
  ./nanogram.sh debug --config <path>
  ./nanogram.sh live --config <path> [--mode debug|release] [--port 8080] [--interval 1]
  ./nanogram.sh comments [--config <path>] [--metadata-dir <path>] [comments source/options]

Commands:
  init-config   Generate a config template file.
  release       Production build using LOGIN_PASSWORD.
  debug         Test build using TEST_BUILD_PASSWORD.
  live          Build once, serve OUTPUT_DIR, and restart server when OUTPUT_DIR changes.
  comments      Import comments into metadata without running a build.

Notes:
  - --config is required for release/debug/live.
  - init-config defaults to writing ./build/config.
  - Relative paths inside config are resolved from the project root.

Comments Command Options:
  --csv <path|url>                     Import comments from CSV file/URL.
  --sheet-id <id>                      Import comments from Google Sheets API.
  --sheet-range <a1>                   Sheets API A1 range (default: A:Z).
  --access-token <tok>                 OAuth bearer token for Sheets API mode.
  --access-token-env <name>            Env var name for Sheets API token.
  --encoding <enc>                     CSV encoding override (default utf-8-sig).
  --post-id-column <name>              CSV post-id column header name.
  --avatar-column <name>               CSV avatar column header name.
  --name-column <name>                 CSV name/alias column header name.
  --comment-column <name>              CSV comment column header name.
  --default-name <name>                Fallback alias for empty names.
  --create-missing                     Create metadata files for missing post ids.
  --dry-run                            Parse and report without writing metadata.
  (same options can be set in config as COMMENTS_* keys; CLI flags take precedence)
USAGE
}

write_config_template() {
  local output_path="$1"
  local force="${2:-0}"

  if [[ -e "$output_path" && "$force" != "1" ]]; then
    fatal "config file already exists: $output_path (use --force to overwrite)"
  fi

  mkdir -p "$(dirname "$output_path")"

  cat > "$output_path" <<'EOF'
# Nanogram build configuration
# Keep this file private. It contains passwords.

# Required
INSTAGRAM_EXPORT_DIR="./path-to-instagram-export"
OUTPUT_DIR="./build/public"
METADATA_DIR="./build/metadata"
LOGIN_PASSWORD="replace-with-release-password"
TEST_BUILD_PASSWORD="replace-with-debug-password"

# Optional
THEME_NAME="default"
APP_HTML_PATH="./src/nanogram.app.html"
GATE_HTML_PATH="./src/nanogram.html"
REGISTRY_PATH="./build/registry.json"
ASSETS_DIR="./build/assets"
DOMAIN_NAME=""
# If 1, commenter images are read from ASSETS_DIR/img/people when present.
USE_SPECIAL_COMMENTS="1"
RECOVER_SCRIPT="./src/scripts/recover_nanogram_export.py"
BUNDLE_SCRIPT="./src/scripts/build_protected_bundle.mjs"
DEBUG_KDF_ITERATIONS="10000"
RELEASE_KDF_ITERATIONS="250000"
# JSON array string or comma/newline-separated list.
COMMENT_AVATAR_EMOJIS='["👶","🧒","👦","👧","🧑","👱","👨","🧔","🧔‍♂️","🧔‍♀️","👨‍🦰","👨‍🦱","👨‍🦳","👨‍🦲","👩","👩‍🦰","🧑‍🦰","👩‍🦱","🧑‍🦱","👩‍🦳","🧑‍🦳","👩‍🦲","🧑‍🦲","👱‍♀️","👱‍♂️","🧓","👴","👵","🧏","🧏‍♂️","🧏‍♀️","👳","👳‍♂️","👳‍♀️","👲","🧕","👼","🗣️","👤","👥","🫂"]'

# Optional: defaults for `./nanogram.sh comments`.
# Set exactly one source (COMMENTS_CSV or COMMENTS_GOOGLE_SHEET_ID).
COMMENTS_CSV=""
COMMENTS_GOOGLE_SHEET_ID=""
COMMENTS_GOOGLE_SHEET_RANGE="A:Z"
COMMENTS_GOOGLE_ACCESS_TOKEN=""
COMMENTS_GOOGLE_ACCESS_TOKEN_ENV="GOOGLE_ACCESS_TOKEN"
COMMENTS_ENCODING="utf-8-sig"
COMMENTS_POST_ID_COLUMN="post-id"
COMMENTS_AVATAR_COLUMN="avatar"
COMMENTS_NAME_COLUMN="name"
COMMENTS_COMMENT_COLUMN="actual comment"
COMMENTS_DEFAULT_NAME="Anonymous"
COMMENTS_CREATE_MISSING="0"
COMMENTS_DRY_RUN="0"
EOF

  chmod 600 "$output_path" || true
  info "config template generated at: $output_path"
}

resolve_from_project() {
  local raw_path="$1"
  if [[ "$raw_path" == /* ]]; then
    printf '%s\n' "$raw_path"
  else
    printf '%s\n' "$SCRIPT_DIR/$raw_path"
  fi
}

ensure_nonempty_config_value() {
  local key="$1"
  local value="$2"
  if [[ -z "${value// }" ]]; then
    fatal "missing required config value: $key"
  fi
}

ensure_safe_clear_dir() {
  local path="$1"
  if [[ -z "${path// }" || "$path" == "/" ]]; then
    fatal "refusing to clear unsafe directory path: '$path'"
  fi

  mkdir -p "$path"
  # Preserve local git metadata when OUTPUT_DIR is itself a repository.
  find "$path" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
}

is_valid_positive_int() {
  local value="$1"
  [[ "$value" =~ ^[0-9]+$ ]] && (( value > 0 ))
}

is_http_url() {
  local value="$1"
  [[ "$value" =~ ^https?:// ]]
}

normalize_domain_for_cname() {
  local raw="$1"
  local value
  value="$(printf '%s' "$raw" | xargs)"
  value="${value#http://}"
  value="${value#https://}"
  value="${value%%/*}"
  value="${value%%:*}"
  value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"

  if [[ -z "${value// }" ]]; then
    fatal "DOMAIN_NAME is set but empty after normalization"
  fi
  if [[ "$value" == .* || "$value" == *. ]]; then
    fatal "DOMAIN_NAME must not start/end with '.' (got: $raw)"
  fi
  if [[ "$value" == *..* ]]; then
    fatal "DOMAIN_NAME must not contain consecutive dots (got: $raw)"
  fi
  if [[ ! "$value" =~ ^[a-z0-9.-]+$ ]]; then
    fatal "DOMAIN_NAME contains invalid characters (got: $raw)"
  fi
  printf '%s\n' "$value"
}

resolve_build_input_source() {
  local value="$1"
  if is_http_url "$value"; then
    printf '%s\n' "$value"
  else
    resolve_from_project "$value"
  fi
}

comment_import_requested() {
  [[ -n "${IMPORT_COMMENTS_CSV// }" ]] || [[ -n "${IMPORT_COMMENTS_GOOGLE_SHEET_ID// }" ]]
}

validate_comment_import_options() {
  local has_csv=0
  local has_sheet=0
  if [[ -n "${IMPORT_COMMENTS_CSV// }" ]]; then
    has_csv=1
  fi
  if [[ -n "${IMPORT_COMMENTS_GOOGLE_SHEET_ID// }" ]]; then
    has_sheet=1
  fi

  if (( has_csv == 1 && has_sheet == 1 )); then
    fatal "comment import supports one source at a time: use either --csv or --sheet-id"
  fi
  if (( has_csv == 0 && has_sheet == 0 )); then
    fatal "comment import options were provided without a source; add --csv or --sheet-id"
  fi

  if (( has_csv == 1 )); then
    if [[ -n "${IMPORT_COMMENTS_GOOGLE_SHEET_RANGE// }" || -n "${IMPORT_COMMENTS_GOOGLE_ACCESS_TOKEN// }" || -n "${IMPORT_COMMENTS_GOOGLE_ACCESS_TOKEN_ENV// }" ]]; then
      warn "Google Sheets-specific comment import options were provided with --csv and will be ignored"
    fi
  fi
}

build_comment_import_cmd() {
  [[ -f "$CFG_IMPORT_COMMENTS_SCRIPT" ]] || fatal "missing comment import script: $CFG_IMPORT_COMMENTS_SCRIPT"
  COMMENT_IMPORT_CMD=(
    python3 "$CFG_IMPORT_COMMENTS_SCRIPT"
    --metadata-dir "$CFG_METADATA_DIR"
  )

  if [[ -n "${IMPORT_COMMENTS_CSV// }" ]]; then
    COMMENT_IMPORT_CMD+=(--csv "$(resolve_build_input_source "$IMPORT_COMMENTS_CSV")")
  else
    COMMENT_IMPORT_CMD+=(--google-sheet-id "$IMPORT_COMMENTS_GOOGLE_SHEET_ID")
    if [[ -n "${IMPORT_COMMENTS_GOOGLE_SHEET_RANGE// }" ]]; then
      COMMENT_IMPORT_CMD+=(--google-sheet-range "$IMPORT_COMMENTS_GOOGLE_SHEET_RANGE")
    fi
    if [[ -n "${IMPORT_COMMENTS_GOOGLE_ACCESS_TOKEN// }" ]]; then
      COMMENT_IMPORT_CMD+=(--google-access-token "$IMPORT_COMMENTS_GOOGLE_ACCESS_TOKEN")
    fi
    if [[ -n "${IMPORT_COMMENTS_GOOGLE_ACCESS_TOKEN_ENV// }" ]]; then
      COMMENT_IMPORT_CMD+=(--google-access-token-env "$IMPORT_COMMENTS_GOOGLE_ACCESS_TOKEN_ENV")
    fi
  fi

  if [[ -n "${IMPORT_COMMENTS_ENCODING// }" ]]; then
    COMMENT_IMPORT_CMD+=(--encoding "$IMPORT_COMMENTS_ENCODING")
  fi
  if [[ -n "${IMPORT_COMMENTS_POST_ID_COLUMN// }" ]]; then
    COMMENT_IMPORT_CMD+=(--post-id-column "$IMPORT_COMMENTS_POST_ID_COLUMN")
  fi
  if [[ -n "${IMPORT_COMMENTS_AVATAR_COLUMN// }" ]]; then
    COMMENT_IMPORT_CMD+=(--avatar-column "$IMPORT_COMMENTS_AVATAR_COLUMN")
  fi
  if [[ -n "${IMPORT_COMMENTS_NAME_COLUMN// }" ]]; then
    COMMENT_IMPORT_CMD+=(--name-column "$IMPORT_COMMENTS_NAME_COLUMN")
  fi
  if [[ -n "${IMPORT_COMMENTS_COMMENT_COLUMN// }" ]]; then
    COMMENT_IMPORT_CMD+=(--comment-column "$IMPORT_COMMENTS_COMMENT_COLUMN")
  fi
  if [[ -n "${IMPORT_COMMENTS_DEFAULT_NAME// }" ]]; then
    COMMENT_IMPORT_CMD+=(--default-name "$IMPORT_COMMENTS_DEFAULT_NAME")
  fi
  if [[ "$IMPORT_COMMENTS_CREATE_MISSING" == "1" ]]; then
    COMMENT_IMPORT_CMD+=(--create-missing)
  fi
  if [[ "$IMPORT_COMMENTS_DRY_RUN" == "1" ]]; then
    COMMENT_IMPORT_CMD+=(--dry-run)
  fi
}

emit_comment_import_summary() {
  local import_log="$1"
  if [[ ! -f "$import_log" ]]; then
    return 0
  fi

  print_section "Comment Import Summary"
  while IFS=$'\t' read -r key value; do
    [[ -n "${key// }" ]] || continue
    print_kv "$key" "$value"
  done < <(python3 - "$import_log" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
patterns = [
    (r"^CSV rows scanned:\s*(.+)$", "rows_scanned"),
    (r"^Rows imported:\s*(.+)$", "rows_imported"),
    (r"^Metadata files updated:\s*(.+)$", "metadata_files_updated"),
    (r"^Missing metadata for post-id\(s\):\s*(.+)$", "missing_post_ids"),
]
for line in lines:
    for regex, key in patterns:
        match = re.match(regex, line.strip())
        if match:
            print(f"{key}\t{match.group(1).strip()}")
PY
)
}

parse_comments_command_option() {
  PARSE_CONSUMED=0

  local option="${1:-}"
  case "$option" in
    --csv)
      [[ $# -ge 2 ]] || fatal "--csv requires a value"
      IMPORT_COMMENTS_CSV="$2"
      PARSE_CONSUMED=2
      ;;
    --sheet-id|--google-sheet-id)
      [[ $# -ge 2 ]] || fatal "--sheet-id requires a value"
      IMPORT_COMMENTS_GOOGLE_SHEET_ID="$2"
      PARSE_CONSUMED=2
      ;;
    --sheet-range|--google-sheet-range)
      [[ $# -ge 2 ]] || fatal "--sheet-range requires a value"
      IMPORT_COMMENTS_GOOGLE_SHEET_RANGE="$2"
      PARSE_CONSUMED=2
      ;;
    --access-token|--google-access-token)
      [[ $# -ge 2 ]] || fatal "--access-token requires a value"
      IMPORT_COMMENTS_GOOGLE_ACCESS_TOKEN="$2"
      PARSE_CONSUMED=2
      ;;
    --access-token-env|--google-access-token-env)
      [[ $# -ge 2 ]] || fatal "--access-token-env requires a value"
      IMPORT_COMMENTS_GOOGLE_ACCESS_TOKEN_ENV="$2"
      PARSE_CONSUMED=2
      ;;
    --encoding)
      [[ $# -ge 2 ]] || fatal "--encoding requires a value"
      IMPORT_COMMENTS_ENCODING="$2"
      PARSE_CONSUMED=2
      ;;
    --post-id-column)
      [[ $# -ge 2 ]] || fatal "--post-id-column requires a value"
      IMPORT_COMMENTS_POST_ID_COLUMN="$2"
      PARSE_CONSUMED=2
      ;;
    --avatar-column)
      [[ $# -ge 2 ]] || fatal "--avatar-column requires a value"
      IMPORT_COMMENTS_AVATAR_COLUMN="$2"
      PARSE_CONSUMED=2
      ;;
    --name-column)
      [[ $# -ge 2 ]] || fatal "--name-column requires a value"
      IMPORT_COMMENTS_NAME_COLUMN="$2"
      PARSE_CONSUMED=2
      ;;
    --comment-column)
      [[ $# -ge 2 ]] || fatal "--comment-column requires a value"
      IMPORT_COMMENTS_COMMENT_COLUMN="$2"
      PARSE_CONSUMED=2
      ;;
    --default-name)
      [[ $# -ge 2 ]] || fatal "--default-name requires a value"
      IMPORT_COMMENTS_DEFAULT_NAME="$2"
      PARSE_CONSUMED=2
      ;;
    --create-missing)
      IMPORT_COMMENTS_CREATE_MISSING=1
      PARSE_CONSUMED=1
      ;;
    --dry-run)
      IMPORT_COMMENTS_DRY_RUN=1
      PARSE_CONSUMED=1
      ;;
  esac
}

to_bool_flag() {
  local key="$1"
  local raw="$2"
  local value
  value="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]' | xargs)"
  case "$value" in
    1|true|yes|on)
      printf '1\n'
      ;;
    0|false|no|off|"")
      printf '0\n'
      ;;
    *)
      fatal "$key must be one of: 1,0,true,false,yes,no,on,off (got: $raw)"
      ;;
  esac
}

load_comment_import_defaults_from_config() {
  if [[ -z "${IMPORT_COMMENTS_CSV// }" && -n "${COMMENTS_CSV:-}" ]]; then
    IMPORT_COMMENTS_CSV="$COMMENTS_CSV"
  fi
  if [[ -z "${IMPORT_COMMENTS_GOOGLE_SHEET_ID// }" && -n "${COMMENTS_GOOGLE_SHEET_ID:-}" ]]; then
    IMPORT_COMMENTS_GOOGLE_SHEET_ID="$COMMENTS_GOOGLE_SHEET_ID"
  fi
  if [[ -z "${IMPORT_COMMENTS_GOOGLE_SHEET_RANGE// }" && -n "${COMMENTS_GOOGLE_SHEET_RANGE:-}" ]]; then
    IMPORT_COMMENTS_GOOGLE_SHEET_RANGE="$COMMENTS_GOOGLE_SHEET_RANGE"
  fi
  if [[ -z "${IMPORT_COMMENTS_GOOGLE_ACCESS_TOKEN// }" && -n "${COMMENTS_GOOGLE_ACCESS_TOKEN:-}" ]]; then
    IMPORT_COMMENTS_GOOGLE_ACCESS_TOKEN="$COMMENTS_GOOGLE_ACCESS_TOKEN"
  fi
  if [[ -z "${IMPORT_COMMENTS_GOOGLE_ACCESS_TOKEN_ENV// }" && -n "${COMMENTS_GOOGLE_ACCESS_TOKEN_ENV:-}" ]]; then
    IMPORT_COMMENTS_GOOGLE_ACCESS_TOKEN_ENV="$COMMENTS_GOOGLE_ACCESS_TOKEN_ENV"
  fi
  if [[ -z "${IMPORT_COMMENTS_ENCODING// }" && -n "${COMMENTS_ENCODING:-}" ]]; then
    IMPORT_COMMENTS_ENCODING="$COMMENTS_ENCODING"
  fi
  if [[ -z "${IMPORT_COMMENTS_POST_ID_COLUMN// }" && -n "${COMMENTS_POST_ID_COLUMN:-}" ]]; then
    IMPORT_COMMENTS_POST_ID_COLUMN="$COMMENTS_POST_ID_COLUMN"
  fi
  if [[ -z "${IMPORT_COMMENTS_AVATAR_COLUMN// }" && -n "${COMMENTS_AVATAR_COLUMN:-}" ]]; then
    IMPORT_COMMENTS_AVATAR_COLUMN="$COMMENTS_AVATAR_COLUMN"
  fi
  if [[ -z "${IMPORT_COMMENTS_NAME_COLUMN// }" && -n "${COMMENTS_NAME_COLUMN:-}" ]]; then
    IMPORT_COMMENTS_NAME_COLUMN="$COMMENTS_NAME_COLUMN"
  fi
  if [[ -z "${IMPORT_COMMENTS_COMMENT_COLUMN// }" && -n "${COMMENTS_COMMENT_COLUMN:-}" ]]; then
    IMPORT_COMMENTS_COMMENT_COLUMN="$COMMENTS_COMMENT_COLUMN"
  fi
  if [[ -z "${IMPORT_COMMENTS_DEFAULT_NAME// }" && -n "${COMMENTS_DEFAULT_NAME:-}" ]]; then
    IMPORT_COMMENTS_DEFAULT_NAME="$COMMENTS_DEFAULT_NAME"
  fi
  if [[ "${IMPORT_COMMENTS_CREATE_MISSING}" != "1" && -n "${COMMENTS_CREATE_MISSING:-}" ]]; then
    IMPORT_COMMENTS_CREATE_MISSING="$(to_bool_flag "COMMENTS_CREATE_MISSING" "$COMMENTS_CREATE_MISSING")"
  fi
  if [[ "${IMPORT_COMMENTS_DRY_RUN}" != "1" && -n "${COMMENTS_DRY_RUN:-}" ]]; then
    IMPORT_COMMENTS_DRY_RUN="$(to_bool_flag "COMMENTS_DRY_RUN" "$COMMENTS_DRY_RUN")"
  fi
}

load_comment_import_config() {
  local config_path_raw="$1"
  local config_abs
  config_abs="$(cd "$(dirname "$config_path_raw")" && pwd)/$(basename "$config_path_raw")"
  if [[ ! -f "$config_abs" ]]; then
    fatal "config file not found: $config_abs"
  fi

  # shellcheck source=/dev/null
  source "$config_abs"
  CFG_CONFIG_PATH="$config_abs"

  local configured_metadata_dir="./build/metadata"
  if [[ -n "${METADATA_DIR:-}" ]]; then
    configured_metadata_dir="$METADATA_DIR"
  fi
  CFG_METADATA_DIR="$(resolve_from_project "$configured_metadata_dir")"
  load_comment_import_defaults_from_config
}

load_and_resolve_config() {
  local config_path_raw="$1"
  if [[ -z "${config_path_raw// }" ]]; then
    fatal "--config is required"
  fi

  local config_abs
  config_abs="$(cd "$(dirname "$config_path_raw")" && pwd)/$(basename "$config_path_raw")"
  if [[ ! -f "$config_abs" ]]; then
    fatal "config file not found: $config_abs"
  fi

  # shellcheck source=/dev/null
  source "$config_abs"

  ensure_nonempty_config_value "INSTAGRAM_EXPORT_DIR" "${INSTAGRAM_EXPORT_DIR:-}"
  ensure_nonempty_config_value "OUTPUT_DIR" "${OUTPUT_DIR:-}"
  ensure_nonempty_config_value "LOGIN_PASSWORD" "${LOGIN_PASSWORD:-}"
  ensure_nonempty_config_value "TEST_BUILD_PASSWORD" "${TEST_BUILD_PASSWORD:-}"

  CFG_CONFIG_PATH="$config_abs"
  CFG_THEME_NAME="${THEME_NAME:-default}"
  CFG_EXPORT_DIR="$(resolve_from_project "$INSTAGRAM_EXPORT_DIR")"
  CFG_OUTPUT_DIR="$(resolve_from_project "$OUTPUT_DIR")"
  local configured_metadata_dir=""
  if [[ -n "${METADATA_DIR:-}" ]]; then
    configured_metadata_dir="$METADATA_DIR"
    if [[ "$configured_metadata_dir" == "./src/metadata" || "$configured_metadata_dir" == "src/metadata" || "$configured_metadata_dir" == "./config/metadata" || "$configured_metadata_dir" == "config/metadata" ]]; then
      warn "config METADATA_DIR points to a deprecated path; use ./build/metadata"
    fi
  else
    configured_metadata_dir="./build/metadata"
    warn "config key METADATA_DIR not set; defaulting to $configured_metadata_dir"
  fi
  CFG_METADATA_DIR="$(resolve_from_project "$configured_metadata_dir")"
  CFG_APP_HTML="$(resolve_from_project "${APP_HTML_PATH:-./src/nanogram.app.html}")"
  CFG_GATE_HTML="$(resolve_from_project "${GATE_HTML_PATH:-./src/nanogram.html}")"
  CFG_RECOVER_SCRIPT="$(resolve_from_project "${RECOVER_SCRIPT:-./src/scripts/recover_nanogram_export.py}")"
  CFG_BUNDLE_SCRIPT="$(resolve_from_project "${BUNDLE_SCRIPT:-./src/scripts/build_protected_bundle.mjs}")"
  local configured_registry_path=""
  if [[ -n "${REGISTRY_PATH:-}" ]]; then
    configured_registry_path="$REGISTRY_PATH"
    if [[ "$configured_registry_path" == "./src/nanogram-data/posts_registry.json" || "$configured_registry_path" == "src/nanogram-data/posts_registry.json" || "$configured_registry_path" == "./config/nanogram-data/posts_registry.json" || "$configured_registry_path" == "config/nanogram-data/posts_registry.json" || "$configured_registry_path" == "./build/nanogram-data/posts_registry.json" || "$configured_registry_path" == "build/nanogram-data/posts_registry.json" ]]; then
      warn "config REGISTRY_PATH points to a deprecated path; use ./build/registry.json"
    fi
  else
    configured_registry_path="./build/registry.json"
    warn "config key REGISTRY_PATH not set; defaulting to $configured_registry_path"
  fi
  CFG_REGISTRY_PATH="$(resolve_from_project "$configured_registry_path")"
  local configured_assets_dir=""
  if [[ -n "${ASSETS_DIR:-}" ]]; then
    configured_assets_dir="$ASSETS_DIR"
    if [[ "$configured_assets_dir" == "./src/assets" || "$configured_assets_dir" == "src/assets" ]]; then
      warn "config ASSETS_DIR points to deprecated src/assets; use ./build/assets"
    fi
  elif [[ -n "${GRAM_DIR:-}" ]]; then
    configured_assets_dir="$GRAM_DIR"
    warn "config key GRAM_DIR is deprecated; use ASSETS_DIR instead"
  else
    configured_assets_dir="./build/assets"
  fi
  CFG_ASSETS_DIR="$(resolve_from_project "$configured_assets_dir")"
  CFG_DOMAIN_NAME=""
  if [[ -n "${DOMAIN_NAME:-}" ]]; then
    CFG_DOMAIN_NAME="$(normalize_domain_for_cname "$DOMAIN_NAME")"
  fi
  CFG_USE_SPECIAL_COMMENTS="$(to_bool_flag "USE_SPECIAL_COMMENTS" "${USE_SPECIAL_COMMENTS:-1}")"
  CFG_COMMENT_AVATAR_EMOJIS="${COMMENT_AVATAR_EMOJIS:-}"
  CFG_THEME_DIR="$SCRIPT_DIR/src/themes/$CFG_THEME_NAME"
  CFG_DEBUG_ITERATIONS="${DEBUG_KDF_ITERATIONS:-10000}"
  CFG_RELEASE_ITERATIONS="${RELEASE_KDF_ITERATIONS:-250000}"
  CFG_LOGIN_PASSWORD="${LOGIN_PASSWORD}"
  CFG_TEST_PASSWORD="${TEST_BUILD_PASSWORD}"

  if [[ ! "$CFG_THEME_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
    fatal "THEME_NAME must match [A-Za-z0-9._-], got: $CFG_THEME_NAME"
  fi

  local posts_json_file="$CFG_EXPORT_DIR/your_instagram_activity/media/posts_1.json"
  local posts_html_file="$CFG_EXPORT_DIR/your_instagram_activity/media/posts_1.html"
  if [[ ! -f "$posts_json_file" && ! -f "$posts_html_file" ]]; then
    fatal "invalid export directory: $CFG_EXPORT_DIR (missing posts_1.json/posts_1.html)"
  fi

  [[ -f "$CFG_APP_HTML" ]] || fatal "missing app HTML: $CFG_APP_HTML"
  [[ -f "$CFG_GATE_HTML" ]] || fatal "missing gate HTML: $CFG_GATE_HTML"
  [[ -f "$CFG_RECOVER_SCRIPT" ]] || fatal "missing recover script: $CFG_RECOVER_SCRIPT"
  [[ -f "$CFG_BUNDLE_SCRIPT" ]] || fatal "missing bundle script: $CFG_BUNDLE_SCRIPT"
  [[ -d "$CFG_THEME_DIR" ]] || fatal "missing theme directory: $CFG_THEME_DIR"

  if ! grep -Fq "themes/$CFG_THEME_NAME/" "$CFG_APP_HTML"; then
    fatal "app HTML does not reference theme '$CFG_THEME_NAME' (expected themes/$CFG_THEME_NAME/)"
  fi
}

select_build_secret() {
  local mode="$1"
  local password=""
  local iterations=""

  case "$mode" in
    release)
      password="$CFG_LOGIN_PASSWORD"
      iterations="$CFG_RELEASE_ITERATIONS"
      ;;
    debug)
      password="$CFG_TEST_PASSWORD"
      iterations="$CFG_DEBUG_ITERATIONS"
      ;;
    *)
      fatal "unknown build mode: $mode"
      ;;
  esac

  if [[ -z "${password// }" ]]; then
    fatal "selected build password is empty for mode '$mode'"
  fi
  if ! [[ "$iterations" =~ ^[0-9]+$ ]] || (( iterations < 1000 )); then
    fatal "KDF iterations must be an integer >= 1000 (got: $iterations)"
  fi

  printf '%s\n' "$password|$iterations"
}

run_step() {
  local step_name="$1"
  local log_file="$2"
  shift 2

  info "$step_name"
  if "$@" >"$log_file" 2>&1; then
    info "$step_name completed"
    return 0
  fi

  error "$step_name failed"
  if [[ -f "$log_file" ]]; then
    error "showing last 30 log lines:"
    tail -n 30 "$log_file" >&2 || true
    error "full log: $log_file"
  fi
  return 1
}

emit_recover_summary() {
  local manifest_path="$1"
  if [[ ! -f "$manifest_path" ]]; then
    warn "recovery manifest not found: $manifest_path"
    return 0
  fi

  print_section "Recovery Summary"
  while IFS=$'\t' read -r key value; do
    print_kv "$key" "$value"
  done < <(python3 - "$manifest_path" <<'PY'
import json
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
data = json.loads(manifest.read_text(encoding="utf-8"))
rows = [
    ("posts_found", str(data.get("posts_found", 0))),
    ("posts_recovered", str(data.get("posts_recovered", 0))),
    ("reels_found", str(data.get("reels_found", 0))),
    ("reels_recovered", str(data.get("reels_recovered", 0))),
    ("forms_entries_total", str(data.get("forms_entries_total", 0))),
    ("forms_entries_added", str(data.get("forms_entries_added", 0))),
]
for key, value in rows:
    print(f"{key}\t{value}")
PY
)
}

emit_bundle_summary() {
  local bundle_path="$1"
  local objs_dir="$2"
  if [[ ! -f "$bundle_path" ]]; then
    warn "bundle file not found: $bundle_path"
    return 0
  fi

  print_section "Bundle Summary"
  while IFS=$'\t' read -r key value; do
    print_kv "$key" "$value"
  done < <(python3 - "$bundle_path" "$objs_dir" <<'PY'
import json
import os
import sys
from pathlib import Path

bundle_path = Path(sys.argv[1])
objs_dir = Path(sys.argv[2])
bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

def fmt_size(num: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}"
        value /= 1024.0

objs_count = 0
if objs_dir.exists():
    objs_count = sum(1 for p in objs_dir.rglob("*.json") if p.is_file())

rows = [
    ("encrypted_files", str(len(bundle.get("files", [])))),
    ("object_files", str(objs_count)),
    ("bundle_size", fmt_size(bundle_path.stat().st_size)),
    ("kdf_iterations", str(bundle.get("kdf", {}).get("iterations", ""))),
    ("app_html_path", str(bundle.get("app_html_path", ""))),
]
for key, value in rows:
    print(f"{key}\t{value}")
PY
)
}

split_forms_config_for_build() {
  local source_forms_path="$1"
  local public_forms_path="$2"
  local protected_forms_path="$3"

  mkdir -p "$(dirname "$public_forms_path")"
  mkdir -p "$(dirname "$protected_forms_path")"

  python3 - "$source_forms_path" "$public_forms_path" "$protected_forms_path" <<'PY'
import json
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
public_path = Path(sys.argv[2])
protected_path = Path(sys.argv[3])

default_login_entry = {
    "type": "login",
    "form-id": "cipherkey",
    "form-link": "",
    "variable": "",
}

payload = []
if source_path.exists():
    try:
        loaded = json.loads(source_path.read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            payload = loaded
    except (json.JSONDecodeError, OSError):
        payload = []

public_entries = []
protected_entries = []
for item in payload:
    if not isinstance(item, dict):
        continue
    entry_type = str(item.get("type") or "").strip().lower()
    form_id = str(item.get("form-id") or "").strip()
    if entry_type == "login" or form_id == "cipherkey":
        public_entries.append(
            {
                "type": "login",
                "form-id": "cipherkey",
                "form-link": str(item.get("form-link") or "").strip(),
                "variable": str(item.get("variable") or "").strip(),
            }
        )
    else:
        protected_entries.append(item)

if not public_entries:
    public_entries = [default_login_entry]

public_path.write_text(
    json.dumps(public_entries, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
protected_path.write_text(
    json.dumps(protected_entries, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(f"{len(public_entries)}\t{len(protected_entries)}")
PY
}

run_build() {
  local mode="$1"
  local config_path_raw="$2"

  load_and_resolve_config "$config_path_raw"
  IFS='|' read -r build_password kdf_iterations < <(select_build_secret "$mode")

  local work_dir
  work_dir="$(mktemp -d)"
  trap 'rm -rf "'"$work_dir"'"' RETURN

  local recover_log="$work_dir/recover.log"
  local bundle_log="$work_dir/bundle.log"
  local build_started
  build_started="$(date +%s)"
  local app_template_path="$CFG_APP_HTML"
  local generated_app_html_dir="$SCRIPT_DIR/build/.generated"
  local generated_app_html_path="$generated_app_html_dir/nanogram.app.generated.html"
  mkdir -p "$generated_app_html_dir"
  cp "$app_template_path" "$generated_app_html_path"

  print_section "Build Configuration"
  print_kv "mode" "$mode"
  print_kv "theme" "$CFG_THEME_NAME"
  print_kv "config" "$CFG_CONFIG_PATH"
  print_kv "app_template" "$app_template_path"
  print_kv "app_generated" "$generated_app_html_path"
  print_kv "export_dir" "$CFG_EXPORT_DIR"
  print_kv "output_dir" "$CFG_OUTPUT_DIR"
  print_kv "assets_dir" "$CFG_ASSETS_DIR"
  print_kv "metadata_dir" "$CFG_METADATA_DIR"
  print_kv "domain_name" "${CFG_DOMAIN_NAME:-"(none)"}"
  print_kv "special_comments" "$CFG_USE_SPECIAL_COMMENTS"
  print_kv "kdf_iterations" "$kdf_iterations"

  local special_comment_people_dir="$CFG_ASSETS_DIR/img/people"
  local special_comment_people_backup_dir="$work_dir/special-comment-people"

  info "Preparing clean output directories"
  if [[ -d "$special_comment_people_dir" ]]; then
    mkdir -p "$special_comment_people_backup_dir"
    cp -a "$special_comment_people_dir/." "$special_comment_people_backup_dir/"
  fi
  ensure_safe_clear_dir "$CFG_ASSETS_DIR"
  ensure_safe_clear_dir "$CFG_OUTPUT_DIR"
  if [[ -d "$special_comment_people_backup_dir" ]]; then
    mkdir -p "$special_comment_people_dir"
    cp -a "$special_comment_people_backup_dir/." "$special_comment_people_dir/"
  fi

  local recover_special_comment_args=(
    --commenter-assets-dir "$special_comment_people_dir"
  )
  if [[ "$CFG_USE_SPECIAL_COMMENTS" == "1" ]]; then
    recover_special_comment_args+=(--use-special-comments)
  else
    recover_special_comment_args+=(--no-use-special-comments)
  fi

  local recover_cmd=(
    python3 "$CFG_RECOVER_SCRIPT" recover
    --export-dir "$CFG_EXPORT_DIR"
    --out-dir "$CFG_ASSETS_DIR"
    --include-reels
    --metadata-dir "$CFG_METADATA_DIR"
    --registry "$CFG_REGISTRY_PATH"
    --app-html "$generated_app_html_path"
    "${recover_special_comment_args[@]}"
  )
  if [[ -n "${CFG_COMMENT_AVATAR_EMOJIS:-}" ]]; then
    recover_cmd+=(--avatar-emojis "$CFG_COMMENT_AVATAR_EMOJIS")
  fi
  run_step "Recovering posts/reels/media" "$recover_log" "${recover_cmd[@]}"

  info "Preparing gate assets"
  local public_gate_path="$CFG_OUTPUT_DIR/nanogram.html"
  local public_forms_path="$CFG_OUTPUT_DIR/forms.json"
  local forms_metadata_path="$CFG_METADATA_DIR/forms.json"
  local protected_forms_path="$CFG_ASSETS_DIR/forms.protected.json"
  local profile_image_path="$CFG_ASSETS_DIR/img/profile.jpg"
  local cname_path="$CFG_OUTPUT_DIR/CNAME"
  cp "$CFG_GATE_HTML" "$public_gate_path"
  local forms_split_counts
  forms_split_counts="$(split_forms_config_for_build "$forms_metadata_path" "$public_forms_path" "$protected_forms_path")"
  local public_forms_entries protected_forms_entries
  IFS=$'\t' read -r public_forms_entries protected_forms_entries <<<"$forms_split_counts"
  info "Forms config split: public login entries=$public_forms_entries, protected entries=$protected_forms_entries"

  if [[ -n "${CFG_DOMAIN_NAME// }" ]]; then
    printf '%s\n' "$CFG_DOMAIN_NAME" > "$cname_path"
    info "Wrote CNAME file for domain: $CFG_DOMAIN_NAME"
  fi

  python3 - "$public_gate_path" "$profile_image_path" "$CFG_REGISTRY_PATH" <<'PY'
import base64
import html
import json
import mimetypes
import re
import sys
from pathlib import Path

gate_path = Path(sys.argv[1])
profile_path = Path(sys.argv[2])
registry_path = Path(sys.argv[3])
placeholder = "__HOME_PROFILE_IMAGE_DATA_URI__"

if not gate_path.exists():
    raise SystemExit(0)

gate_html = gate_path.read_text(encoding="utf-8")
if placeholder not in gate_html:
    raise SystemExit(0)

data_uri = ""
if profile_path.exists():
    mime = mimetypes.guess_type(profile_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(profile_path.read_bytes()).decode("ascii")
    data_uri = f"data:{mime};base64,{encoded}"

def first_metric_token(value):
    if isinstance(value, (int, float)):
        if value < 0:
            return None
        return str(int(round(float(value))))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        direct = re.fullmatch(r"\d[\d,]*(?:\.\d+)?[kKmMbB]?", text)
        if direct:
            return direct.group(0)
        embedded = re.search(r"(\d[\d,]*(?:\.\d+)?[kKmMbB]?)", text)
        if embedded:
            return embedded.group(1)
    return None


def replace_text_by_id(doc: str, element_id: str, value: str) -> str:
    pattern = re.compile(
        rf'(<[^>]*\bid="{re.escape(element_id)}"[^>]*>)(.*?)(</[^>]+>)',
        re.S,
    )
    escaped = html.escape(value, quote=False)
    return pattern.sub(lambda m: f"{m.group(1)}{escaped}{m.group(3)}", doc, count=1)


def replace_alt_by_id(doc: str, element_id: str, value: str) -> str:
    pattern = re.compile(
        rf'(<[^>]*\bid="{re.escape(element_id)}"[^>]*\balt=")([^"]*)(")',
        re.S,
    )
    escaped = html.escape(value, quote=True)
    return pattern.sub(lambda m: f'{m.group(1)}{escaped}{m.group(3)}', doc, count=1)


registry = {}
if registry_path.exists():
    try:
        loaded = json.loads(registry_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            registry = loaded
    except (OSError, json.JSONDecodeError):
        registry = {}

profile = registry.get("profile")
if not isinstance(profile, dict):
    profile = {}

posts = registry.get("posts")
reels = registry.get("reels")
posts_total = (len(posts) if isinstance(posts, list) else 0) + (len(reels) if isinstance(reels, list) else 0)

username = str(profile.get("username") or "").strip() or "profile_user"
followers = first_metric_token(
    profile.get("followers_count")
    if "followers_count" in profile
    else profile.get("followers")
) or "0"
following = first_metric_token(
    profile.get("following_count")
    if "following_count" in profile
    else profile.get("following")
) or "0"

updated = gate_html.replace(placeholder, data_uri)
updated = replace_text_by_id(updated, "homeProfileUsername", username)
updated = replace_text_by_id(updated, "gateCopyUsername", username)
updated = replace_text_by_id(updated, "homeProfilePosts", str(posts_total))
updated = replace_text_by_id(updated, "homeProfileFollowers", followers)
updated = replace_text_by_id(updated, "homeProfileFollowing", following)
updated = replace_alt_by_id(updated, "homeProfileImage", f"{username} profile picture")

gate_path.write_text(updated, encoding="utf-8")
PY

  local bundle_out_path="$CFG_OUTPUT_DIR/site.bundle.json"
  local objs_dir="$CFG_OUTPUT_DIR/objs"
  run_step "Encrypting site bundle" "$bundle_log" \
    node "$CFG_BUNDLE_SCRIPT" \
      --password "$build_password" \
      --app-html "$generated_app_html_path" \
      --bundle-out "$bundle_out_path" \
      --objs-dir "$objs_dir" \
      --iterations "$kdf_iterations" \
      --no-default-includes \
      --include "$CFG_THEME_DIR" \
      --include "$CFG_ASSETS_DIR"

  emit_recover_summary "$CFG_ASSETS_DIR/manifest.json"
  emit_bundle_summary "$bundle_out_path" "$objs_dir"

  local build_finished duration
  build_finished="$(date +%s)"
  duration=$((build_finished - build_started))

  print_section "Build Output"
  print_kv "gate_html" "$public_gate_path"
  print_kv "forms_json" "$public_forms_path"
  print_kv "protected_forms" "$protected_forms_path"
  print_kv "bundle_json" "$bundle_out_path"
  print_kv "objects_dir" "$objs_dir"
  if [[ -n "${CFG_DOMAIN_NAME// }" ]]; then
    print_kv "cname_file" "$cname_path"
  fi
  print_kv "duration_sec" "$duration"
}

compute_dir_signature() {
  local target_dir="$1"
  python3 - "$target_dir" <<'PY'
import hashlib
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
if not root.exists():
    print("missing")
    raise SystemExit(0)

rows = []
for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue
    stat = path.stat()
    rel = path.relative_to(root).as_posix()
    rows.append(f"{rel}|{stat.st_mtime_ns}|{stat.st_size}")

digest = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
print(digest)
PY
}

start_live_server() {
  local output_dir="$1"
  local port="$2"
  local log_file="$3"

  python3 -m http.server "$port" --directory "$output_dir" >"$log_file" 2>&1 &
  LIVE_SERVER_PID="$!"
  sleep 0.3

  if ! kill -0 "$LIVE_SERVER_PID" 2>/dev/null; then
    error "failed to start local server on port $port"
    if [[ -f "$log_file" ]]; then
      tail -n 30 "$log_file" >&2 || true
    fi
    return 1
  fi

  info "Serving $output_dir at http://127.0.0.1:$port"
}

stop_live_server() {
  if [[ -n "$LIVE_SERVER_PID" ]] && kill -0 "$LIVE_SERVER_PID" 2>/dev/null; then
    kill "$LIVE_SERVER_PID" 2>/dev/null || true
    wait "$LIVE_SERVER_PID" 2>/dev/null || true
  fi
  LIVE_SERVER_PID=""
}

run_live() {
  local config_path="$1"
  local mode="$2"
  local port="$3"
  local interval="$4"

  if [[ "$mode" != "debug" && "$mode" != "release" ]]; then
    fatal "--mode must be debug or release (got: $mode)"
  fi
  if ! is_valid_positive_int "$port" || (( port > 65535 )); then
    fatal "--port must be an integer between 1 and 65535 (got: $port)"
  fi
  if ! is_valid_positive_int "$interval"; then
    fatal "--interval must be an integer >= 1 second (got: $interval)"
  fi

  load_and_resolve_config "$config_path"
  run_build "$mode" "$config_path"

  local work_dir
  work_dir="$(mktemp -d)"
  LIVE_SERVER_LOG="$work_dir/live-server.log"
  local output_dir="$CFG_OUTPUT_DIR"

  trap 'stop_live_server; rm -rf "'"$work_dir"'"; info "Live mode stopped"' EXIT INT TERM

  start_live_server "$output_dir" "$port" "$LIVE_SERVER_LOG" || fatal "unable to start live server"
  local last_signature
  last_signature="$(compute_dir_signature "$output_dir")"

  print_section "Live Watcher"
  print_kv "watch_dir" "$output_dir"
  print_kv "port" "$port"
  print_kv "poll_interval_sec" "$interval"
  print_kv "mode" "$mode"
  info "Watching for OUTPUT_DIR changes. Press Ctrl+C to stop."

  while true; do
    sleep "$interval"
    local next_signature
    next_signature="$(compute_dir_signature "$output_dir")"
    if [[ "$next_signature" != "$last_signature" ]]; then
      info "Detected change in OUTPUT_DIR; reloading server"
      last_signature="$next_signature"
      stop_live_server
      start_live_server "$output_dir" "$port" "$LIVE_SERVER_LOG" || fatal "unable to restart live server"
    elif [[ -n "$LIVE_SERVER_PID" ]] && ! kill -0 "$LIVE_SERVER_PID" 2>/dev/null; then
      warn "Live server exited unexpectedly; restarting"
      start_live_server "$output_dir" "$port" "$LIVE_SERVER_LOG" || fatal "unable to restart live server"
    fi
  done
}

run_comments() {
  local config_path=""
  local metadata_dir_override=""

  while [[ $# -gt 0 ]]; do
    parse_comments_command_option "$@"
    if (( PARSE_CONSUMED > 0 )); then
      shift "$PARSE_CONSUMED"
      continue
    fi
    case "$1" in
      --config)
        config_path="${2:-}"
        shift 2
        ;;
      --metadata-dir)
        metadata_dir_override="${2:-}"
        shift 2
        ;;
      -h|--help)
        usage
        return 0
        ;;
      *)
        fatal "unknown option for comments: $1"
        ;;
    esac
  done

  CFG_CONFIG_PATH="(none)"
  CFG_METADATA_DIR="$(resolve_from_project "./build/metadata")"
  if [[ -n "${config_path// }" ]]; then
    load_comment_import_config "$config_path"
  fi
  if [[ -n "${metadata_dir_override// }" ]]; then
    CFG_METADATA_DIR="$(resolve_from_project "$metadata_dir_override")"
  fi

  if ! comment_import_requested; then
    fatal "comments command requires a source: --csv or --sheet-id (or set COMMENTS_* in config)"
  fi
  validate_comment_import_options
  build_comment_import_cmd

  local work_dir
  work_dir="$(mktemp -d)"
  trap 'rm -rf "'"$work_dir"'"' RETURN
  local import_log="$work_dir/import-comments.log"

  print_section "Comments Import"
  print_kv "config" "$CFG_CONFIG_PATH"
  print_kv "metadata_dir" "$CFG_METADATA_DIR"
  if [[ -n "${IMPORT_COMMENTS_CSV// }" ]]; then
    print_kv "source" "$IMPORT_COMMENTS_CSV"
  else
    print_kv "source" "google-sheet:$IMPORT_COMMENTS_GOOGLE_SHEET_ID"
  fi

  run_step "Importing sheet comments" "$import_log" "${COMMENT_IMPORT_CMD[@]}"
  emit_comment_import_summary "$import_log"
}

main() {
  if [[ $# -eq 0 ]]; then
    usage >&2
    exit 1
  fi

  local command="$1"
  shift

  case "$command" in
    -h|--help|help)
      usage
      ;;
    init-config|config-template)
      local output_path="./build/config"
      local force=0
      while [[ $# -gt 0 ]]; do
        case "$1" in
          --output)
            output_path="${2:-}"
            shift 2
            ;;
          --force)
            force=1
            shift
            ;;
          -h|--help)
            usage
            return 0
            ;;
          *)
            fatal "unknown option for $command: $1"
            ;;
        esac
      done
      if [[ -z "${output_path// }" ]]; then
        fatal "--output requires a value"
      fi
      write_config_template "$output_path" "$force"
      ;;
    release|debug)
      local config_path=""
      while [[ $# -gt 0 ]]; do
        case "$1" in
          --config)
            config_path="${2:-}"
            shift 2
            ;;
          -h|--help)
            usage
            return 0
            ;;
          *)
            fatal "unknown option for $command: $1"
            ;;
        esac
      done
      run_build "$command" "$config_path"
      ;;
    live)
      local config_path=""
      local mode="debug"
      local port="8080"
      local interval="1"
      while [[ $# -gt 0 ]]; do
        case "$1" in
          --config)
            config_path="${2:-}"
            shift 2
            ;;
          --mode)
            mode="${2:-}"
            shift 2
            ;;
          --port)
            port="${2:-}"
            shift 2
            ;;
          --interval)
            interval="${2:-}"
            shift 2
            ;;
          -h|--help)
            usage
            return 0
            ;;
          *)
            fatal "unknown option for $command: $1"
            ;;
        esac
      done
      run_live "$config_path" "$mode" "$port" "$interval"
      ;;
    comments)
      run_comments "$@"
      ;;
    *)
      fatal "unknown command: $command"
      ;;
  esac
}

main "$@"
