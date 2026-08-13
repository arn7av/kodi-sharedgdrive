# Shared Google Drive for Kodi

A small Kodi video add-on for browsing and playing original video files from one Google shared drive through a dedicated service account.

## Capabilities and limits

Supported:

- One service account and one configured shared drive.
- Folder browsing and original-file playback.
- Short-lived token caching and a three-minute folder cache.
- Optional one-file diagnostic playback by Drive file ID or recognized Drive URL.
- Optional snapshot export of `.strm` files to a Kodi-writable location.

Not supported:

- User OAuth, multiple accounts, drive discovery, My Drive, Shared with me, or search.
- Metadata scraping, watched-state sync, transcoding, alternate resolutions, subtitles, or Google Photos.
- Background synchronization, a database, or a local proxy/server.

## Install

1. Enable installation from unknown sources in Kodi.
2. In **Settings → File manager**, add `https://k.atx.sx/` as a **Web server directory (HTTPS)** source.
3. Open **Add-ons → Install from zip file**, select that source, and install `repository.sharedgdrive.zip`.
4. Open **Install from repository → Shared Google Drive Repository → Video add-ons → Shared Google Drive** and install the add-on.

Updates are then available through the installed repository. To force a refresh, open the repository's context menu and select **Check for updates**.

## Configure Google and Kodi

1. Create or select a Google Cloud project and enable the Google Drive API.
2. Create a dedicated service account with:
   - no Google Cloud project roles;
   - no domain-wide delegation;
   - **Viewer** access to only the intended shared drive.
3. Create a JSON key for the service account.
4. Copy the shared-drive ID from its URL. For `https://drive.google.com/drive/folders/0ABExampleDriveId`, use `0ABExampleDriveId`.
5. Open the add-on settings, enter the shared-drive ID, and select **Import service-account JSON**.
6. Confirm Kodi shows **Credentials imported**, then remove the temporary copy of the JSON key.

The importer retains only `client_email` and `private_key`. Kodi stores them as plaintext add-on settings, so other software running as the Kodi OS user can read them. See `SECURITY.md` for the full trust model and incident response.

## Use

Open **Add-ons → Video add-ons → Shared Google Drive**. Kodi shows folders plus downloadable files whose MIME type starts with `video/`.

To add it to the home screen's favourites:

1. Highlight **Shared Google Drive** without opening it.
2. Open the context menu (usually long-press **OK/Select** or press `C`).
3. Select **Add to favourites**.

### Playback

The add-on validates fresh Drive metadata, then hands Kodi this authenticated endpoint:

```text
https://www.googleapis.com/drive/v3/files/<file-id>?alt=media&supportsAllDrives=true
```

Media bytes go directly from Google to Kodi; Python is not a media proxy. Playback starts with a token that has at least 55 minutes remaining. Very long playback or a seek/reconnect after token expiry may require restarting the item.

**Check media access before playback** is optional and disabled by default. It adds a one-byte range request that can expose a current permission or quota failure before Kodi starts playback, but it cannot guarantee later playback success.

### One-file diagnostic playback

**Play one Google Drive file** accepts a raw file ID or these `drive.google.com` URL forms:

- `/file/d/<id>`
- `/open?id=<id>`
- `/uc?id=<id>`

The add-on extracts the ID locally and never requests the pasted URL. The file must be public or accessible to the service account, be a downloadable `video/*` item, and be outside the trash. Playing an item outside the configured shared drive requires confirmation. Links that require a separate `resourcekey` are not supported; share the file directly with the service account instead.

### Snapshot `.strm` export

Snapshot export is disabled by default. When enabled, **Export snapshot now** creates a corresponding `.strm` tree for downloadable videos in a selected local, SMB, or NFS destination. Empty and non-video-only folders are omitted; unsafe names are sanitized and collisions may receive an ID suffix. Each `.strm` file contains only a plugin playback URL and Google file ID.

- Google Drive remains read-only; export writes only to the selected Kodi filesystem destination.
- Re-export preserves correct files, creates missing files, and never overwrites unrelated or modified files.
- Stale files are reported, not deleted, unless manual cleanup or **Automatically delete stale exported files** is explicitly used.
- Cleanup deletes only exporter-owned files whose contents still exactly match the generated URL; it never deletes directories or Google Drive content.
- Do not use a destination writable by untrusted users or services.

