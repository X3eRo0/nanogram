# Nanogram

Nanogram turns an Instagram export into a static, password-protected site you can deploy on any static host.

## Why Nanogram

- Static output, no backend runtime required
- Password-gated bundle (PBKDF2 + AES-GCM)
- Config-driven build flow
- Comment import support via `nanogram.sh comments`
- Theme-ready frontend structure

## Prerequisites

- Python 3.10+ (standard library only)
- Node.js 18+
- Core Unix tools (`cp`, `find`, `mktemp`)
- Instagram export containing either:
  - `your_instagram_activity/media/posts_1.json`, or
  - `your_instagram_activity/media/posts_1.html`
- Browser with Web Crypto support (`window.crypto.subtle`)

Tested in CI with Python 3.12 and Node.js 20.

## Quick Start

1. Generate config template:

```bash
./nanogram.sh init-config --output ./build/config
```

2. Edit `./build/config`.

3. Run builds:

```bash
./nanogram.sh debug --config ./build/config
./nanogram.sh release --config ./build/config
```

## Configuration

All configuration lives in one file (default template path: `./build/config`).

- Relative paths are resolved from the project root.
- Boolean values accepted: `1`, `0`, `true`, `false`, `yes`, `no`, `on`, `off`.
- Size values (for `LAZY_LOAD_SIZE_MAX`) accept raw bytes or unit suffixes like `512KB`, `12MB`, `1GB`.

### Required Keys

| Key | Type | Description |
| --- | --- | --- |
| `INSTAGRAM_EXPORT_DIR` | path | Root of Instagram export data |
| `OUTPUT_DIR` | path | Public/deploy output directory |
| `LOGIN_PASSWORD` | string | Release build password |
| `TEST_BUILD_PASSWORD` | string | Debug build password |

### Build Keys

| Key | Default | Description |
| --- | --- | --- |
| `METADATA_DIR` | `./build/metadata` | Metadata directory for posts/reels/forms |
| `THEME_NAME` | `default` | Theme under `src/themes/` |
| `APP_HTML_PATH` | `./src/nanogram.app.html` | App template HTML |
| `GATE_HTML_PATH` | `./src/nanogram.html` | Password gate HTML |
| `REGISTRY_PATH` | `./build/registry.json` | Registry JSON path |
| `ASSETS_DIR` | `./build/assets` | Recovered asset output directory |
| `DOMAIN_NAME` | empty | If set, writes `CNAME` into `OUTPUT_DIR` |
| `USE_SPECIAL_COMMENTS` | `1` | Enable special commenter avatars from `ASSETS_DIR/img/people` |
| `RECOVER_SCRIPT` | `./src/scripts/recover_nanogram_export.py` | Recover pipeline script path |
| `BUNDLE_SCRIPT` | `./src/scripts/build_protected_bundle.mjs` | Bundle/encryption script path |
| `DEBUG_KDF_ITERATIONS` | `10000` | PBKDF2 iterations for debug builds |
| `RELEASE_KDF_ITERATIONS` | `250000` | PBKDF2 iterations for release builds |
| `LAZY_LOAD_SIZE_MAX` | `15728640` | Max plaintext bytes loaded eagerly at unlock; larger media loads on click (`0` disables) |
| `COMMENT_AVATAR_EMOJIS` | built-in list | Emoji pool for avatar fallback/selection |

### Comment Import Keys (`nanogram.sh comments` defaults)

These keys are consumed by `nanogram.sh comments` (not by `debug`, `release`, or `live`).

| Key | Default | Description |
| --- | --- | --- |
| `COMMENTS_CSV` | empty | CSV path/URL source |
| `COMMENTS_GOOGLE_SHEET_ID` | empty | Google Sheet ID source (required for Sheets mode) |
| `COMMENTS_GOOGLE_ACCESS_TOKEN` | empty | OAuth access token (direct value) |
| `COMMENTS_GOOGLE_CLIENT_ID` | empty | OAuth client id (used for refresh-token flow) |
| `COMMENTS_GOOGLE_CLIENT_SECRET` | empty | OAuth client secret (used for refresh-token flow) |
| `COMMENTS_GOOGLE_REFRESH_TOKEN` | empty | OAuth refresh token (used to mint access token) |
| `COMMENTS_ENCODING` | `utf-8-sig` | CSV encoding |
| `COMMENTS_POST_ID_COLUMN` | `post-id` | Post-id column header |
| `COMMENTS_AVATAR_COLUMN` | `avatar` | Avatar column header |
| `COMMENTS_NAME_COLUMN` | `name` | Name column header |
| `COMMENTS_COMMENT_COLUMN` | `actual comment` | Comment text column header |
| `COMMENTS_DEFAULT_NAME` | `Anonymous` | Fallback commenter name |
| `COMMENTS_CREATE_MISSING` | `0` | Create missing metadata files |
| `COMMENTS_DRY_RUN` | `0` | Parse/report only, no writes |

## Commands

### Build

```bash
./nanogram.sh debug --config ./build/config
./nanogram.sh release --config ./build/config
```

### Live Preview

```bash
./nanogram.sh live --config ./build/config --mode debug --port 8080
```

### Import Comments

Comment operations are invoked only through `nanogram.sh comments`.

Google Sheets mode reads source/auth from config:
- `COMMENTS_GOOGLE_SHEET_ID`
- one auth path:
  - `COMMENTS_GOOGLE_ACCESS_TOKEN`
  - or refresh credentials (`COMMENTS_GOOGLE_CLIENT_ID`, `COMMENTS_GOOGLE_CLIENT_SECRET`, `COMMENTS_GOOGLE_REFRESH_TOKEN`)
- setup guide: [Google OAuth Setup For Comments](docs/google-oauth-comments.md)

Run:

```bash
./nanogram.sh comments --config ./build/config
```

CSV source:

```bash
./nanogram.sh comments --config ./build/config --csv ./responses.csv
```

## Theme System

- Theme assets live under `src/themes/<theme-name>/`
- Set `THEME_NAME` in config
- Ensure app HTML references `themes/<theme-name>/...`

## CI

Workflow: `.github/workflows/ci.yml`

CI validates shell syntax and smoke-tests `debug`, `release`, and `comments` workflows.

## Security

- This is client-side protection, not server-side authorization.
- Anyone with the password can decrypt content.
- Never commit real config files with production passwords.
