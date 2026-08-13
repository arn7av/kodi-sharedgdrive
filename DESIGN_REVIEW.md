# Architecture and performance review

This document records implementation tradeoffs and hard limits. User setup, operation, and troubleshooting are in `README.md`; the trust model, sensitive artifacts, and incident response are in `SECURITY.md`.

## Architecture

| Area | Design |
|---|---|
| Identity | One service account signs RS256 JWT assertions for the fixed Google token audience and `drive.readonly` scope. |
| Drive boundary | One configured shared-drive ID; no drive discovery. Queries and returned metadata are checked against that ID. |
| HTTP | Standard-library client, exact Google host allowlist, no followed redirects, bounded bodies/timeouts/retries. |
| Playback | Python validates metadata and gives Kodi an authenticated `alt=media` URL; media does not pass through Python. |
| State | Plaintext Kodi settings plus bounded token and folder JSON caches; no database or service process. |
| Export | User-triggered `.strm` snapshot with a bounded ownership manifest; no Google writes. |
| Packaging | Deterministic ZIP metadata, sorted entries, explicit file allowlist, and symlink rejection. |

The one-file diagnostic player is the only intentional shared-drive-boundary exception. It accepts only a validated raw ID or recognized `drive.google.com` file URL, refetches metadata, requires a downloadable non-trashed `video/*` item, and asks for confirmation outside the configured drive.

## Request cost

| Operation | Google requests when caches are usable |
|---|---|
| Token cache hit | 0 |
| Folder cache hit | 0 |
| Root folder cache miss | 1 request per results page, requested with `pageSize=1000` |
| Nested folder cache miss | 1 folder metadata request plus paginated listing |
| Playback startup | 1 fresh metadata request; token exchange only when required |
| Diagnostic playback, in-drive | 1 fresh metadata request |
| Diagnostic playback, outside-drive | 1 metadata request, confirmation, then a second metadata request |
| Optional media preflight | 1 additional one-byte range request; one refresh/retry on `401` |

The folder cache has a three-minute TTL, at most 50 folder entries, and at most 2 MiB. Cache hits do not rewrite it. Snapshot export bypasses the folder cache.

## Playback tradeoffs

Direct handoff preserves Kodi's native buffering and range-seeking but leaves no process available to refresh the Authorization header mid-stream. A refresh-capable proxy would remove that limitation at the cost of an inbound service, concurrent state, and a materially larger security surface.

The optional preflight uses `GET` with `Range: bytes=0-0`, not `HEAD`, because it more closely represents media access while avoiding a successful response body.

## API and traversal bounds

- JSON response: 8 MiB.
- Error body: 64 KiB.
- Folder page size: 1,000.
- Pages per folder: 10,000.
- Items per folder: 1,000,000.
- Exported folders: 100,000.
- Export depth: 256.
- Active plus stale manifest entries: 100,000.
- Manifest size: 16 MiB.
- Generated `.strm` content: 8 KiB.
- Manual stale-review selection: 5,000 entries.
- Repeated or malformed page tokens are rejected.
- Google's `incompleteSearch=true` aborts listing/export rather than treating partial data as complete.
- Transient JSON requests use bounded exponential retries; successful media preflight responses are not buffered.

These limits favor predictable resource use on embedded Kodi devices. Supporting larger drives requires code changes rather than silently bypassing the limits.

## Snapshot export internals

- Enumeration bypasses the folder cache.
- New ownership is checkpointed every 5,000 writes and on cancellation; failure-path checkpointing is best-effort.
- Stale classification occurs only after complete enumeration.
- Automatic pruning starts only after the final manifest is saved.
- Cleanup checkpoints every 500 removals.

Generic Kodi VFS backends do not offer uniform no-follow, exclusive-create, locking, or atomic-replace guarantees, so content and ownership checks cannot protect against a malicious concurrent writer with access to the export destination.

## Target-device validation matrix

- Large MKV and MP4 playback.
- Forward/backward seeking and reconnect after network loss.
- Playback crossing the one-hour token boundary.
- Authorization behavior across any Google media redirects.
- SMB/NFS `.strm` libraries.
- Diagnostic playback for in-drive and accessible out-of-drive files.
- Settings parsing and credential import on each supported Kodi generation/platform.
- Cancellation and restart during large snapshot exports and stale cleanup.