## Troubleshooting

| Symptom | Check |
|---|---|
| Repository ZIP is not visible | Add `https://k.atx.sx/` as **Web server directory (HTTPS)**, not as a video source. |
| Update is not visible | Run **Check for updates** on **Shared Google Drive Repository** and confirm `https://k.atx.sx/addons.xml` advertises the expected version. |
| Add-on says it is not configured | Enter the shared-drive ID and import the JSON again; verify the **Credentials imported** notification appears. |
| Google rejects authentication | Verify the key is active, the Drive API is enabled, and the TV's date/time is correct. |
| Access is denied or the drive is empty | Add the service-account email as a Viewer of the shared drive itself. Only folders and downloadable `video/*` files are listed. |
| Listing appears stale | Use **Clear folder cache**. Importing or removing credentials already clears both token and folder caches. |
| **The operation failed unexpectedly** | Inspect Kodi's log for the add-on's error marker and adjacent Kodi/Python errors. This message indicates an unclassified exception, not necessarily a Google failure. |

Kodi exposes its logs as:

```text
special://logpath/kodi.log
special://logpath/kodi.old.log
```

On the tested webOS package, useful paths are:

```text
Current log:     /media/developer/apps/usr/palm/applications/org.xbmc.kodi/.kodi/temp/kodi.log
Previous log:    /media/developer/apps/usr/palm/applications/org.xbmc.kodi/.kodi/temp/kodi.old.log
Installed add-on:/media/developer/apps/usr/palm/applications/org.xbmc.kodi/.kodi/addons/plugin.video.sharedgdrive/
Add-on profile:  /media/developer/apps/usr/palm/applications/org.xbmc.kodi/.kodi/userdata/addon_data/plugin.video.sharedgdrive/
```

With a Rust `ares` device named `tv`:

```sh
# Confirm the installed version.
~/.cargo/bin/ares-shell -d tv -r 'grep -m1 "<addon id=\"plugin.video.sharedgdrive\"" /media/developer/apps/usr/palm/applications/org.xbmc.kodi/.kodi/addons/plugin.video.sharedgdrive/addon.xml'

# Find relevant failures in the current log.
~/.cargo/bin/ares-shell -d tv -r 'grep -n -Ei "plugin.video.sharedgdrive|Shared Google Drive unexpected error|EXCEPTION|GetDirectory" /media/developer/apps/usr/palm/applications/org.xbmc.kodi/.kodi/temp/kodi.log'
```

Review log lines locally before sharing them; logs and the add-on profile can contain sensitive data. The marker's privacy limits and log-redaction guidance are in `SECURITY.md`.

The absolute webOS paths are package-specific. On other platforms, resolve Kodi's `special://logpath` and add-on profile locations instead of reusing them.

## Development

Run the tests from the repository root:

```sh
python3 -m unittest discover -s tests -v
```

Build the deterministic add-on ZIP:

```sh
python3 package.py
```

Build the static Kodi repository site:

```sh
VERSION=$(python3 -c "import xml.etree.ElementTree as ET; print(ET.parse('addon.xml').getroot().attrib['version'])")
python3 build_repository.py \
  --addon-archive "../plugin.video.sharedgdrive-${VERSION}.zip" \
  --output ../kodi-repository-site
```

Packaging, architecture, and performance details are in `DESIGN_REVIEW.md`.

## CI and releases

`.github/workflows/ci.yml` runs tests, Python/XML validation, package generation, repository generation, and ZIP checks for pull requests and pushes to `main` on Python 3.9 and 3.13.

A release tag must be `v<major>.<minor>.<patch>`, match `addon.xml`, and point to a commit in `main`. `.github/workflows/release.yml` preserves prior versioned archives, builds and attests release ZIPs, deploys Pages, and publishes the GitHub Release.

Pages deployments target the `kodi-repository` GitHub environment.

Release procedure:

1. Bump `addon.xml` and merge/push the commit to `main`.
2. If repository URLs or bootstrap metadata changed, also bump `repository.sharedgdrive/addon.xml`.
3. Push a matching annotated tag. Never reuse or move a published tag.
