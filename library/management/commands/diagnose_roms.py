"""Explain current ROM scan decisions without changing the library."""

import json
import os
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from library import archive
from library.extensions import (
    COMPRESSED_EXTENSIONS,
    IMAGE_EXTENSIONS,
    get_full_extension,
    is_compound_rom_extension,
    load_non_rom_extensions,
)
from library.models import Game, ROM, ScanJob, ScanPath, System
from library.scanner import (
    build_extension_map,
    detect_system_details,
    is_bios_file,
    resolve_folder_decision,
    should_expand_archive,
)
from library.system_loader import get_systems_config


class Command(BaseCommand):
    help = "Write a read-only, privacy-safe ROM identification report as JSON"

    def add_arguments(self, parser):
        parser.add_argument(
            "path", nargs="?", help="Scan directory (defaults to saved scan paths)"
        )
        parser.add_argument(
            "--details",
            action="store_true",
            help="Include relative filenames, game names, and ROM hashes; review before sharing",
        )

    def handle(self, *args, **options):
        details = options["details"]
        if details:
            self.stderr.write(
                "Private details enabled: review the report before sharing."
            )

        if options["path"]:
            paths = [os.path.abspath(options["path"])]
            if not os.path.isdir(paths[0]):
                raise CommandError("Scan directory does not exist or is not readable")
        else:
            paths = list(ScanPath.objects.values_list("path", flat=True))
            if not paths and settings.ROM_LIBRARY_ROOT:
                paths = [settings.ROM_LIBRARY_ROOT]
            if not paths:
                raise CommandError("No saved scan paths; supply a directory")
            paths = sorted({os.path.abspath(path) for path in paths}, key=len)
            # A parent walk already includes every nested saved scan path.
            paths = [
                path
                for path in paths
                if not any(
                    path.startswith(parent.rstrip(os.sep) + os.sep)
                    for parent in paths
                    if parent != path and len(parent) < len(path)
                )
            ]

        systems = list(System.objects.all())
        exclusive_map = build_extension_map(systems)
        packaged = {system["slug"]: system for system in get_systems_config()}
        public_aliases = {
            name.casefold(): name
            for config in packaged.values()
            for name in config["folder_names"]
        }
        public_extensions = (
            {
                ext.lower()
                for config in packaged.values()
                for ext in config["extensions"]
            }
            | load_non_rom_extensions()
            | COMPRESSED_EXTENSIONS
            | IMAGE_EXTENSIONS
        )
        allowed_sources = {key for key, _ in Game.SOURCE_CHOICES}
        labels = {
            system.pk: system.slug
            if system.slug in packaged
            else f"custom-system-{index}"
            for index, system in enumerate(
                (s for s in systems if s.slug not in packaged), 1
            )
        }
        labels.update(
            {system.pk: system.slug for system in systems if system.slug in packaged}
        )
        drift = []
        for system in systems:
            config = packaged.get(system.slug)
            if config is None:
                drift.append(
                    {"system": labels[system.pk], "fields": ["not_in_packaged_config"]}
                )
                continue
            fields = [
                field
                for field in (
                    "extensions",
                    "exclusive_extensions",
                    "folder_names",
                    "archive_as_rom",
                    "screenscraper_ids",
                )
                if getattr(system, field)
                != config.get(field, [] if field != "archive_as_rom" else False)
            ]
            if fields:
                drift.append({"system": labels[system.pk], "fields": fields})
        drift.extend(
            {"system": slug, "fields": ["missing_from_database"]}
            for slug in packaged.keys() - {system.slug for system in systems}
        )

        def folder_info(path, system, root):
            """Expose only public system aliases, never private directory names.

            Reports the folder component nearest the filename, mirroring the
            scanner's nearest-folder decision.
            """
            if not system:
                return None, None
            aliases = {name.casefold() for name in system.folder_names}
            parts = Path(path).parts
            for index in range(len(parts) - 2, -1, -1):
                part = parts[index]
                if part.casefold() in aliases:
                    scope = (
                        "archive_member"
                        if not os.path.isabs(path)
                        else "ancestor_of_scan_root"
                        if index < len(Path(root).parts) - 1
                        else "within_scan_root"
                    )
                    return public_aliases.get(part.casefold(), "custom_alias"), scope
            return None, None

        def dump(value):
            self.stdout.write(
                json.dumps(value, ensure_ascii=True, separators=(",", ":")), ending=""
            )

        summary = Counter()
        predicted_counts = Counter()
        stored_counts = Counter()
        self.stdout.write('{"schema":1,"details":', ending="")
        dump(details)
        self.stdout.write(',"config_drift":', ending="")
        dump(drift)
        self.stdout.write(',"scan_paths":[', ending="")

        for path_index, root in enumerate(paths, 1):
            if path_index > 1:
                self.stdout.write(",", ending="")
            root = os.path.abspath(root)
            prefix = root.rstrip(os.sep) + os.sep
            last_job = (
                ScanJob.objects.filter(path=root)
                .order_by("-started_at")
                .values(
                    "status",
                    "files_processed",
                    "added",
                    "skipped",
                    "deleted_roms",
                    "errors",
                )
                .first()
            )
            if last_job:
                last_job["error_count"] = len(last_job.pop("errors"))
            self.stdout.write('{"id":', ending="")
            dump(path_index)
            self.stdout.write(',"exists":', ending="")
            dump(os.path.isdir(root))
            self.stdout.write(',"last_scan":', ending="")
            dump(last_job)
            self.stdout.write(',"files":[', ending="")
            first = True
            sequence = 0
            samples = {}
            stored = {}
            fields = [
                "file_path",
                "archive_path",
                "path_in_archive",
                "rom_set__game__system_id",
                "rom_set__game__name_source",
                "rom_set__game__metadata_match_failed",
            ]
            if details:
                fields += [
                    "crc32",
                    "sha1",
                    "rom_set__game__name",
                    "rom_set__game__screenscraper_id",
                ]
            for row in (
                ROM.objects.filter(
                    Q(file_path__startswith=prefix) | Q(archive_path__startswith=prefix)
                )
                .values(*fields)
                .iterator(chunk_size=2000)
            ):
                stored.setdefault(row["file_path"], []).append(row)

            def emit(
                file_path,
                extension,
                predicted,
                reason,
                kind="file",
                member="",
                collapsed=False,
                crc32="",
            ):
                nonlocal first, sequence
                sequence += 1
                records = stored.pop(file_path + ("!" + member if member else ""), [])
                folder_system = None
                folder_path = ""
                if reason in (
                    "internal_folder",
                    "folder",
                    "archive_folder",
                    "archive_as_rom",
                ):
                    folder_system = predicted
                    folder_path = member if reason == "internal_folder" else file_path
                elif reason in (
                    "exclusive_extension",
                    "unsupported_extension",
                    "non_rom_extension",
                ):
                    # Rejected/exclusive decisions still report the nearest
                    # folder context that produced them.
                    folder_system, folder_path = resolve_folder_decision(
                        file_path, systems, member if kind == "member" else ""
                    )
                folder_alias, folder_scope = folder_info(
                    folder_path, folder_system, root
                )
                if reason == "missing_on_disk" and os.path.exists(file_path):
                    reason = "not_in_current_scan"
                status = (
                    reason
                    if reason in ("missing_on_disk", "not_in_current_scan")
                    else "stored"
                    if records
                    else "container"
                    if reason == "archive_contents"
                    else "collapsed"
                    if collapsed and predicted
                    else "not_imported"
                    if predicted
                    else "skipped"
                )
                entry = {
                    "id": sequence,
                    "kind": kind,
                    "extension": extension
                    if details or extension in public_extensions
                    else "other",
                    "predicted_system": labels.get(predicted.pk) if predicted else None,
                    "reason": reason,
                    "folder_system": labels.get(folder_system.pk)
                    if folder_system
                    else None,
                    "folder_alias": folder_alias,
                    "folder_scope": folder_scope,
                    "status": status,
                }
                if records:
                    entry["stored_system"] = [
                        labels.get(row["rom_set__game__system_id"]) for row in records
                    ]
                    entry["name_source"] = [
                        row["rom_set__game__name_source"]
                        if row["rom_set__game__name_source"] in allowed_sources
                        else "other"
                        for row in records
                    ]
                    entry["metadata_match_failed"] = any(
                        row["rom_set__game__metadata_match_failed"] for row in records
                    )
                    entry["system_mismatch"] = reason not in (
                        "missing_on_disk",
                        "not_in_current_scan",
                    ) and any(
                        row["rom_set__game__system_id"]
                        != (predicted.pk if predicted else None)
                        for row in records
                    )
                if details:
                    entry["relative_path"] = os.path.relpath(file_path, root) + (
                        "!" + member if member else ""
                    )
                    if records:
                        entry["game_names"] = [
                            row["rom_set__game__name"] for row in records
                        ]
                        entry["screenscraper_ids"] = [
                            row["rom_set__game__screenscraper_id"] for row in records
                        ]
                        entry["crc32"] = [row["crc32"] for row in records]
                        entry["sha1"] = [row["sha1"] for row in records]
                    elif crc32:
                        entry["crc32"] = crc32
                if details:
                    if not first:
                        self.stdout.write(",", ending="")
                    dump(entry)
                    first = False
                else:
                    entry.pop("id")
                    key = json.dumps(entry, sort_keys=True, separators=(",", ":"))
                    if key in samples:
                        samples[key]["count"] += 1
                    else:
                        entry["count"] = 1
                        samples[key] = entry
                summary[status] += 1
                if predicted:
                    predicted_counts[labels[predicted.pk]] += 1
                for row in records:
                    stored_counts[labels[row["rom_set__game__system_id"]]] += 1
                if entry.get("system_mismatch"):
                    summary["system_mismatch"] += 1

            if os.path.isdir(root):

                def walk_error(_error):
                    summary["walk_errors"] += 1

                for directory, _subdirs, filenames in os.walk(root, onerror=walk_error):
                    for filename in filenames:
                        file_path = os.path.join(directory, filename)
                        extension = get_full_extension(filename)
                        if not extension:
                            emit(file_path, "", None, "no_extension")
                        elif is_bios_file(filename, file_path):
                            emit(file_path, extension, None, "bios")
                        elif (
                            extension in IMAGE_EXTENSIONS
                            and not is_compound_rom_extension(filename)
                        ):
                            emit(file_path, extension, None, "image")
                        elif extension in COMPRESSED_EXTENSIONS:
                            system, reason = detect_system_details(
                                file_path, systems, exclusive_map
                            )
                            if system and system.archive_as_rom:
                                emit(
                                    file_path,
                                    extension,
                                    system,
                                    "archive_as_rom",
                                    kind="archive",
                                )
                                continue
                            try:
                                contents = archive.list_archive_contents(file_path)
                            except Exception:
                                emit(
                                    file_path,
                                    extension,
                                    None,
                                    "archive_read_error",
                                    kind="archive",
                                )
                                continue
                            emit(
                                file_path,
                                extension,
                                None,
                                "archive_contents",
                                kind="archive",
                            )
                            classified = [
                                (item, None, "nested_archive")
                                if archive.is_nested_archive(item.name)
                                else (
                                    item,
                                    *detect_system_details(
                                        file_path, systems, exclusive_map, item.name
                                    ),
                                )
                                for item in contents
                            ]
                            valid = [
                                item
                                for item, item_system, _ in classified
                                if item_system
                            ]
                            try:
                                expanded = (
                                    should_expand_archive(valid) if valid else False
                                )
                            except Exception:
                                expanded = True
                                summary["archive_parse_errors"] += 1
                            first_valid = next(
                                (
                                    item.name
                                    for item, item_system, _ in classified
                                    if item_system
                                ),
                                None,
                            )
                            for item, item_system, item_reason in classified:
                                emit(
                                    file_path,
                                    get_full_extension(item.name),
                                    item_system,
                                    item_reason,
                                    kind="member",
                                    member=item.name,
                                    collapsed=not expanded and item.name != first_valid,
                                    crc32=item.crc32,
                                )
                        else:
                            system, reason = detect_system_details(
                                file_path, systems, exclusive_map
                            )
                            emit(file_path, extension, system, reason)

            for key in list(stored):
                row = stored[key][0]
                file_path = row["archive_path"] or row["file_path"]
                member = row["path_in_archive"]
                emit(
                    file_path,
                    get_full_extension(Path(member or file_path).name),
                    None,
                    "missing_on_disk",
                    kind="member" if member else "file",
                    member=member,
                )
            if not details:
                for entry in samples.values():
                    if not first:
                        self.stdout.write(",", ending="")
                    dump(entry)
                    first = False
            self.stdout.write("]}", ending="")

        self.stdout.write('],"summary":', ending="")
        dump(
            dict(summary)
            | {
                "predicted_systems": dict(predicted_counts),
                "stored_systems": dict(stored_counts),
            }
        )
        self.stdout.write("}")
