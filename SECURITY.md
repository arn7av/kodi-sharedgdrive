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
- Metadata validation of `driveId` before nested browsing and playback.
- MIME-type and `canDownload` validation before playback.
- Strict Google HTTPS host allowlist and no HTTP redirects.
- Explicit request timeouts, bounded JSON responses, page/item/folder limits, repeated-page-token detection, and bounded status-aware retries.
- No local server, inbound port, service process, database, telemetry, or error-reporting endpoint.
- Short-lived access tokens are cached with expiration and a credential fingerprint; JWT assertions are never persisted.
- Folder results use a three-minute, 50-entry, 2 MiB cache isolated by credential and shared-drive fingerprint; cached items are boundary-validated before use.
- No credential or playback-URL logging.
- No pickle, `eval`, dynamic code loading, or credential obfuscation.

## Residual risks

- Kodi stores add-on settings in plaintext. A local process or add-on acting as the Kodi user can read the private key.
- The profile contains a plaintext, short-lived access-token cache. A local process or add-on acting as the Kodi user can read it until it expires.
- Kodi itself may log or expose a resolved playback URL containing a short-lived bearer token.
- Anyone with the service-account private key can mint tokens using other scopes. Google-side access membership—not the scope string in this add-on—is the decisive boundary.
- Playback requires at least 45 minutes of token lifetime at startup, but the token can still expire during very long playback. The design deliberately accepts this availability limitation rather than running a refresh proxy.
- Folder-result caches contain short-lived filenames, file IDs, MIME types, and download capability metadata in the local profile.
- Snapshot manifests and `.strm` URLs disclose Google file IDs and exported filenames to anyone who can read the selected export destination. They contain no credentials or tokens.
- A malicious video container may target vulnerabilities in Kodi or its media libraries; this add-on does not inspect or transcode media.

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

Snapshot export writes only to the user-selected Kodi filesystem destination. It never creates, changes, or deletes Google Drive files and therefore does not require a writable Google role or broader OAuth scope. Idempotent re-export and stale removal are constrained by a validated local ownership manifest plus exact generated-content checks. Stale removal is explicit, selected, confirmed, and revalidated immediately before each file deletion; it never removes directories.

Export destinations must be trusted against malicious concurrent writers. The implementation uses randomized sibling temporary files and revalidation, but generic Kodi VFS backends do not expose consistent no-follow, exclusive-create, locking, or atomic-replace primitives. Local/network peers with write access to the same destination may still race VFS operations. Do not export into a directory writable by untrusted users or services.
