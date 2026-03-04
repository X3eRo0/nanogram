#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

TEMPLATE_PATH="$TMP_DIR/nanogram.template.config.sh"
./nanogram.sh init-config --output "$TEMPLATE_PATH"

grep -q '^INSTAGRAM_EXPORT_DIR=' "$TEMPLATE_PATH"
grep -q '^OUTPUT_DIR=' "$TEMPLATE_PATH"
grep -q '^METADATA_DIR=' "$TEMPLATE_PATH"
grep -q '^LOGIN_PASSWORD=' "$TEMPLATE_PATH"
grep -q '^TEST_BUILD_PASSWORD=' "$TEMPLATE_PATH"
grep -q '^COMMENT_AVATAR_EMOJIS=' "$TEMPLATE_PATH"
grep -q '^DOMAIN_NAME=' "$TEMPLATE_PATH"
grep -q '^COMMENTS_GOOGLE_SHEET_ID=' "$TEMPLATE_PATH"
grep -q '^COMMENTS_GOOGLE_ACCESS_TOKEN=' "$TEMPLATE_PATH"

NO_CONFIG_LOG="$TMP_DIR/no-config.log"
if ./nanogram.sh release >"$NO_CONFIG_LOG" 2>&1; then
  echo "Expected release without --config to fail, but it succeeded." >&2
  exit 1
fi
grep -q -- "--config is required" "$NO_CONFIG_LOG"

BUILD_CASE_DIR="$TMP_DIR/build-case"
mkdir -p "$BUILD_CASE_DIR"
cp "$ROOT_DIR/src/nanogram.app.html" "$BUILD_CASE_DIR/nanogram.app.html"
CONFIG_PATH="$BUILD_CASE_DIR/nanogram.config.sh"
cat > "$CONFIG_PATH" <<EOF
INSTAGRAM_EXPORT_DIR="$ROOT_DIR/tests/fixtures/export_minimal"
OUTPUT_DIR="$BUILD_CASE_DIR/public_out"
METADATA_DIR="$BUILD_CASE_DIR/metadata_out"
LOGIN_PASSWORD="release-password-123"
TEST_BUILD_PASSWORD="debug-password-123"

THEME_NAME="default"
APP_HTML_PATH="$BUILD_CASE_DIR/nanogram.app.html"
GATE_HTML_PATH="$ROOT_DIR/src/nanogram.html"
REGISTRY_PATH="$BUILD_CASE_DIR/posts_registry.json"
RECOVER_SCRIPT="$ROOT_DIR/src/scripts/recover_nanogram_export.py"
BUNDLE_SCRIPT="$ROOT_DIR/src/scripts/build_protected_bundle.mjs"
ASSETS_DIR="$BUILD_CASE_DIR/assets_out"
DOMAIN_NAME="example.com"
DEBUG_KDF_ITERATIONS="1200"
RELEASE_KDF_ITERATIONS="1200"
COMMENT_AVATAR_EMOJIS='["🧑","👤","🫂"]'
EOF

assert_output_files() {
  local out_dir="$1"
  [[ -f "$out_dir/nanogram.html" ]]
  [[ -f "$out_dir/forms.json" ]]
  [[ -f "$out_dir/site.bundle.json" ]]
  [[ -f "$out_dir/CNAME" ]]
  [[ -d "$out_dir/objs" ]]
}

assert_cname_contents() {
  local cname_path="$1"
  local expected="$2"
  local actual
  actual="$(tr -d '\r\n' < "$cname_path")"
  if [[ "$actual" != "$expected" ]]; then
    echo "Unexpected CNAME contents in $cname_path (expected '$expected', got '$actual')" >&2
    exit 1
  fi
}

assert_public_forms_login_only() {
  local forms_path="$1"
  python3 - "$forms_path" <<'PY'
import json
import sys
from pathlib import Path

forms_path = Path(sys.argv[1])
payload = json.loads(forms_path.read_text(encoding="utf-8"))
if not isinstance(payload, list):
    raise SystemExit(f"forms.json must be an array: {forms_path}")

if not payload:
    raise SystemExit(f"forms.json must contain at least one login entry: {forms_path}")

for item in payload:
    if not isinstance(item, dict):
        raise SystemExit(f"forms.json contains non-object entry: {forms_path}")
    entry_type = str(item.get("type") or "").strip().lower()
    form_id = str(item.get("form-id") or "").strip()
    if not (entry_type == "login" or form_id == "cipherkey"):
        raise SystemExit(
            "forms.json leaked non-login config entry into public output."
        )
PY
}

