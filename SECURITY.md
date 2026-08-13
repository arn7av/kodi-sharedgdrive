# Security policy and threat model

## Trust boundary

The service account should be dedicated to this add-on, have no unrelated Google Cloud roles or domain-wide delegation, and be a Viewer of only the intended shared drive. Google-side membership is the primary access boundary: the `drive.readonly` OAuth scope does not restrict a credential to one drive.

The Kodi host, its OS account, and installed Kodi add-ons are inside the local trust boundary. Kodi add-ons generally share the same user-level filesystem access.

## Controls

- Normal browsing, export, and playback are restricted to one configured shared-drive ID.
- Folder queries use `corpora=drive`, `driveId`, and `includeItemsFromAllDrives`.
- Item IDs, `driveId`, trash state, MIME type, and `capabilities.canDownload` are validated.
- Python requests are limited to `oauth2.googleapis.com` and `www.googleapis.com` over HTTPS; redirects are not followed.
- API responses, error bodies, pagination, item counts, traversal depth, retries, and timeouts are bounded.
- Tokens and folder caches are tied to credential/drive fingerprints and have bounded lifetimes and sizes.
- The add-on runs no inbound server, background service, telemetry, or error-reporting endpoint.
- The package uses an explicit file allowlist and rejects symlinks.
- Snapshot cleanup requires a validated ownership manifest and exact generated-content checks.

The one-file diagnostic action is an explicit exception to the configured-drive boundary. After user confirmation, it can play a public item or any downloadable item otherwise accessible to the service account. Shares outside the configured drive expand the credential's actual Google-side access.

## Sensitive local artifacts

All paths below are relative to the add-on profile unless noted otherwise.

| Artifact | Contents | Lifetime |
|---|---|---|
| Kodi add-on `settings.xml` | Service-account email, private key, shared-drive ID | Until credentials/settings are removed |
| `access_token.json` | Plaintext bearer token and expiration | Token validity is normally less than one hour; the file remains until overwritten or cleared |
| `folder_results.json` | Filenames, file IDs, MIME types, and capability metadata | Three-minute logical TTL; file may remain longer |
| Snapshot manifest and `.strm` files | Exported filenames and Google file IDs; no key or bearer token | Until removed from the export destination |
| Kodi logs/crash reports | May contain private names or a resolved playback URL after Kodi takes control | Platform-dependent |

The service-account JSON is read through Kodi VFS during import but is not copied into the add-on profile. Only `client_email` and `private_key` are retained.

## Residual risks

- Any process or add-on running as the Kodi OS user can read plaintext settings and caches.
- Anyone who obtains the private key can mint tokens with scopes other than the one used here.
- Kodi/libcurl controls media redirects and Authorization handling after playback handoff. Eliminating that platform-dependent risk would require the intentionally excluded local proxy.
- Playback uses a static short-lived token; very long sessions or reconnects after expiration can fail.
- Metadata can change after validation but before Kodi requests media.
- Generic Kodi VFS backends do not uniformly provide no-follow, exclusive-create, locking, or atomic replacement. Export only to trusted destinations.
- A malicious media file can target vulnerabilities in Kodi or its media libraries; this add-on does not inspect or transcode media.

## Handling diagnostics

Log locations and webOS `ares` commands are documented once in `README.md` under **Troubleshooting**.

Treat the add-on profile and unreviewed Kodi logs as secret-bearing:

- Do not publish or attach the complete profile, `settings.xml`, caches, service-account JSON, or unrestricted logs.
- Do not print setting values merely to confirm that a setting exists.
- Before sharing a log excerpt, remove Authorization data, URLs, Drive file/folder names and IDs, network credentials, local usernames/addresses, and unrelated add-on activity.
- Share the smallest relevant excerpt.

For unclassified exceptions, the add-on writes a bounded marker containing only the action, exception class, and final Python source location. It deliberately omits exception text, traceback locals, API bodies, credentials, Drive names/IDs, and playback URLs.

## Incident response

If the private key or an unredacted credential-bearing artifact may have been exposed:

1. Delete or disable the affected key in Google Cloud IAM.
2. Create a replacement key if access is still needed.
3. Select **Remove imported credentials** in the add-on.
4. Import the replacement JSON.
5. Review the service account's shared-drive memberships, direct file/folder shares, project IAM roles, and domain-wide delegation status.
6. If credential removal could not run, remove `access_token.json` from the add-on profile as well.

Do not include private keys, bearer tokens, unreviewed logs, resolved playback URLs, or private file names/IDs in public security reports.
