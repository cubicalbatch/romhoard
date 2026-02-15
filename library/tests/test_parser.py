"""Tests for ROM filename parser."""

from django.test import TestCase

from library.parser import parse_rom_filename


class TestROMNumberParsing(TestCase):
    """Test ROM number extraction from filenames."""

    def test_rom_number_with_prefix(self):
        """Test filenames with ROM number prefix."""
        # Standard case with spaces around dash
        result = parse_rom_filename("237 - Super Mario World.sfc")
        self.assertEqual(result["name"], "Super Mario World")
        self.assertEqual(result["rom_number"], "237")

        # Leading zeros should be preserved
        result = parse_rom_filename("0001 - Tetris.gb")
        self.assertEqual(result["name"], "Tetris")
        self.assertEqual(result["rom_number"], "0001")

        # Multiple digit ROM number
        result = parse_rom_filename("99999 - Some Game.gba")
        self.assertEqual(result["name"], "Some Game")
        self.assertEqual(result["rom_number"], "99999")

        # New format with period
        result = parse_rom_filename("42. Super Mario World.sfc")
        self.assertEqual(result["name"], "Super Mario World")
        self.assertEqual(result["rom_number"], "42")

        # Leading zeros with period format
        result = parse_rom_filename("0001. Tetris.gb")
        self.assertEqual(result["name"], "Tetris")
        self.assertEqual(result["rom_number"], "0001")

        # Multiple digit ROM number with period
        result = parse_rom_filename("99999. Some Game.gba")
        self.assertEqual(result["name"], "Some Game")
        self.assertEqual(result["rom_number"], "99999")

    def test_rom_number_without_prefix_pattern(self):
        """Test numbers that are part of game names, not prefixes."""
        # Number without dash pattern is part of name
        result = parse_rom_filename("007 James Bond.gba")
        self.assertEqual(result["name"], "007 James Bond")
        self.assertEqual(result["rom_number"], "")

        # Year in title stays as part of name
        result = parse_rom_filename("1942.nes")
        self.assertEqual(result["name"], "1942")
        self.assertEqual(result["rom_number"], "")

        # Sequel number without dash pattern
        result = parse_rom_filename("Street Fighter 2.sfc")
        self.assertEqual(result["name"], "Street Fighter 2")
        self.assertEqual(result["rom_number"], "")

    def test_edge_cases(self):
        """Test edge cases for ROM number parsing."""
        # Number with dash but no spaces doesn't match
        result = parse_rom_filename("123-NoSpaces.gba")
        self.assertEqual(result["name"], "123-NoSpaces")
        self.assertEqual(result["rom_number"], "")

        # Number at end doesn't match
        result = parse_rom_filename("Game Name - 456.gba")
        self.assertEqual(result["name"], "Game Name - 456")
        self.assertEqual(result["rom_number"], "")

        # Empty string for ROM number when no prefix
        result = parse_rom_filename("Super Mario World.sfc")
        self.assertEqual(result["name"], "Super Mario World")
        self.assertEqual(result["rom_number"], "")

    def test_rom_number_with_metadata(self):
        """Test ROM number parsing combined with other metadata."""
        result = parse_rom_filename("237 - Super Mario World (USA) (Rev 1).sfc")
        self.assertEqual(result["name"], "Super Mario World")
        self.assertEqual(result["rom_number"], "237")
        self.assertEqual(result["region"], "USA")
        self.assertEqual(result["revision"], "Rev 1")

        result = parse_rom_filename("0001 - Tetris (World).gb")
        self.assertEqual(result["name"], "Tetris")
        self.assertEqual(result["rom_number"], "0001")
        self.assertEqual(result["region"], "World")
        self.assertEqual(result["revision"], "")

        # Period format with metadata
        result = parse_rom_filename("42. Super Mario World (USA) (Rev 1).sfc")
        self.assertEqual(result["name"], "Super Mario World")
        self.assertEqual(result["rom_number"], "42")
        self.assertEqual(result["region"], "USA")
        self.assertEqual(result["revision"], "Rev 1")

        result = parse_rom_filename("0001. Tetris (World).gb")
        self.assertEqual(result["name"], "Tetris")
        self.assertEqual(result["rom_number"], "0001")
        self.assertEqual(result["region"], "World")
        self.assertEqual(result["revision"], "")


