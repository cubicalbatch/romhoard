"""Unit tests for ScreenScraper search query variant generation and ROM filename fallback."""

import pytest
from unittest.mock import MagicMock, patch

from library.metadata.screenscraper import _get_search_variants
from library.lookup.screenscraper import (
    ScreenScraperLookupService,
    _extract_rom_stem,
)


class TestSearchQueryVariants:
    """Tests for _get_search_variants in library/metadata/screenscraper.py."""

    # a) Date prefix removal
    def test_date_prefix_removal_excitebike(self):
        """Strip ISO dates at the start."""
        variants = _get_search_variants("1984-11-30 Excitebike")
        assert "Excitebike" in variants

    def test_date_prefix_removal_portopia(self):
        """Strip ISO date prefix preserving remainder and normalized forms."""
        variants = _get_search_variants("1985-11-29 The Portopia Serial Murder Incident")
        assert "The Portopia Serial Murder Incident" in variants
        assert "Portopia Serial Murder Incident" in variants

    def test_date_prefix_non_date_not_stripped(self):
        """Titles starting with a 4-digit number like 1942 are not stripped."""
        variants = _get_search_variants("1942 Joint Strike")
        assert "1942 Joint Strike" in variants
        assert "Joint Strike" not in variants

    # b) Numbered rank prefix removal
    def test_numbered_rank_prefix_removal(self):
        """Strip leading 2- or 3-digit rank numbers when followed by letters."""
        variants = _get_search_variants("089 Ice Climber")
        assert "Ice Climber" in variants

    def test_numbered_rank_prefix_two_digits(self):
        """Strip 2-digit rank prefix."""
        variants = _get_search_variants("01 Super Mario Bros")
        assert "Super Mario Bros" in variants

    def test_numbered_rank_prefix_preserves_titles_like_10_yard_fight(self):
        """Titles like 10-Yard Fight are not stripped because no space followed by letters."""
        variants = _get_search_variants("10-Yard Fight")
        assert "10-Yard Fight" in variants

    # c) Hardware / revision prefixes
    def test_hardware_prefix_2c03(self):
        """Strip VS System PPU 2C03 prefix."""
        variants = _get_search_variants("2C03 Pinball")
        assert "Pinball" in variants

    def test_hardware_prefix_2c04_with_subrevision(self):
        """Strip VS System PPU 2C04-01 prefix."""
        variants = _get_search_variants("2C04-01 Gradius")
        assert "Gradius" in variants

    def test_hardware_prefix_case_insensitive(self):
        """Strip hardware prefix case-insensitively."""
        variants = _get_search_variants("2c03 Pinball")
        assert "Pinball" in variants

    # d) VS. arcade prefix
    def test_vs_arcade_prefix_duck_hunt(self):
        """Strip leading VS. prefix."""
        variants = _get_search_variants("VS. Duck Hunt")
        assert "Duck Hunt" in variants

    def test_vs_arcade_prefix_goonies(self):
        """Strip leading VS. prefix for Goonies."""
        variants = _get_search_variants("VS. Goonies")
        assert "Goonies" in variants

    def test_vs_arcade_prefix_case_insensitive(self):
        """Strip leading Vs. prefix case-insensitively."""
        variants = _get_search_variants("Vs. Castlevania")
        assert "Castlevania" in variants

    # e) Inverted articles
    def test_inverted_article_the(self):
        """Handle titles ending in ', The'."""
        variants = _get_search_variants("Berenstain Bears' Camping Adventure, The")
        assert "The Berenstain Bears' Camping Adventure" in variants
        assert "Berenstain Bears' Camping Adventure" in variants

    def test_inverted_article_a(self):
        """Handle titles ending in ', A'."""
        variants = _get_search_variants("Boy and His Blob, A")
        assert "A Boy and His Blob" in variants
        assert "Boy and His Blob" in variants

    def test_inverted_article_an(self):
        """Handle titles ending in ', An'."""
        variants = _get_search_variants("Awesome Game, An")
        assert "An Awesome Game" in variants
        assert "Awesome Game" in variants

    def test_inverted_article_with_subtitle(self):
        """Handle titles ending in ', The' with following subtitle."""
        variants = _get_search_variants("Hobbit, The - An Unexpected Journey")
        assert "The Hobbit - An Unexpected Journey" in variants
        assert "Hobbit - An Unexpected Journey" in variants

    # f) Quality-of-Life Patch / Edition / Mod suffixes
    @pytest.mark.parametrize(
        "title,expected_clean",
        [
            ("ActRaiser PAL-to-NTSC Patched", "ActRaiser"),
            ("Aladdin PAL-to-NTSC Patched", "Aladdin"),
            ("Aladdin PAL-to-NTSC 60Hz Patched", "Aladdin"),
            ("Game PAL-to-NTSC 60Hz", "Game"),
            ("Game PAL-to-NTSC", "Game"),
            ("Game 60Hz Patched", "Game"),
            ("Aladdin Trained", "Aladdin"),
            ("Aladdin Trainer", "Aladdin"),
            ("Aladdin +Trainer", "Aladdin"),
            ("Game Save Patched", "Game"),
            ("Game Savepatch", "Game"),
            ("Game Save Patch", "Game"),
            ("Game Bug-Fixed", "Game"),
            ("Game Bugfix", "Game"),
            ("Game Fixed", "Game"),
            ("Game Fix", "Game"),
            ("Bubble Bobble Improvement", "Bubble Bobble"),
            ("Miracle Warriors Improvement", "Miracle Warriors"),
            ("Secret Commando Speed-Up", "Secret Commando"),
            ("Game Enhanced", "Game"),
            ("Game Uncensored", "Game"),
            ("Golvellius PTBR+Save", "Golvellius"),
            ("Golvellius PTBR+Save+Bugfixes", "Golvellius"),
            ("Game MSX2SMS Hack", "Game"),
            ("Game NES2PCE", "Game"),
        ],
    )
    def test_qol_patch_suffixes(self, title, expected_clean):
        """Strip retail patch and QoL mod suffixes."""
        variants = _get_search_variants(title)
        assert expected_clean in variants

    # g) Unbracketed Version and Author suffixes
    @pytest.mark.parametrize(
        "title,expected_clean",
        [
            ("Batter Up v0.1 Revo", "Batter Up"),
            ("Arch Rivals v0.1 Revo", "Arch Rivals"),
            ("Battletoads b1 nextvolume", "Battletoads"),
            ("Beavis and Butt-head v0.1 Revo", "Beavis and Butt-head"),
            ("Alien 1.02 Final", "Alien"),
            ("Anguna - the Prison Dungeon v0.07a", "Anguna - the Prison Dungeon"),
        ],
    )
    def test_unbracketed_version_and_author_suffixes(self, title, expected_clean):
        """Strip unbracketed version and author suffixes."""
        variants = _get_search_variants(title)
        assert expected_clean in variants

    # h) Trailing "Hack" suffix
    def test_trailing_hack_suffix(self):
        """Strip trailing 'Hack' suffix."""
        variants = _get_search_variants("Aero Fighters Hack")
        assert "Aero Fighters" in variants

    def test_trailing_hacks_suffix(self):
        """Strip trailing 'Hacks' suffix."""
        variants = _get_search_variants("Aero Fighters Hacks")
        assert "Aero Fighters" in variants

    # Combined prefix + suffix
    def test_combined_prefix_and_suffix(self):
        """Handle titles with both a prefix and a suffix."""
        variants = _get_search_variants("VS. Duck Hunt Hack")
        assert "Duck Hunt" in variants

    def test_priority_order_clean_variant_high_priority(self):
        """Clean stripped variant is high priority (near top of list)."""
        variants = _get_search_variants("089 Ice Climber")
        # "089 Ice Climber" is base (index 0), clean "Ice Climber" should be index 1
        assert variants[0] == "089 Ice Climber"
        assert variants[1] == "Ice Climber"

    def test_no_duplicate_variants(self):
        """Ensure variant list contains no duplicates."""
        variants = _get_search_variants("1984-11-30 Excitebike PAL-to-NTSC Patched")
        assert len(variants) == len(set(variants))

    def test_long_title_leading_words_never_single_word(self):
        """4+ word titles extract leading 2-3 words, never a single word."""
        variants_32x = _get_search_variants("32X Color by mic")
        assert "32X" not in variants_32x
        assert "32X Color" in variants_32x

        variants_sonic = _get_search_variants("Sonic The Hedgehog 32X Pure Port")
        assert "Sonic" not in variants_sonic
        assert "32X" not in variants_sonic
        assert "Sonic The Hedgehog" in variants_sonic

    def test_console_terms_never_added_as_variants(self):
        """Console terms and words < 3 chars are never added as variants."""
        assert _get_search_variants("32X") == []
        assert _get_search_variants("NES") == []
        assert _get_search_variants("SNES") == []
        assert _get_search_variants("Genesis") == []


