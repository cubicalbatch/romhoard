"""Tests for the release-tag glossary."""

from typing import cast

import pytest

from library.release_tags import describe_release_tag


class TestCodedFlags:
    @pytest.mark.parametrize(
        ("tag", "expected"),
        [
            ("!", "Verified good dump"),
            ("cr", "Cracked"),
            ("cr GroupName", "Cracked: GroupName"),
            ("f", "Fixed / altered for compatibility"),
            ("f NTSC-fix", "Fixed / altered for compatibility: NTSC-fix"),
            ("h", "Hacked / deliberately modified"),
            ("h Hardcore", "Hacked / deliberately modified: Hardcore"),
            ("m", "Modified (not necessarily deliberately)"),
            ("m Colour-fix", "Modified (not necessarily deliberately): Colour-fix"),
            ("p", "Pirated / unlicensed version"),
            ("p Bootleg", "Pirated / unlicensed version: Bootleg"),
            ("t", "Trained: cheats or a cheat menu added"),
            ("t TrainerName", "Trained: TrainerName"),
            ("tr", "Translation"),
            ("tr En-partial", "Translation: En-partial"),
            ("tr German (Fan)", "Translation: German (Fan)"),
            ("o", "Overdump: duplicated or excess dumped data"),
            ("o Extra", "Overdump: duplicated or excess dumped data: Extra"),
            ("u", "Underdump: missing dumped data"),
            ("u Missing", "Underdump: missing dumped data: Missing"),
            ("v", "Virus-infected"),
            ("v VirusName", "Virus-infected: VirusName"),
            ("b", "Bad or damaged dump"),
            ("b BadSector", "Bad or damaged dump: BadSector"),
            ("a", "Alternate original version"),
            ("a Alt", "Alternate original version: Alt"),
        ],
    )
    def test_descriptions(self, tag, expected):
        assert describe_release_tag(tag) == expected

    @pytest.mark.parametrize(
        ("tag", "expected"),
        [
            ("t +2", "Trained: 2 trainers added"),
            ("t +1", "Trained: 1 trainer added"),
            ("t +2 TrainerName", "Trained: 2 trainers added: TrainerName"),
            ("t +12 Name Extra", "Trained: 12 trainers added: Name Extra"),
        ],
    )
    def test_trainer_counts(self, tag, expected):
        assert describe_release_tag(tag) == expected

    def test_trainer_count_explains_exact_number(self):
        description = describe_release_tag("t +2")

        assert description is not None
        assert "Trained" in description
        assert "2 trainers added" in description

    def test_trainer_descriptor_preserved(self):
        description = describe_release_tag("t +2 TrainerName")

        assert description is not None
        assert "2 trainers added" in description
        assert "TrainerName" in description

    def test_translation_descriptor_preserved(self):
        description = describe_release_tag("tr En-partial")

        assert description is not None
        assert "Translation" in description
        assert "En-partial" in description

    def test_crack_descriptor_preserved(self):
        description = describe_release_tag("cr GroupName")

        assert description is not None
        assert "Cracked" in description
        assert "GroupName" in description


class TestWordTags:
    @pytest.mark.parametrize(
        ("tag", "expected"),
        [
            ("BIOS", "BIOS: system firmware image"),
            ("bios", "BIOS: system firmware image"),
            ("demo", "Demo version"),
            ("DEMO", "Demo version"),
            ("demo-kiosk", "Kiosk demo version"),
            ("Demo-Playable", "Playable demo version"),
            ("demo-rolling", "Rolling demo (non-interactive attract mode)"),
            ("demo-slideshow", "Slideshow demo"),
            ("beta", "Beta version"),
            ("BETA", "Beta version"),
            ("proto", "Prototype version"),
            ("Proto", "Prototype version"),
            ("unl", "Unlicensed release"),
            ("UNL", "Unlicensed release"),
        ],
    )
    def test_word_tag_descriptions_are_case_insensitive(self, tag, expected):
        assert describe_release_tag(tag) == expected


class TestFallbacks:
    @pytest.mark.parametrize(
        "tag",
        [
            "",
            "   ",
            "unknown",
            "demo-unknown",
            "demokiosk",
            # Similar but unsupported codes must not borrow a prefix meaning.
            "crx Group",
            "cracked",
            "trx Group",
            "trained",
            "T +2",
            "CR Group",
            "TR En",
            "t +",
            "t +N",
            "t +2N",
            "T",
            "Tr",
        ],
    )
    def test_unknown_or_ambiguous_returns_none(self, tag):
        assert describe_release_tag(tag) is None

    def test_non_string_returns_none(self):
        assert describe_release_tag(cast(str, None)) is None