class TestCompoundExtensionParsing(TestCase):
    """Test compound extension handling in parser."""

    def test_compound_extension_parsing(self):
        """Test that parser handles .p8.png stem correctly."""
        # Test compound extension .p8.png
        result = parse_rom_filename("Celeste.p8.png")
        self.assertEqual(result["name"], "Celeste")
        self.assertEqual(result["extension"], ".p8.png")

        # Test compound extension with metadata
        result = parse_rom_filename("Celeste (World).p8.png")
        self.assertEqual(result["name"], "Celeste")
        self.assertEqual(result["extension"], ".p8.png")
        self.assertEqual(result["region"], "World")

        # Test compound extension with ROM number
        result = parse_rom_filename("001 - Celeste.p8.png")
        self.assertEqual(result["name"], "Celeste")
        self.assertEqual(result["extension"], ".p8.png")
        self.assertEqual(result["rom_number"], "001")

        # Test regular extension still works
        result = parse_rom_filename("Game.gba")
        self.assertEqual(result["name"], "Game")
        self.assertEqual(result["extension"], ".gba")

        # Test case insensitivity
        result = parse_rom_filename("GAME.P8.PNG")
        self.assertEqual(result["name"], "GAME")
        self.assertEqual(result["extension"], ".p8.png")

    def test_compound_extension_with_path(self):
        """Compound extensions should extract filename even when path is passed."""
        # This happens when parsing files inside archives with directory structure
        result = parse_rom_filename("Roms/PICO/Pieces of Cake.p8.png")
        self.assertEqual(result["name"], "Pieces of Cake")
        self.assertEqual(result["extension"], ".p8.png")

        # Test with deeper path
        result = parse_rom_filename("some/deep/path/Game.p8.png")
        self.assertEqual(result["name"], "Game")
        self.assertEqual(result["extension"], ".p8.png")

        # Test with metadata in path
        result = parse_rom_filename("Roms/PICO/Celeste (World).p8.png")
        self.assertEqual(result["name"], "Celeste")
        self.assertEqual(result["region"], "World")
        self.assertEqual(result["extension"], ".p8.png")

    def test_existing_parser_functionality(self):
        """Ensure existing parser functionality still works."""
        # Test region parsing
        result = parse_rom_filename(
            "Pokemon - Emerald Version (USA, Europe) (Rev 1).gba"
        )
        self.assertEqual(result["name"], "Pokemon - Emerald Version")
        self.assertEqual(result["region"], "USA")
        self.assertEqual(result["revision"], "Rev 1")
        self.assertEqual(result["rom_number"], "")

        # Test tag parsing
        result = parse_rom_filename("Castlevania - Aria of Sorrow (USA) [!].gba")
        self.assertEqual(result["name"], "Castlevania - Aria of Sorrow")
        self.assertEqual(result["region"], "USA")
        self.assertEqual(result["tags"], ["!"])
        self.assertEqual(result["rom_number"], "")

        # Test simple case
        result = parse_rom_filename("Game Name.gba")
        self.assertEqual(result["name"], "Game Name")
        self.assertEqual(result["region"], "")
        self.assertEqual(result["revision"], "")
        self.assertEqual(result["tags"], [])
        self.assertEqual(result["rom_number"], "")


class TestDiscParsing(TestCase):
    """Test disc/track number extraction from filenames."""

    def test_disc_number_basic(self):
        """Test basic disc number extraction."""
        result = parse_rom_filename("Final Fantasy VII (USA) (Disc 1).bin")
        self.assertEqual(result["name"], "Final Fantasy VII")
        self.assertEqual(result["disc"], 1)
        self.assertEqual(result["region"], "USA")

        result = parse_rom_filename("Metal Gear Solid (Disc 2).bin")
        self.assertEqual(result["name"], "Metal Gear Solid")
        self.assertEqual(result["disc"], 2)

    def test_track_number(self):
        """Test track number extraction (alternative to disc)."""
        result = parse_rom_filename("Resident Evil 2 (USA) (Track 1).cue")
        self.assertEqual(result["name"], "Resident Evil 2")
        self.assertEqual(result["disc"], 1)

    def test_disc_of_total(self):
        """Test 'Disc N of M' format extracts only N."""
        result = parse_rom_filename("Chrono Cross (USA) (Disc 1 of 2).bin")
        self.assertEqual(result["name"], "Chrono Cross")
        self.assertEqual(result["disc"], 1)

        result = parse_rom_filename("Chrono Cross (USA) (Disc 2 of 2).bin")
        self.assertEqual(result["disc"], 2)

    def test_case_insensitive(self):
        """Test case-insensitive disc detection."""
        result = parse_rom_filename("Game (disc 1).bin")
        self.assertEqual(result["disc"], 1)

        result = parse_rom_filename("Game (DISC 1).bin")
        self.assertEqual(result["disc"], 1)

        result = parse_rom_filename("Game (Disc 1).bin")
        self.assertEqual(result["disc"], 1)

    def test_no_disc(self):
        """Test that single-disc games return None."""
        result = parse_rom_filename("Super Mario World (USA).sfc")
        self.assertIsNone(result["disc"])

        result = parse_rom_filename("Game Name.gba")
        self.assertIsNone(result["disc"])

    def test_disc_not_in_tags(self):
        """Test that disc indicator is not included in tags."""
        result = parse_rom_filename("Game (USA) (Disc 1) [!].bin")
        self.assertEqual(result["disc"], 1)
        self.assertNotIn("Disc 1", result["tags"])
        self.assertIn("!", result["tags"])

    def test_disc_with_rom_number(self):
        """Test disc parsing combined with ROM number prefix."""
        result = parse_rom_filename("0001 - Final Fantasy VII (USA) (Disc 1).bin")
        self.assertEqual(result["name"], "Final Fantasy VII")
        self.assertEqual(result["rom_number"], "0001")
        self.assertEqual(result["disc"], 1)
        self.assertEqual(result["region"], "USA")