class TestExtractRomStem:
    """Tests for _extract_rom_stem helper."""

    def test_loose_file(self):
        """Extract stem from loose file path."""
        stem = _extract_rom_stem("/roms/nes/Musashi no Bouken (Japan) (Translated En).nes")
        assert stem == "Musashi no Bouken"

    def test_archive_inner_file(self):
        """Extract stem from inner archive file."""
        stem = _extract_rom_stem(
            "/roms/nes/archive.zip!Musashi no Bouken (Japan) (Translated En).nes",
            archive_as_rom=False,
        )
        assert stem == "Musashi no Bouken"

    def test_arcade_archive_as_rom(self):
        """For arcade systems, extract stem from archive name."""
        stem = _extract_rom_stem(
            "/roms/arcade/pacman.zip!pacman/pacman.rom",
            archive_as_rom=True,
        )
        assert stem == "pacman"

    def test_rom_number_prefix_stripped_from_stem(self):
        """Extract stem with ROM number prefix stripped."""
        stem = _extract_rom_stem("/roms/snes/089 - Super Mario World (USA).sfc")
        assert stem == "Super Mario World"


@pytest.mark.django_db
class TestRomFilenameFallbackInLookup:
    """Tests for ROM filename fallback in ScreenScraperLookupService.lookup."""

    @pytest.fixture
    def mock_system(self):
        """Create a mock System."""
        system = MagicMock()
        system.slug = "nes"
        system.archive_as_rom = False
        system.all_screenscraper_ids = [3]
        return system

    def test_filename_fallback_when_game_name_fails(self, mock_system):
        """When game_name search fails, ROM filename stem is searched."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_by_crc.return_value = None
        mock_client.search_by_romnom.return_value = None

        # search_game returns nothing for "Adventures of Musashi",
        # but returns match for "Musashi no Bouken"
        def fake_search_game(query, system_id):
            if "Musashi no Bouken" in query:
                return [{"id": 18819, "name": "Musashi no Bouken: The Quest of Fighter's", "system_id": system_id}]
            return []

        mock_client.search_game.side_effect = fake_search_game

        with patch.object(service, "_get_client", return_value=mock_client):
            result = service.lookup(
                system=mock_system,
                crc32="12345678",
                file_path="/roms/nes/Musashi no Bouken (Japan) (Translated En).nes",
                game_name="Adventures of Musashi",
            )

        assert result is not None
        assert result.screenscraper_id == 18819
        assert result.name == "Musashi no Bouken: The Quest of Fighter's"

    def test_filename_fallback_skipped_when_stem_matches_game_name(self, mock_system):
        """When stem matches game_name, stem search is not repeated redundantly."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_by_crc.return_value = None
        mock_client.search_by_romnom.return_value = None
        mock_client.search_game.return_value = []

        with patch.object(service, "_get_client", return_value=mock_client):
            result = service.lookup(
                system=mock_system,
                crc32="",
                file_path="/roms/nes/Super Mario Bros (USA).nes",
                game_name="Super Mario Bros",
            )

        assert result is None
        # Should only search variants for "Super Mario Bros" once, not repeat for identical stem
        searched_queries = [call[0][0] for call in mock_client.search_game.call_args_list]
        assert searched_queries.count("Super Mario Bros") == 1

    def test_filename_fallback_when_game_name_is_empty(self, mock_system):
        """When game_name is empty, ROM filename stem is searched directly."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_by_crc.return_value = None
        mock_client.search_by_romnom.return_value = None
        mock_client.search_game.return_value = [
            {"id": 18819, "name": "Musashi no Bouken", "system_id": 3}
        ]

        with patch.object(service, "_get_client", return_value=mock_client):
            result = service.lookup(
                system=mock_system,
                crc32="",
                file_path="/roms/nes/Musashi no Bouken (Japan).nes",
                game_name="",
            )

        assert result is not None
        assert result.screenscraper_id == 18819
