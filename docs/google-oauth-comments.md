# Google OAuth Setup For Comments

This guide shows how to get values for Nanogram Google Sheets comment import and where each value goes in `build/config`.

## Required Config Keys

```bash
COMMENTS_GOOGLE_SHEET_ID=""
COMMENTS_GOOGLE_ACCESS_TOKEN=""
COMMENTS_GOOGLE_CLIENT_ID=""
COMMENTS_GOOGLE_CLIENT_SECRET=""
COMMENTS_GOOGLE_REFRESH_TOKEN=""
```

## Value Mapping

1. `COMMENTS_GOOGLE_SHEET_ID`
Get this from the sheet URL:
`https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`

2. `COMMENTS_GOOGLE_CLIENT_ID`
From Google Cloud Console OAuth client credentials.

3. `COMMENTS_GOOGLE_CLIENT_SECRET`
From Google Cloud Console OAuth client credentials.

4. `COMMENTS_GOOGLE_REFRESH_TOKEN`
From OAuth Playground Step 2 after exchanging authorization code.

5. `COMMENTS_GOOGLE_ACCESS_TOKEN` (optional)
Also from OAuth Playground Step 2. This expires quickly. If empty, Nanogram will mint one automatically using refresh-token credentials.

## Steps To Acquire Credentials

1. Create or select a Google Cloud project.
2. Enable the Google Sheets API in that project.
3. Configure OAuth consent screen.
4. If app status is Testing, add your Google account under Test users.
5. Create OAuth Client ID credentials (Application type: Web application).
6. Add redirect URI:
`https://developers.google.com/oauthplayground`
7. Open OAuth Playground.
8. Open settings and enable Use your own OAuth credentials.
9. Paste your client ID and client secret.
10. In Step 1, authorize scope:
`https://www.googleapis.com/auth/spreadsheets.readonly`
11. Exchange authorization code for tokens in Step 2.
12. Copy refresh token and optional access token.

## Recommended `build/config` Setup

Use auto-refresh so you do not manually replace expiring access tokens.

```bash
COMMENTS_GOOGLE_SHEET_ID="your_sheet_id"

COMMENTS_GOOGLE_ACCESS_TOKEN=""

COMMENTS_GOOGLE_CLIENT_ID="your_client_id"
COMMENTS_GOOGLE_CLIENT_SECRET="your_client_secret"
COMMENTS_GOOGLE_REFRESH_TOKEN="your_refresh_token"
```

Then run:

```bash
./nanogram.sh comments --config ./build/config
```

## Notes

1. `./nanogram.sh comments` always reads Google Sheets source/auth from config.
2. Access tokens are short-lived.
3. Refresh tokens can be revoked. If token minting fails with `invalid_grant`, re-authorize in OAuth Playground and update refresh token.
