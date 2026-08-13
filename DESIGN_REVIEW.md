# Security and performance design review

This review covers the intended single-service-account, single-shared-drive use case plus its explicit one-shot diagnostic exception.

## Security conclusions

No critical remote-code-execution, open SSRF, or query-injection path was identified. Normal browse, `.strm`, and playback flows remain confined to the configured shared drive; the visible one-shot diagnostic action intentionally permits a user-confirmed item outside that drive.

Primary controls:

- Dedicated service-account flow with fixed RS256 JWT audience and `drive.readonly` scope.
- Exact HTTPS host allowlist for Python API requests; redirects are never followed.
- One configured shared-drive ID and no drive discovery.
- ID syntax validation, `corpora=drive`, and `driveId` constraints.
- `driveId` metadata validation for listed folders/videos and fresh normal playback metadata.
- Diagnostic references accept only validated raw IDs or recognized `drive.google.com` file links; arbitrary hosts and pasted-URL requests are rejected.
- Diagnostic input is ephemeral and cannot enter plugin URLs, caches, browsing, `.strm` files, or manifests.
- Downloadable `video/*` filtering plus trash checks in both normal and diagnostic playback.
- Bounded token/folder caches with credential/drive fingerprints and atomic local replacement.
- Bounded API JSON, pagination, item counts, folder traversal, repeated-page-token rejection, and fail-closed handling of Google's `incompleteSearch` signal.
- No inbound server, background process, database, pickle, telemetry, or secret logging.
- Fail-closed package allowlist and symlink rejection.
- Snapshot manifests validate version, drive ID, file IDs, relative `.strm` paths, count, and size.
- Snapshot writes use randomized sibling temporary files and rollback backups.
- Manual stale removal is selected, confirmed, exact-content revalidated, and file-only.
- Optional auto-prune can run only after a complete fresh export and successful final manifest save; it retains manifest/path/content validation, active-path protection, cancellation, and checkpointing.

Residual security boundaries:

1. Kodi settings contain the long-lived private key in plaintext. Anyone acting as the Kodi OS user can read it.
2. Kodi receives a short-lived bearer token in the resolved pipe-header URL. Kodi, debug logs, crash reports, skins, or other local add-ons may expose it.
3. Google-side service-account membership is the real drive boundary. The OAuth scope itself is not restricted to one shared drive.
4. An explicitly supplied public or directly shared item can be played through the diagnostic action after warning. Direct sharing therefore expands the credential's actual authority beyond the configured drive, even though discovery/export remain bounded.
5. There is a metadata-to-media race: a file could move after metadata validation but before Kodi requests media. Restricting the service account to only the intended drive limits the impact.
6. Generic Kodi VFS backends do not consistently provide no-follow, exclusive-create, locks, or atomic replacement. Export destinations must not be writable by untrusted concurrent actors.
7. Exported filenames and file IDs are visible to readers of the export destination.
8. Dependency trust is inherited from the Kodi repository supplying PyCryptodome.

## Client/API performance

Normal cached interactive request cost:

- Token cache hit: local read/hash only.
- Folder cache hit: local bounded JSON read; zero Drive requests.
- Root folder miss: one request per 1,000 returned children.
- Nested folder miss: one fresh folder metadata request plus paginated listing.
- Playback startup: one fresh metadata request; token exchange only when required.
- One-shot diagnostic startup: one metadata request for an in-drive item; an outside-drive result triggers confirmation followed by a fresh token-lifetime check and second metadata request with an explicit one-shot override; no folder-cache interaction.
- With media preflight enabled: normally one additional one-byte range request and network round trip; a `401` causes one token refresh and retry.

Optimizations:

- Three-minute folder cache, 50-folder limit, 2 MiB limit.
- Cache hits do not rewrite the cache.
- Kodi directory entries are submitted in one batch.
- Drive query excludes unrelated MIME types server-side.
- Requested fields are limited to routing, boundary, display, and download capability metadata.
- Pagination uses `pageSize=1000` and detects invalid/repeated page tokens.
- Optional cache write failure does not fail successful online operations.
- One API request receiving `401` refreshes the service-account token and retries once; future requests can repeat this process if a long export crosses another token lifetime.
- Optional media preflight does not read a successful response body, does not follow redirects, and maps known bounded 403 error responses before Kodi startup.

