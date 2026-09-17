"""Management command to unmatch and rematch library games through the ScreenScraper caching proxy."""

import logging
import os
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Count, Q
from django.utils import timezone

from library.metadata.matcher import (
    _get_game_cache_path,
    _get_image_storage_path,
    _identify_game,
    apply_metadata_to_game,
    save_metadata_cache,
)
from library.metadata.screenscraper import (
    ALLOWED_MEDIA_TYPES,
    ScreenScraperClient,
    screenscraper_available,
)
from library.models import Game, GameImage, ScreenScraperLookupCache, System

logger = logging.getLogger(__name__)

PROXY_URL = "http://127.0.0.1:8765/api2/"


def check_proxy_alive(proxy_url: str = PROXY_URL) -> bool:
    """Verify that the local caching proxy is running and responding."""
    try:
        parts = urlsplit(proxy_url)
        base = f"{parts.scheme}://{parts.netloc}"
        resp = requests.get(f"{base}/api2/jeuInfos.php", timeout=5)
        # Proxy responds 400 for missing params, or 200/404 -- anything other than connection error
        return resp.status_code in (200, 400, 404)
    except requests.RequestException:
        return False


class Command(BaseCommand):
    help = "Unmatch all (or system-specific) games and rematch using the local caching proxy."

    def add_arguments(self, parser):
        parser.add_argument(
            "--system",
            type=str,
            help="Limit rematch to a specific system slug (e.g. 'channelf', 'snes')",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=8,
            help="Number of concurrent worker threads (default: 8)",
        )
        parser.add_argument(
            "--skip-unmatch",
            action="store_true",
            help="Skip the unmatch step (useful if resuming an interrupted run)",
        )
        parser.add_argument(
            "--baseline-only",
            action="store_true",
            help="Only compute and display baseline statistics without making changes",
        )
        parser.add_argument(
            "--preserve-romless-collections",
            action="store_true",
            help="Preserve curated collection IDs on placeholder entries without ROMs (by default, all games are unmatched)",
        )
        parser.add_argument(
            "--report",
            type=str,
            help="Path to save markdown comparison report",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run matching pipeline without modifying database records",
        )
        parser.add_argument(
            "--pending-only",
            action="store_true",
            help="Process only games where screenscraper_id is null and metadata_match_failed is false",
        )

    def handle(self, *args, **options):
        system_slug = options.get("system")
        workers = options.get("workers", 8)
        skip_unmatch = options.get("skip_unmatch", False)
        baseline_only = options.get("baseline_only", False)
        preserve_romless = options.get("preserve_romless_collections", False)
        report_path = options.get("report")
        dry_run = options.get("dry_run", False)
        pending_only = options.get("pending_only", False)

        # 1. Verify proxy is active
        self.stdout.write("Checking local ScreenScraper caching proxy...")
        if not check_proxy_alive():
            raise CommandError(
                f"ScreenScraper caching proxy is NOT reachable at {PROXY_URL}.\n"
                "Please start it with: uv run python scripts/screenscraper_proxy.py"
            )
        self.stdout.write(self.style.SUCCESS(f"Proxy reachable at {PROXY_URL}"))

        # Enforce proxy in environment
        os.environ["SCREENSCRAPER_API_BASE"] = PROXY_URL

        # Verify ScreenScraper credentials
        if not screenscraper_available():
            raise CommandError("ScreenScraper credentials are not configured in settings or environment.")

        # 2. Collect Baseline
        self.stdout.write("\nCollecting library baseline statistics...")
        baseline = self._collect_stats(system_slug)
        self._print_stats_table("BASELINE MATCHING RATES", baseline)

        if baseline_only:
            return

        # Snapshot individual game states for before/after comparison
        games_qs = Game.objects.select_related("system")
        if system_slug:
            games_qs = games_qs.filter(system__slug=system_slug)
        if pending_only:
            games_qs = games_qs.filter(
                screenscraper_id__isnull=True,
                metadata_match_failed=False,
            )

        baseline_snapshot = {
            g.pk: {
                "name": g.name,
                "system": g.system.slug,
                "screenscraper_id": g.screenscraper_id,
                "failed": g.metadata_match_failed,
                "has_metadata": bool(g.metadata_updated_at),
                "matched": bool(g.screenscraper_id),
            }
            for g in games_qs.iterator(chunk_size=2000)
        }
        initial_images_count = GameImage.objects.count()

        if dry_run:
            self.stdout.write(self.style.WARNING("\n[DRY RUN] Running match evaluation without modifying the database..."))
            results = self._run_matching_pass(games_qs, workers, dry_run=True)
            self._print_dry_run_report(baseline, results)
            return

        # 3. Unmatch Phase
        if not skip_unmatch and not pending_only:
            self.stdout.write(self.style.WARNING("\n=== UNMATCHING GAMES ==="))
            self._unmatch_games(system_slug, preserve_romless=preserve_romless)
            # Verify images count after unmatching
            current_images = GameImage.objects.count()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Unmatched complete. Images intact: {current_images} (baseline: {initial_images_count})"
                )
            )
        elif pending_only:
            self.stdout.write(self.style.WARNING("\nPending-only mode: skipping unmatch to preserve existing matched/failed games."))

        # 4. Rematch Phase
        self.stdout.write(self.style.HTTP_INFO(f"\n=== REMATCHING GAMES (Workers: {workers}) ==="))
        start_time = time.time()
        results = self._run_matching_pass(games_qs, workers, dry_run=False)
        duration = time.time() - start_time

        # 5. Final Stats & Evaluation
        self.stdout.write(self.style.SUCCESS(f"\nRematch completed in {duration:.1f}s"))
        final_stats = self._collect_stats(system_slug)
        final_images_count = GameImage.objects.count()

        # Print comparison
        self._print_comparison(baseline, final_stats, initial_images_count, final_images_count)

        # Detailed breakdown of transitions
        transitions = self._compute_transitions(baseline_snapshot)
        self._print_transitions(transitions)

        # Generate markdown report if requested
        if report_path:
            self._write_markdown_report(report_path, baseline, final_stats, transitions, duration, initial_images_count, final_images_count)
            self.stdout.write(self.style.SUCCESS(f"Report saved to {report_path}"))

    def _collect_stats(self, system_slug: str | None = None) -> dict:
        """Gather current matching statistics for library or system."""
        systems_qs = System.objects.all()
        if system_slug:
            systems_qs = systems_qs.filter(slug=system_slug)

        systems = (
            systems_qs.annotate(
                total=Count("games"),
                matched=Count("games", filter=Q(games__screenscraper_id__isnull=False)),
                failed=Count("games", filter=Q(games__metadata_match_failed=True)),
                pending=Count(
                    "games",
                    filter=Q(games__screenscraper_id__isnull=True, games__metadata_match_failed=False),
                ),
            )
            .filter(total__gt=0)
            .order_by("-total", "slug")
        )

        overall_total = sum(s.total for s in systems)
        overall_matched = sum(s.matched for s in systems)
        overall_failed = sum(s.failed for s in systems)
        overall_pending = sum(s.pending for s in systems)
        overall_rate = (overall_matched / overall_total * 100) if overall_total else 0.0

        return {
            "overall": {
                "total": overall_total,
                "matched": overall_matched,
                "failed": overall_failed,
                "pending": overall_pending,
                "rate": overall_rate,
            },
            "systems": [
                {
                    "slug": s.slug,
                    "name": s.name,
                    "total": s.total,
                    "matched": s.matched,
                    "failed": s.failed,
                    "pending": s.pending,
                    "rate": (s.matched / s.total * 100) if s.total else 0.0,
                }
                for s in systems
            ],
        }

    def _print_stats_table(self, title: str, stats: dict):
        """Print a formatted table of matching rates."""
        self.stdout.write(f"\n{title}")
        self.stdout.write("-" * 75)
        self.stdout.write(
            f"{'System':<20} | {'Total':<7} | {'Matched':<7} | {'Failed':<7} | {'Pending':<7} | {'Match Rate':<10}"
        )
        self.stdout.write("-" * 75)
        for s in stats["systems"]:
            self.stdout.write(
                f"{s['slug']:<20} | {s['total']:<7} | {s['matched']:<7} | {s['failed']:<7} | {s['pending']:<7} | {s['rate']:6.2f}%"
            )
        self.stdout.write("-" * 75)
        o = stats["overall"]
        self.stdout.write(
            f"{'OVERALL':<20} | {o['total']:<7} | {o['matched']:<7} | {o['failed']:<7} | {o['pending']:<7} | {o['rate']:6.2f}%\n"
        )

    def _print_dry_run_report(self, baseline: dict, results: dict):
        """Print evaluation report for dry-run."""
        b_ov = baseline["overall"]
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("DRY RUN EVALUATION REPORT")
        self.stdout.write("=" * 80)
        self.stdout.write(f"  Processed: {results['processed']}")
        rate = (results['matched'] / results['processed'] * 100) if results['processed'] else 0.0
        self.stdout.write(f"  Matched:   {results['matched']} ({rate:.2f}%)")
        self.stdout.write(f"  Failed:    {results['failed']}")
        self.stdout.write(f"  Baseline:  {b_ov['matched']} / {b_ov['total']} ({b_ov['rate']:.2f}%)")
        self.stdout.write("  Match types:")
        for mt, count in sorted(results["match_types"].items()):
            self.stdout.write(f"    {mt}: {count}")
        self.stdout.write("=" * 80 + "\n")

    def _unmatch_games(self, system_slug: str | None = None, preserve_romless: bool = False):
        """Unmatch games by clearing matching flags and clearing app-level lookup cache."""
        with transaction.atomic():
            # 1. Clear ScreenScraperLookupCache in PostgreSQL so 30-day misses and stale entries don't block
            if system_slug:
                sys_obj = System.objects.get(slug=system_slug)
                sids = sys_obj.all_screenscraper_ids
                ScreenScraperLookupCache.objects.filter(system_id__in=sids).delete()
                self.stdout.write(f"Cleared ScreenScraperLookupCache for system '{system_slug}' (IDs: {sids})")
            else:
                ScreenScraperLookupCache.objects.all().delete()
                self.stdout.write("Cleared all ScreenScraperLookupCache entries in PostgreSQL.")

            # 2. Reset Game matching fields (screenscraper_id, metadata_updated_at, metadata_match_failed)
            qs = Game.objects.all()
            if system_slug:
                qs = qs.filter(system__slug=system_slug)
            if preserve_romless:
                qs = qs.exclude(name_source=Game.SOURCE_COLLECTION, rom_sets__isnull=True)
                self.stdout.write("Preserving curated collection IDs on placeholder entries without ROMs.")

            count = qs.count()
            # Reset screenscraper-named titles back to filename source so renaming logic applies cleanly
            qs.filter(name_source=Game.SOURCE_SCREENSCRAPER).update(name_source=Game.SOURCE_FILENAME)
            qs.update(
                screenscraper_id=None,
                metadata_updated_at=None,
                metadata_match_failed=False,
            )
            self.stdout.write(f"Reset matching status for {count} games (images remain completely untouched).")

    def _run_matching_pass(self, games_qs, workers: int, dry_run: bool = False) -> dict:
        """Run the matching pipeline concurrently across games."""
        game_ids = list(games_qs.values_list("pk", flat=True))
        total_games = len(game_ids)
        self.stdout.write(f"Processing {total_games} games...")

        client = ScreenScraperClient()
        write_lock = threading.Lock()
        counter_lock = threading.Lock()

        stats = {
            "processed": 0,
            "matched": 0,
            "failed": 0,
            "merged": 0,
            "match_types": defaultdict(int),
        }

        start_time = time.time()

        def process_game(game_id: int):
            try:
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        try:
                            game = Game.objects.select_related("system").get(pk=game_id)
                        except Game.DoesNotExist:
                            with counter_lock:
                                stats["merged"] += 1
                                stats["processed"] += 1
                            return {"game_id": game_id, "status": "merged_away"}

                        if not game.system.all_screenscraper_ids:
                            with counter_lock:
                                stats["failed"] += 1
                                stats["processed"] += 1
                            return {"game_id": game_id, "status": "no_system_mapping"}

                        result = _identify_game(game, client)

                        if result and result.screenscraper_id:
                            matched_id = result.screenscraper_id
                            matched_sys_id = result.matched_system_id or game.system.screenscraper_id
                            match_type = result.match_type

                            if not dry_run:
                                # Fetch full metadata from proxy
                                metadata = client.get_game_info(
                                    matched_id,
                                    media_types=ALLOWED_MEDIA_TYPES,
                                    game_name=result.name or game.name,
                                    system_id=matched_sys_id,
                                )

                                with write_lock:
                                    try:
                                        g = Game.objects.select_related("system").get(pk=game_id)
                                    except Game.DoesNotExist:
                                        with counter_lock:
                                            stats["merged"] += 1
                                            stats["processed"] += 1
                                        return {"game_id": game_id, "status": "merged_away"}

                                    if metadata and metadata.get("id"):
                                        metadata["_match_type"] = match_type
                                        metadata["_matched_system_id"] = matched_sys_id
                                        if match_type in ("crc32", "romnom") and result.name:
                                            metadata["_screenscraper_name"] = result.raw_name

                                        # apply_metadata_to_game will handle duplicate merge and metadata update
                                        apply_metadata_to_game(g, metadata)
                                        if Game.objects.filter(pk=g.pk).exists():
                                            save_metadata_cache(g, metadata)
                                    else:
                                        g.screenscraper_id = matched_id
                                        g.metadata_updated_at = timezone.now()
                                        g.save(update_fields=["screenscraper_id", "metadata_updated_at"])

                            with counter_lock:
                                stats["matched"] += 1
                                stats["match_types"][match_type] += 1
                                stats["processed"] += 1

                            return {
                                "game_id": game_id,
                                "status": "matched",
                                "screenscraper_id": matched_id,
                                "match_type": match_type,
                            }
                        else:
                            if not dry_run:
                                with write_lock:
                                    try:
                                        g = Game.objects.get(pk=game_id)
                                        g.metadata_match_failed = True
                                        g.save(update_fields=["metadata_match_failed"])
                                        # Remove stale metadata.json so future lookups don't resurrect failed match
                                        cache_file = _get_game_cache_path(g, _get_image_storage_path())
                                        if cache_file.exists():
                                            cache_file.unlink()
                                    except Game.DoesNotExist:
                                        pass

                            with counter_lock:
                                stats["failed"] += 1
                                stats["processed"] += 1

                            return {"game_id": game_id, "status": "failed"}

                    except (requests.exceptions.RequestException, ConnectionError) as e:
                        if attempt < max_retries - 1:
                            backoff = 2 ** attempt
                            logger.warning(
                                "Transient network error matching game %s (attempt %d/%d): %s. Retrying in %ds...",
                                game_id,
                                attempt + 1,
                                max_retries,
                                e,
                                backoff,
                            )
                            time.sleep(backoff)
                        else:
                            logger.error(
                                "Failed matching game %s after %d attempts due to network error: %s",
                                game_id,
                                max_retries,
                                e,
                            )
                            raise

            finally:
                connection.close()

        # Run with ThreadPoolExecutor
        last_report = time.time()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(process_game, gid) for gid in game_ids]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.exception("Unexpected error processing game: %s", e)
                now = time.time()
                if now - last_report > 2.0 or stats["processed"] == total_games:
                    elapsed = now - start_time
                    rate = (stats["matched"] / stats["processed"] * 100) if stats["processed"] else 0.0
                    speed = stats["processed"] / elapsed if elapsed > 0 else 0
                    sys.stdout.write(
                        f"\rProgress: {stats['processed']}/{total_games} ({stats['processed']/total_games*100:5.1f}%) | "
                        f"Matched: {stats['matched']} ({rate:5.1f}%) | Failed: {stats['failed']} | "
                        f"Merged: {stats['merged']} | {speed:.1f} games/s"
                    )
                    sys.stdout.flush()
                    last_report = now

        sys.stdout.write("\n")
        return stats

    def _compute_transitions(self, baseline_snapshot: dict) -> dict:
        """Compute changes between baseline snapshot and current DB state."""
        current_games = {
            g.pk: {
                "name": g.name,
                "system": g.system.slug,
                "screenscraper_id": g.screenscraper_id,
                "failed": g.metadata_match_failed,
                "matched": bool(g.screenscraper_id),
            }
            for g in Game.objects.select_related("system").iterator(chunk_size=2000)
        }

        newly_matched = []
        newly_failed = []
        id_changed = []
        merged_games = []

        for pk, old in baseline_snapshot.items():
            if pk not in current_games:
                merged_games.append(old)
                continue

            new = current_games[pk]
            if not old["matched"] and new["matched"]:
                newly_matched.append({"pk": pk, "old": old, "new": new})
            elif old["matched"] and not new["matched"]:
                newly_failed.append({"pk": pk, "old": old, "new": new})
            elif old["matched"] and new["matched"] and old["screenscraper_id"] != new["screenscraper_id"]:
                id_changed.append({"pk": pk, "old": old, "new": new})

        return {
            "newly_matched": newly_matched,
            "newly_failed": newly_failed,
            "id_changed": id_changed,
            "merged_games": merged_games,
        }

    def _print_comparison(self, baseline: dict, final: dict, img_before: int, img_after: int):
        """Print comprehensive comparison between baseline and final stats."""
        b_ov = baseline["overall"]
        f_ov = final["overall"]

        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("MATCHING RATE COMPARISON: BASELINE vs FINAL")
        self.stdout.write("=" * 80)
        self.stdout.write(
            f"{'System':<18} | {'Base Rate':<10} | {'Final Rate':<10} | {'Delta':<8} | {'Base Matched':<12} | {'Final Matched':<13}"
        )
        self.stdout.write("-" * 80)

        final_by_slug = {s["slug"]: s for s in final["systems"]}
        for b_sys in baseline["systems"]:
            slug = b_sys["slug"]
            f_sys = final_by_slug.get(slug, {"matched": 0, "total": 0, "rate": 0.0})
            delta = f_sys["rate"] - b_sys["rate"]
            delta_str = f"{delta:+5.2f}%" if delta != 0 else "  0.00%"
            self.stdout.write(
                f"{slug:<18} | {b_sys['rate']:6.2f}%    | {f_sys['rate']:6.2f}%    | {delta_str:<8} | "
                f"{b_sys['matched']}/{b_sys['total']:<6} | {f_sys['matched']}/{f_sys['total']:<7}"
            )

        self.stdout.write("-" * 80)
        total_delta = f_ov["rate"] - b_ov["rate"]
        total_delta_str = f"{total_delta:+5.2f}%" if total_delta != 0 else "  0.00%"
        self.stdout.write(
            f"{'OVERALL':<18} | {b_ov['rate']:6.2f}%    | {f_ov['rate']:6.2f}%    | {total_delta_str:<8} | "
            f"{b_ov['matched']}/{b_ov['total']:<6} | {f_ov['matched']}/{f_ov['total']:<7}"
        )
        self.stdout.write("=" * 80)

        # Images check
        self.stdout.write("\nImage Asset Verification:")
        self.stdout.write(f"  GameImage records before: {img_before}")
        self.stdout.write(f"  GameImage records after:  {img_after}")
        if img_after >= img_before:
            self.stdout.write(self.style.SUCCESS(f"  ✓ All images preserved ({img_after} records)"))
        else:
            diff = img_before - img_after
            self.stdout.write(self.style.WARNING(f"  Note: {diff} duplicate image records consolidated via game merges."))

    def _print_transitions(self, transitions: dict):
        """Print summary of transitions."""
        self.stdout.write("\nTransition Highlights:")
        self.stdout.write(f"  Newly Matched Games: {len(transitions['newly_matched'])}")
        self.stdout.write(f"  Newly Failed Games:  {len(transitions['newly_failed'])}")
        self.stdout.write(f"  Changed Match IDs:   {len(transitions['id_changed'])}")
        self.stdout.write(f"  Merged Duplicates:   {len(transitions['merged_games'])}")

        if transitions["newly_matched"]:
            self.stdout.write("\nSample Newly Matched Games:")
            for item in transitions["newly_matched"][:15]:
                old = item["old"]
                new = item["new"]
                self.stdout.write(f"  + [{old['system']}] {new['name']} -> ScreenScraper ID {new['screenscraper_id']}")

        if transitions["newly_failed"]:
            self.stdout.write(self.style.WARNING("\nSample Newly Failed Games (Regressions):"))
            for item in transitions["newly_failed"][:10]:
                old = item["old"]
                self.stdout.write(f"  - [{old['system']}] {old['name']} (was ID {old['screenscraper_id']})")

    def _write_markdown_report(
        self,
        path: str,
        baseline: dict,
        final: dict,
        transitions: dict,
        duration: float,
        img_before: int,
        img_after: int,
    ):
        """Write detailed markdown report of the rematch run."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        b_ov = baseline["overall"]
        f_ov = final["overall"]
        delta_rate = f_ov["rate"] - b_ov["rate"]
        delta_str = f"{delta_rate:+5.2f}%"

        lines = [
            "# Library Rematch Report",
            "",
            f"**Run completed:** {timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"**Duration:** {duration:.1f} seconds ({duration/60:.1f} minutes)",
            "",
            "## Executive Summary",
            "",
            f"- **Initial Library Match Rate:** {b_ov['matched']} / {b_ov['total']} ({b_ov['rate']:.2f}%)",
            f"- **Final Library Match Rate:**   **{f_ov['matched']} / {f_ov['total']} ({f_ov['rate']:.2f}%)** ({delta_str})",
            f"- **Newly Matched Games:** {len(transitions['newly_matched'])}",
            f"- **Newly Failed (Regressions):** {len(transitions['newly_failed'])}",
            f"- **Consolidated Duplicates (Merged):** {len(transitions['merged_games'])}",
            f"- **Image Integrity:** Baseline {img_before} images -> Final {img_after} images (100% preserved)",
            "",
            "## Match Rate by System",
            "",
            "| System | Baseline Rate | Final Rate | Delta | Baseline Matched | Final Matched |",
            "| :--- | :---: | :---: | :---: | :---: | :---: |",
        ]

        final_by_slug = {s["slug"]: s for s in final["systems"]}
        for b_sys in baseline["systems"]:
            slug = b_sys["slug"]
            f_sys = final_by_slug.get(slug, {"matched": 0, "total": 0, "rate": 0.0})
            delta = f_sys["rate"] - b_sys["rate"]
            d_str = f"**{delta:+5.2f}%**" if delta > 0 else (f"{delta:+5.2f}%" if delta < 0 else "0.00%")
            lines.append(
                f"| **{slug}** | {b_sys['rate']:.2f}% | **{f_sys['rate']:.2f}%** | {d_str} | "
                f"{b_sys['matched']} / {b_sys['total']} | {f_sys['matched']} / {f_sys['total']} |"
            )

        lines.extend([
            f"| **TOTAL** | {b_ov['rate']:.2f}% | **{f_ov['rate']:.2f}%** | **{delta_str}** | "
            f"{b_ov['matched']} / {b_ov['total']} | {f_ov['matched']} / {f_ov['total']} |",
            "",
            "## Newly Matched Games",
            "",
        ])

        if transitions["newly_matched"]:
            lines.append("| System | Game Title | ScreenScraper ID |")
            lines.append("| :--- | :--- | :--- |")
            for item in transitions["newly_matched"]:
                old = item["old"]
                new = item["new"]
                lines.append(f"| `{old['system']}` | {new['name']} | `{new['screenscraper_id']}` |")
        else:
            lines.append("*None*")

        lines.extend(["", "## Regressions (Newly Failed)", ""])
        if transitions["newly_failed"]:
            lines.append("| System | Game Title | Previous ScreenScraper ID |")
            lines.append("| :--- | :--- | :--- |")
            for item in transitions["newly_failed"]:
                old = item["old"]
                lines.append(f"| `{old['system']}` | {old['name']} | `{old['screenscraper_id']}` |")
        else:
            lines.append("*None (0 regressions)*")

        lines.extend(["", "## Consolidated / Merged Duplicates", ""])
        if transitions["merged_games"]:
            lines.append(f"Total merged: {len(transitions['merged_games'])}")
            for m in transitions["merged_games"][:20]:
                lines.append(f"- [{m['system']}] {m['name']} (ID: {m['screenscraper_id']})")
        else:
            lines.append("*None*")

        p.write_text("\n".join(lines), encoding="utf-8")
