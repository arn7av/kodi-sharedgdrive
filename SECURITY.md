# Security policy and threat model

## Protected assets

- The service-account private key.
- Short-lived Google access tokens.
- Shared-drive filenames, IDs, and media.

## Assumptions

- The service account is dedicated to this add-on and is a Viewer of only the intended shared drive.
- The Kodi host and the OS account running Kodi are trusted.
- Other Kodi add-ons execute with effectively the same user-level filesystem access and are therefore inside the local trust boundary.
- Google TLS endpoints and Kodi's TLS certificate store are trusted.

## Controls

- Read-only Drive OAuth scope.
- One configured shared-drive ID; no drive discovery.
- `corpora=drive` and `driveId=<configured ID>` on all folder listings.
- Metadata validation of `driveId` before nested browsing and normal/`.strm` playback.
- A separately invoked one-shot diagnostic exception that parses only recognized Drive links, performs fresh metadata checks, and requires confirmation outside the configured shared drive.
- MIME-type and `canDownload` validation before every playback mode.
- Strict Google HTTPS host allowlist and no followed HTTP redirects.
- Optional one-byte media preflight that does not buffer media, maps bounded Google error bodies, and treats unfollowed redirects as inconclusive.
- Explicit request timeouts, bounded JSON/error responses, page/item/folder limits, repeated-page-token detection, and bounded status-aware retries.
- No local server, inbound port, service process, database, telemetry, or error-reporting endpoint.
- Short-lived access tokens are cached with expiration and a credential fingerprint; JWT assertions are never persisted.
- Folder results use a three-minute, 50-entry, 2 MiB cache isolated by credential and shared-drive fingerprint; cached items are boundary-validated before use.
- No credential or playback-URL logging.
- No pickle, `eval`, dynamic code loading, or credential obfuscation.

## Residual risks

- Kodi stores add-on settings in plaintext. A local process or add-on acting as the Kodi user can read the private key.
- The profile contains a plaintext, short-lived access-token cache. A local process or add-on acting as the Kodi user can read it until it expires.
- Kodi itself may log or expose a resolved playback URL containing a short-lived bearer token. After handoff, Kodi/libcurl controls media redirects and whether authorization headers are forwarded; eliminating that platform-dependent residual risk would require the intentionally excluded local proxy.
- Anyone with the service-account private key can mint tokens using other scopes. Google-side access membership—not the scope string in this add-on—is the decisive boundary.
- The one-shot diagnostic player can access a public item or any item separately shared with the service account. Such direct shares expand the credential's real Google-side authority beyond the configured shared drive, even though they remain excluded from browsing, `.strm` files, and export.
- Playback requires at least 55 minutes of token lifetime at startup, but the token can still expire during very long playback. The design deliberately accepts this availability limitation rather than running a refresh proxy.
- Optional media preflight adds a request and can identify only failures present at probe time. Direct Kodi playback may later encounter a quota, permission, redirect, or token failure that the add-on cannot translate without a proxy.
- Folder-result caches contain short-lived filenames, file IDs, MIME types, and download capability metadata in the local profile.
- Snapshot manifests and `.strm` URLs disclose Google file IDs and exported filenames to anyone who can read the selected export destination. They contain no credentials or tokens.
- A malicious video container may target vulnerabilities in Kodi or its media libraries; this add-on does not inspect or transcode media.
- The add-on does not persist diagnostic input, but Kodi's input controls, playback history, debug logging, crash reporting, or operating environment may still expose values or resolved bearer-token URLs outside the add-on's control.

## Reporting and response

Do not attach service-account JSON, private keys, bearer tokens, Kodi logs containing resolved URLs, or private filenames to public reports.

If credentials may be exposed:

1. Disable or delete the affected key in Google Cloud IAM.
2. Create a replacement key if continued access is required.
3. Remove credentials in the add-on settings.
4. Import the replacement key.
5. Review the service account's shared-drive memberships and project IAM roles.
6. Remove `access_token.json` from the add-on profile if credential removal could not be performed through Kodi.

## Snapshot-export write boundary

Snapshot export writes only to the user-selected Kodi filesystem destination. It never creates, changes, or deletes Google Drive files and therefore does not require a writable Google role or broader OAuth scope. Idempotent re-export and stale removal are constrained by a validated local ownership manifest plus exact generated-content checks.

Manual stale removal is selected, confirmed, and revalidated immediately before each deletion. Opt-in automatic pruning runs only after complete fresh Drive enumeration and successful final manifest storage; cancellation or failure before that boundary cannot invoke it. It bypasses the 5,000-entry review UI limit but not manifest limits, path validation, active-path protection, exact-content checks, cancellation, or checkpointing. Neither mode removes directories.

Export destinations must be trusted against malicious concurrent writers. The implementation uses randomized sibling temporary files and revalidation, but generic Kodi VFS backends do not expose consistent no-follow, exclusive-create, locking, or atomic-replace primitives. Local/network peers with write access to the same destination may still race VFS operations. Do not export into a directory writable by untrusted users or services.