class TestUnderscoreHandling(TestCase):
    """Test underscore to space conversion in game names."""

    def test_basic_underscore_conversion(self):
        """Test simple underscore to space conversion."""
        result = parse_rom_filename("Super_Mario_World.sfc")
        self.assertEqual(result["name"], "Super Mario World")

        result = parse_rom_filename("The_Legend_of_Zelda.sfc")
        self.assertEqual(result["name"], "The Legend of Zelda")

    def test_mixed_separators(self):
        """Test underscores with other separators."""
        result = parse_rom_filename("Super_Mario-World.sfc")
        self.assertEqual(result["name"], "Super Mario-World")

        result = parse_rom_filename("Game_Title_-_The_Game.gba")
        self.assertEqual(result["name"], "Game Title - The Game")

    def test_underscore_with_metadata(self):
        """Test underscores combined with regions, revisions, etc."""
        result = parse_rom_filename("Super_Mario_World (USA).sfc")
        self.assertEqual(result["name"], "Super Mario World")
        self.assertEqual(result["region"], "USA")

        result = parse_rom_filename("The_Legend_of_Zelda (USA) (Rev 1).sfc")
        self.assertEqual(result["name"], "The Legend of Zelda")
        self.assertEqual(result["region"], "USA")
        self.assertEqual(result["revision"], "Rev 1")

    def test_underscore_with_rom_number(self):
        """Test underscores with ROM number prefixes."""
        result = parse_rom_filename("001 - Super_Mario_World.sfc")
        self.assertEqual(result["name"], "Super Mario World")
        self.assertEqual(result["rom_number"], "001")

        result = parse_rom_filename("237. The_Legend_of_Zelda.gba")
        self.assertEqual(result["name"], "The Legend of Zelda")
        self.assertEqual(result["rom_number"], "237")

    def test_edge_cases(self):
        """Test edge cases with underscores."""
        result = parse_rom_filename("Game_.sfc")
        self.assertEqual(result["name"], "Game")

        result = parse_rom_filename("_Game.sfc")
        self.assertEqual(result["name"], "Game")

        result = parse_rom_filename("_Game_.sfc")
        self.assertEqual(result["name"], "Game")

    def test_multiple_consecutive_underscores(self):
        """Test multiple consecutive underscores become multiple spaces."""
        result = parse_rom_filename("Super___Mario_World.sfc")
        self.assertEqual(result["name"], "Super   Mario World")

    def test_metadata_underscores_preserved(self):
        """Test that underscores in metadata are preserved appropriately."""
        result = parse_rom_filename("Game_Name (Custom_Tag).sfc")
        self.assertEqual(result["name"], "Game Name")
        self.assertEqual(result["tags"], ["Custom_Tag"])  # Tag underscore preserved


class TestPrefixTags(TestCase):
    """Test filenames starting with tags."""

    def test_filename_starting_with_tag(self):
        """Test filenames starting with tags like [BIOS] or (Beta)."""
        # Case 1: [BIOS] prefix
        filename = "[BIOS] SNK Neo Geo Pocket (Japan, Europe) (En,Ja).ngp"
        result = parse_rom_filename(filename)
        self.assertEqual(result["name"], "SNK Neo Geo Pocket")
        self.assertEqual(result["region"], "Japan")

        # Case 2: (Beta) prefix
        filename = "(Beta) Super Mario World.sfc"
        result = parse_rom_filename(filename)
        self.assertEqual(result["name"], "Super Mario World")
        self.assertEqual(result["tags"], ["Beta"])

        # Case 3: Mixed prefix tags
        filename = "[!] (USA) Game Name.gba"
        result = parse_rom_filename(filename)
        self.assertEqual(result["name"], "Game Name")
        self.assertEqual(result["region"], "USA")
        self.assertIn("!", result["tags"])