Tradeoffs:

- A cache file is parsed as one JSON document. Its 2 MiB/50-entry bounds avoid database complexity.
- Interactive listings are materialized before Kodi rendering so a complete result can be cached and sorted.
- Snapshot enumeration is incremental by video but retains ownership/name maps proportional to exported files.
- Export hard limits prevent unlimited hostile hierarchy expansion, but very large legitimate drives may hit those limits and require code/config review rather than silently exhausting the device.

## Retrieval/download performance

Media bytes do not pass through Python. Kodi directly requests:

```text
https://www.googleapis.com/drive/v3/files/<id>?alt=media&supportsAllDrives=true
```

with an Authorization header. Therefore Python is not a throughput, buffering, or range-seeking bottleneck. Kodi/libcurl controls connection reuse, `Range` requests, media buffering, timeouts, and reconnect behavior.

Playback startup requires a token with at least 55 minutes remaining. This avoids starting most films with an almost-expired token, but a static resolved header still cannot refresh during multi-hour playback. A seek/reconnect after expiry may fail. Solving that requires a refresh-capable local proxy, which is intentionally excluded because it adds an inbound service, concurrency/state management, and a larger security surface.

The default-off media preflight uses `Range: bytes=0-0` rather than `HEAD`, because a range GET is more representative of actual media access. It closes successful responses without reading media and can surface a current Google 403 before resolution, at the cost of one request and startup latency. An unfollowed redirect is treated as inconclusive. A quota or permission change can still happen after the probe, so it is not a guarantee of successful playback.

Diagnostic playback uses the same authenticated `alt=media` endpoint and direct Kodi transfer after fresh metadata validation. Outside-drive playback refetches metadata and re-establishes the 55-minute token policy immediately after confirmation. The pasted reference is never fetched; only an extracted Drive file ID reaches the fixed Google API endpoint. Public visibility does not trigger an anonymous or direct-link fallback. Resource-key-protected sharing links are intentionally unsupported rather than adding another credential-like input/header path. Share the file directly with the configured service-account email or move it into the configured shared drive, then use its file ID or a link that does not rely on a resource key.

Target-device validation should cover:

- Large MKV and MP4 playback.
- Forward/backward seeks.
- Network loss and reconnect.
- Playback crossing the one-hour token boundary.
- Whether the target Kodi/libcurl follows any Google media redirect and how it handles Authorization across hosts.
- SMB/NFS-hosted `.strm` libraries with direct Google media playback.
- Settings-launched one-shot playback for an in-drive, public, and service-account-shared file.

## Snapshot/export performance

- Export bypasses the folder cache and obtains a token with at least 55 minutes remaining.
- API `401` responses refresh once per request, allowing exports longer than one token lifetime.
- Cancellation is checked before folders, pages, videos, and writes; an in-flight socket read remains bounded by the HTTP timeout.
- Existing files are not written by default.
- Re-export is idempotent: correct exporter-owned files are retained without rewriting, while missing files are created and modified/unowned files are skipped.
- Parent folder checks are cached during a run.
- Partial ownership is checkpointed every 5,000 new writes and on handled exceptions/cancellation.
- Manifest writes are skipped when unchanged.
- Manifest size and entry counts are bounded.
- Stale classification happens only after a complete fresh enumeration; Google's explicit `incompleteSearch` signal aborts the operation.
- Default-off auto-prune begins only after stale classification and successful final manifest storage.
- Auto-prune bypasses the 5,000-entry GUI review cap, not the 100,000-entry manifest limit or any ownership checks.
- Every stale file is exact-content revalidated immediately before deletion; cleanup is cancellable and persists progress every 500 successful removals.
- No cleanup path deletes directories or Google Drive content.

## Operational recommendations

- Keep the service account Viewer-only on exactly one shared drive for normal operation.
- Do not directly share unrelated items with it except when intentionally testing the diagnostic path; remove test shares afterward.
- Do not enable domain-wide delegation or unrelated IAM roles.
- Use a trusted local/export share destination.
- Keep Kodi debug logs and backups protected.
- Rotate the service-account key after suspected exposure.
- Run snapshot export manually and review stale files before removal; enable auto-prune only when unattended cleanup is preferred and the export destination is trusted.
- Test long playback and seeking on the exact Kodi device/build before relying on unattended multi-hour playback.