assert_metadata_forms_schema() {
  local forms_path="$1"
  python3 - "$forms_path" <<'PY'
import json
import sys
from pathlib import Path

forms_path = Path(sys.argv[1])
payload = json.loads(forms_path.read_text(encoding="utf-8"))
if not isinstance(payload, list):
    raise SystemExit(f"metadata forms payload must be a list: {forms_path}")

posts_entries = [
    item for item in payload
    if isinstance(item, dict) and str(item.get("type") or "").strip().lower() == "posts"
]
if not posts_entries:
    raise SystemExit(f"metadata forms payload is missing posts entry: {forms_path}")

entry = posts_entries[0]
for required_key in ("form-link", "post-id-var", "alias-var", "avatar-var", "comment-var"):
    if required_key not in entry:
        raise SystemExit(f"posts entry missing key '{required_key}' in {forms_path}")

expected_order = ["type", "form-link", "post-id-var", "alias-var", "avatar-var", "comment-var"]
actual_order = list(entry.keys())
if actual_order[: len(expected_order)] != expected_order:
    raise SystemExit(
        f"posts entry key order mismatch in {forms_path}: expected prefix {expected_order}, got {actual_order}"
    )
PY
}

bundle_hash_matches() {
  local bundle_path="$1"
  local password="$2"
  python3 - "$bundle_path" "$password" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

bundle_path = Path(sys.argv[1])
password = sys.argv[2]

bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
actual = str(bundle.get("password_hash_sha256", "")).strip().lower()
expected = hashlib.sha256(password.encode("utf-8")).hexdigest().lower()
if actual != expected:
    raise SystemExit(
        f"Password hash mismatch for {bundle_path}: expected {expected}, got {actual}"
    )

paths = [str(item.get("path", "")) for item in bundle.get("files", []) if isinstance(item, dict)]
if "src/themes/default/css/nanogram.css" not in paths:
    raise SystemExit("Theme CSS was not included in encrypted bundle.")
if not any(path.endswith("/forms.protected.json") or path == "forms.protected.json" for path in paths):
    raise SystemExit("Protected forms payload was not included in encrypted bundle.")
PY
}

./nanogram.sh debug --config "$CONFIG_PATH"
DEBUG_OUT_DIR="$BUILD_CASE_DIR/public_out"
assert_output_files "$DEBUG_OUT_DIR"
assert_cname_contents "$DEBUG_OUT_DIR/CNAME" "example.com"
assert_public_forms_login_only "$DEBUG_OUT_DIR/forms.json"
assert_metadata_forms_schema "$BUILD_CASE_DIR/metadata_out/forms.json"
bundle_hash_matches "$DEBUG_OUT_DIR/site.bundle.json" "debug-password-123"

./nanogram.sh release --config "$CONFIG_PATH"
RELEASE_OUT_DIR="$BUILD_CASE_DIR/public_out"
assert_output_files "$RELEASE_OUT_DIR"
assert_cname_contents "$RELEASE_OUT_DIR/CNAME" "example.com"
assert_public_forms_login_only "$RELEASE_OUT_DIR/forms.json"
bundle_hash_matches "$RELEASE_OUT_DIR/site.bundle.json" "release-password-123"

FIRST_POST_META="$(find "$BUILD_CASE_DIR/metadata_out/posts" -maxdepth 1 -type f -name '*.json' | head -n 1)"
FIRST_POST_ID="$(basename "$FIRST_POST_META" .json)"
COMMENTS_CSV="$BUILD_CASE_DIR/comments.csv"
cat > "$COMMENTS_CSV" <<EOF
post-id,avatar,name,actual comment
$FIRST_POST_ID,🧑,ci-smoke,Imported via nanogram.sh comments
EOF
./nanogram.sh comments --metadata-dir "$BUILD_CASE_DIR/metadata_out" --csv "$COMMENTS_CSV"

python3 - "$FIRST_POST_META" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
comments = payload.get("comments")
if not isinstance(comments, dict) or "ci-smoke" not in comments:
    raise SystemExit(f"nanogram.sh comments did not update metadata: {path}")
PY

python3 src/scripts/recover_nanogram_export.py --help >/dev/null
python3 src/scripts/import_sheet_comments.py --help >/dev/null
node src/scripts/build_protected_bundle.mjs --help >/dev/null
./nanogram.sh comments --help >/dev/null

echo "CI smoke test completed successfully."
