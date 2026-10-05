# RomHoard

A self-hosted ROM library manager. Point it at your ROM folders, and it handles the rest: identifies games, fetches metadata and artwork from ScreenScraper, and lets you build collections to send directly to your devices. Discover curated collections from the community or share your own.

![Library View](documentation/screenshots/library.png)

## What It Does

- Scans your ROM directories and identifies games automatically
- Fetches cover art, screenshots, and game metadata from ScreenScraper.fr
- Build collections to curate your favorites, top lists, or themed sets
- Browse community collections at [romhoard.cubical.fyi](https://romhoard.cubical.fyi) and import them into your library
- Send collections or individual games to handhelds via FTP/SFTP, with images
- Includes presets for common device OSes (MuOS, KNULLI, Batocera, etc.)

![Game Detail](documentation/screenshots/game-detail.png)

## Collections

Collections are game lists you can share, export, and sync to devices. Create a "Top 20 SNES" list, import someone else's curated RPG picks, or build themed sets for different handhelds. Collections track games by name, so they work even if your ROM files are named differently.

![Collection View](documentation/screenshots/collection.png)

## Collection Hub

Browse and download community-curated collections at [romhoard.cubical.fyi](https://romhoard.cubical.fyi). Find ready-made lists like "Best GBA RPGs" or "Hidden SNES Gems" and import them directly into your library. Collections are just game lists, no ROMs are uploaded or downloaded.

You can also export your own collections and share them with the community. Build something great? Upload it to the hub for others to discover.

## Send to Device

Select games or an entire collection and send them directly to your handheld over FTP or SFTP. RomHoard maps systems to the right folders based on device presets, so ROMs end up where your emulator expects them, with the images.

![Send to Device](documentation/screenshots/send-modal.png)

## Why RomHoard?

RomHoard didn't start as a ROM manager. The original idea was to discover and share curated game lists, because having 2000 ROMs and not knowing what to play is a real problem.

I wanted to send those collections straight to my retro handhelds. Turns out you need to manage ROMs to do that, so here we are.

### RomHoard vs Romm

[Romm](https://romm.app/) is great if you want to carefully curate your library with metadata from lots of sources.

RomHoard is collections-first: discover curated lists, share your own, and send them to your devices. The ROM management exists to support that.

You can run both, they're good neighbors.

## Installation

RomHoard runs as a Docker container with PostgreSQL.

### 1. Create a directory for your setup

```bash
mkdir romhoard && cd romhoard
```

### 2. Download the compose file

```bash
curl -O https://raw.githubusercontent.com/cubicalbatch/romhoard/master/docker-compose.yml
```

### 3. Point it at your ROMs

By default, the container looks for ROMs in a `./roms` folder. Edit the compose file to mount your actual ROM directory:

```yaml
volumes:
  - /path/to/your/roms:/roms
```

### 4. Start it up

```bash
docker compose up -d
```

Open http://localhost:6766 in your browser.

## Troubleshooting ROM identification

From the directory containing your Compose file, create a report to share:

```bash
docker compose exec -T romhoard python manage.py diagnose_roms > romhoard-debug.json
```

For a local installation: `uv run ./manage.py diagnose_roms > romhoard-debug.json`.
The command reads saved scan directories (or `ROM_LIBRARY_ROOT` if none are saved).
Pass a directory to diagnose just that path. It does not rescan, modify the
database, compute hashes, or contact metadata services.

The default report groups files with the same detection outcome, giving a count
and representative fields for each group. It includes stored-versus-predicted
systems, archive outcomes, scan counts, and configuration drift; it excludes
paths, filenames, game titles, hashes, credentials, and raw errors. For every
file's relative path, game title, and stored CRC32/SHA1, add `--details` before
`>` (optionally with a single scan directory to keep the file small). Review
this detailed report before sharing: hashes can identify ROM content. Neither
report uploads itself.

The report describes the **current** detector output only. It groups by
outcome and contains no queue, quota, or error-log data, so it cannot
reconstruct what happened during earlier scans (for example a past
ScreenScraper quota cutoff or partially processed runs).

### How identification works

System detection is folder/filename based and happens before any metadata is
fetched:

- **Folder aliases are explicit and case-insensitive.** Each system in
  `library/systems.json` lists accepted folder names (`folder_names`),
  including No-Intro-style names like `Sony - Playstation Portable`,
  `SNK - Neo Geo`, `Sega - Genesis - MegaDrive`, `Atari - ST`,
  `Commodore - C64`, and `NEC - Super Grafx`. The folder component nearest the
  file decides; a matching folder inside an archive is final.
- **A folder that accepts an extension beats another system's exclusive claim
  on it.** `.ipf` under `Atari - ST` is Atari ST (not Amiga), `.tap` under
  `Commodore - C64` is C64 (not ZX Spectrum), `.pce` under
  `NEC - Super Grafx` is SuperGrafx (not PC Engine).
- **Outside a matching folder, only exclusive extensions identify a file.**
  Shared extensions (`.bin`, `.iso`, `.cue`, …) without a matching folder are
  skipped. A misfiled file can still surface under its extension's
  owner — the reported `.xex` under an incompatible PSP folder stayed
  Xbox 360.
- **Listed non-ROM types never import** — e.g. music dumps like `.spc`, saves,
  and docs. `.md` is accepted only inside a folder configured to claim it; in
  practice that is Genesis/Mega Drive.
- **ZIP and 7z archives are opened and their members scanned individually**,
  except archive-as-ROM systems (Neo Geo, arcade, MS-DOS) where the archive
  itself is the game. Package formats that are not ROM archives — e.g. Xbox
  360 XBLA/XBLIG content packages — stay unsupported no matter which folder
  they sit in. A folder name is a label, never content proof.

### Fixing records stored under the wrong system

Rescans do not reclassify existing ROM paths: a rescan skips files already
stored and imports only what is missing. If your library was scanned by an
older release
that didn't know your folder names (issue
[#3](https://github.com/cubicalbatch/romhoard/issues/3): files stored as Amiga,
ZX Spectrum, and PC Engine games that are really Atari ST, C64, and SuperGrafx),
those records stay wrong until you remove just those records and rescan.

1. **Update and restart** so the current system config is loaded (the container
   runs `sync_systems` on startup; local installs:
   `uv run ./manage.py sync_systems`).
2. **Back up your database** before deleting anything:

   ```bash
   docker compose exec -T db pg_dump -U romhoard romhoard > romhoard-backup.sql
   ```

3. **Preview** with the report above: compare the stored vs predicted system
   for the affected folders.
4. **Delete only the exact mismatched records.** Identify the ROM records the
   report shows with a wrong stored system — by **both** their physical path
   **and** the wrong stored system, never by folder alone: most files in the
   affected folders were classified correctly and must stay (in the issue #3
   report only 86 of 25,636 stored C64 records were wrong). Remove just those
   database records, then let orphan cleanup handle games left with no ROM
   sets. Never delete whole games as a shortcut: one game can hold ROM
   variants from several folders, and deleting the game also removes the
   metadata, images, and collection links of its genuine variants. Deleting a
   ROM record alone leaves the rest of the game intact; metadata, images, and
   collection links are only removed when an emptied game is deleted. No
   deletion touches your ROM files.

5. **Rescan** the affected folders (Scan page → Rescan, or
   `uv run ./manage.py scan`). New records import under the correct system and
   fetch fresh metadata; don't reattach old metadata IDs — wrong-system IDs
   must not survive onto the new games.
6. **Misfiled content is not a detector bug — move it out.** In the issue #3
   library, the `Sony - Playstation Portable` folder contained no identifiable
   PSP content: all 208 readable archives carried Xbox 360 package markers
   (`Content/…/000D0000`, `Cache/TU_…`, `default.xex`), and none of the 831
   archive members showed a PSP disc marker (`PSP_GAME/`, `UMD_DATA.BIN`,
   `EBOOT.PBP`, `PARAM.SFO`). RomHoard predicted PSP there purely from the
   folder label — filenames alone cannot verify disc content. After moving
   such files out, whatever remains unsupported (packages, resources, archives
   the scanner cannot read) stays unsupported, and the report's archive read
   failures mean those files need direct inspection: the report cannot tell
   corruption from permissions or unsupported formats.

## Configuration

Most things are configured through the web UI. For ScreenScraper metadata fetching, you'll need a free account at [screenscraper.fr](https://www.screenscraper.fr/).

See [documentation/docker.md](documentation/docker.md) for environment variables and advanced configuration.

## Tech Stack

Django, PostgreSQL, HTMX, Alpine.js, Tailwind CSS, Procrastinate for background jobs.

## Contributing

Contributions for targeted bug fixes are welcome. For new features, please open an issue to discuss with me first before submitting a pull request.
