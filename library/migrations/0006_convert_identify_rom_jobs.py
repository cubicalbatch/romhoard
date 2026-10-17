"""Replace stuck per-ROM `identify_rom` jobs with per-game `identify_game` jobs.

No worker ever consumed the `identification` queue, so installs accumulated
one `identify_rom` job per scanned ROM. Those games still deserve Hasheous
naming: each affected game whose name still comes from its filename gets one
low-priority `identify_game` job (Hasheous only; their metadata was already
queued by the scan).
"""

from django.db import migrations

CONVERT_JOBS = """
INSERT INTO procrastinate_jobs (queue_name, task_name, priority, args)
SELECT
    'identification',
    'library.tasks.identify_game',
    25,
    jsonb_build_object('game_id', g.id)
FROM library_game g
JOIN library_system s ON s.id = g.system_id
WHERE g.name_source = 'filename'
  AND NOT s.archive_as_rom
  AND g.id IN (
      SELECT rs.game_id
      FROM procrastinate_jobs j
      JOIN library_rom r ON r.id = (j.args ->> 'rom_id')::bigint
      JOIN library_romset rs ON rs.id = r.rom_set_id
      WHERE j.task_name = 'library.tasks.identify_rom' AND j.status = 'todo'
  )
ORDER BY g.id;

DELETE FROM procrastinate_jobs
WHERE task_name = 'library.tasks.identify_rom' AND status = 'todo';
"""


class Migration(migrations.Migration):
    dependencies = [
        ("library", "0005_remove_legacy_screenscraper_pause"),
        ("procrastinate", "0041_post_retry_failed_job"),
    ]

    operations = [migrations.RunSQL(CONVERT_JOBS, migrations.RunSQL.noop)]
