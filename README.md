# Shared Google Drive for Kodi

A deliberately small Kodi video add-on that uses one Google service account to browse and play original video files from one configured shared drive.

## Scope

Included:

- One service account.
- One fixed shared-drive ID.
- Folder browsing.
- Original video playback through the documented Google Drive API.
- Short-lived access-token and bounded folder-result caching in the add-on profile.
- Optional, user-triggered local `.strm` snapshot export.
- Credentials stored through Kodi's standard add-on settings.

Intentionally excluded:

- User OAuth and third-party sign-in servers.
- Drive discovery, My Drive, Shared with me, and search.
- Multiple accounts or account rotation.
- Continuous synchronization, automatic library management, metadata, and watched-state tracking.
- Google Photos, transcoding, alternate resolutions, downloads, and subtitles.
- Databases, long-lived token stores, background services, and local HTTP servers.
- Credential encryption or reversible obfuscation.

## Security model

The Google OAuth scope is `drive.readonly`, but OAuth scopes do not restrict a credential to a particular shared drive. The primary access boundary is Google-side membership:

1. Create a dedicated service account.
2. Do not grant it unrelated Google Cloud IAM roles.
3. Do not enable domain-wide delegation.
4. Add its `client_email` as a **Viewer** of exactly one shared drive.
5. Do not directly share unrelated Drive files or folders with it.
6. Configure that shared drive's ID in the add-on.

The add-on never enumerates shared drives. Every listed or played item's `driveId` must equal the configured ID. This is defense in depth; Google-side membership remains the real security boundary.

Credentials are imported into Kodi's normal per-add-on settings. Only `client_email` and `private_key` are retained; the original JSON is not stored. Kodi settings are plaintext and are readable by software running as the same OS/Kodi user. This is intentional and documented rather than disguised with a source-embedded encryption key.

A short-lived access token and its expiration are cached in `special://profile/addon_data/plugin.video.sharedgdrive/access_token.json`. The cache is reused only when its credential fingerprint matches and more than five minutes remain before expiration. It is atomically replaced, uses owner-only permissions where the platform supports them, and is cleared when credentials are imported or removed. The token remains plaintext but normally expires within an hour.

Interactive folder listings are cached in `folder_results.json` in the same profile. The cache is fixed at a three-minute TTL, at most 50 folders, and at most 2 MiB. It is isolated by a credential-plus-shared-drive fingerprint, atomically written, and revalidates cached items against the configured `driveId` before returning them. Cache hits do not rewrite the file. Snapshot exports bypass this cache and always enumerate Drive afresh. **Clear folder cache** in settings provides immediate refresh without clearing credentials or the access token.

The add-on makes HTTPS requests only to:

- `oauth2.googleapis.com`
- `www.googleapis.com`

Redirects are disabled so an authorization header or JWT assertion cannot be redirected to another host. The add-on does not log credentials, bearer tokens, API response bodies, filenames, or playback URLs.

## Google setup

1. Create or select a Google Cloud project.
2. Enable the Google Drive API.
3. Create a dedicated service account with no project roles unless separately required.
4. Create a JSON key for the service account.
5. In Google Drive, add the service account email to the intended shared drive as **Viewer**.
6. Copy the shared drive ID from its URL. For a URL such as:

   ```text
   https://drive.google.com/drive/folders/0ABExampleSharedDriveId
   ```

   the ID is `0ABExampleSharedDriveId`.
7. Install the add-on ZIP in Kodi.
8. Open add-on settings and enter the shared drive ID.
9. Select **Import service-account JSON** and choose the downloaded JSON key.
10. Securely delete or archive the original key according to your operational policy.

If the service-account key may have been exposed, disable/delete that key in Google Cloud, create a replacement, and import the replacement.

## Playback behavior

Playback uses:

```text
GET https://www.googleapis.com/drive/v3/files/<file-id>?alt=media&supportsAllDrives=true
```

Kodi receives the short-lived bearer token as an HTTP header in its resolved playback URL. Playback startup refreshes cached tokens unless at least 55 minutes remain. The add-on does not run a refresh proxy, so very long playback sessions or seeks after token expiry may still require restarting playback. A proxy should only be added if target-device testing demonstrates that it is necessary.

Only files whose MIME type starts with `video/` and whose `capabilities.canDownload` is true are shown as playable.

## Snapshot `.strm` export

Snapshot export is disabled by default. When enabled, **Export snapshot now** recursively enumerates the configured shared drive and mirrors its folder layout into a user-selected local or Kodi-writable destination. Each generated file contains only a plugin playback URL and Google file ID; it contains no bearer token, service-account key, or shared-drive ID.

This feature does **not** require Google Drive write access. It writes `.strm` files to the selected Kodi filesystem destination, not into Google Drive. Keep the service account as a shared-drive **Viewer** and keep the `drive.readonly` scope.

Re-export behavior:

- Re-export is idempotent: existing exporter-owned files with the expected URL are retained without rewriting them.
- Missing/new `.strm` files are created.
- Unrelated or manually changed `.strm` files are skipped and never overwritten.
- A completed re-export reports exporter-owned `.strm` files whose Google file is no longer present as `stale`; it does not delete them.
- Stale ownership is retained only while the local file still contains the exact generated URL. Removed or manually changed files lose exporter ownership.
- A cancelled export does not perform stale classification because its Drive enumeration is incomplete.
- The manifest contains active and stale relative export paths plus Google file IDs, but no credentials or tokens.
- **Review/remove stale exported files** lists only currently valid exporter-owned stale entries, allows multi-selection, asks for confirmation, and revalidates exact content immediately before deleting each selected `.strm`.
- No directories are deleted by stale cleanup.

The destination must be writable by Kodi. If the destination is an SMB/NFS share, permissions are governed by the credentials and mount/share configuration Kodi uses for that destination—not by the Google service account. The destination must be trusted: generic Kodi VFS backends do not provide uniform no-follow, exclusive-create, locking, or fully atomic replacement guarantees against malicious concurrent writers. Embedded credentials in the destination URL are rejected.

## Dependency

JWT assertions are signed with Kodi's maintained `script.module.pycryptodome` dependency. RSA signing is required by Google's service-account protocol; the add-on does not implement cryptographic primitives itself.

## Development

The non-Kodi modules use only Python's standard library plus PyCryptodome for signing. Run the unit tests from the add-on directory:

```sh
python3 -m unittest discover -s tests -v
```

Create the installable ZIP from the workspace directory with the repository packaging script:

```sh
python3 plugin.video.sharedgdrive/package.py
```

The script excludes `.git`, tests, caches, unexpected files, symlinks, and development-only files. The detailed security/performance assessment is in `DESIGN_REVIEW.md`.

## Continuous integration and releases

`.github/workflows/ci.yml` runs the unit suite, Python/XML validation, and package integrity checks on Python 3.9 and 3.13 for branch pushes and pull requests.

`.github/workflows/release.yml` publishes a release when a semantic version tag such as `v0.1.1` is pushed. The tag must exactly match the version in `addon.xml`. The workflow tests and builds the add-on, publishes the ZIP and SHA-256 checksum, and creates a GitHub build-provenance attestation using OIDC.
