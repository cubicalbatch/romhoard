"""Helpers for computing selection size estimates.

The authoritative per-file size is ``ROM.file_size`` (uncompressed bytes,
populated at scan time). Neither ``Game`` nor ``Collection`` stores an
aggregate size, so estimates are computed on demand by summing
``ROM.file_size`` across each game's ``default_rom_set``.

This matches the actual download's ROMSet choice in the common case
(``default_rom_set`` is set by the same scorer the bundler uses). It is
exact for loose files and a slight over-estimate for archived ROMs -- a
fine "estimate", and it keeps the lookup to a single SQL ``SUM``.
"""

from django.db import connection


def estimate_games_size(game_ids: list[int]) -> int:
    """Return total uncompressed bytes across each game's default ROMSet.

    Games without a default ROMSet (or without ROMs) contribute 0. Each
    game is counted once regardless of how many times its id appears.

    Args:
        game_ids: Game primary keys to size.

    Returns:
        Sum of ``ROM.file_size`` over the default ROMSet of each game.
    """
    if not game_ids:
        return 0
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COALESCE(SUM(r.file_size), 0)
            FROM library_rom r
            JOIN library_romset rs ON rs.id = r.rom_set_id
            JOIN library_game g ON g.default_rom_set_id = rs.id
            WHERE g.id = ANY(%s)
            """,
            [list(game_ids)],
        )
        row = cursor.fetchone()
    return int(row[0] or 0)
