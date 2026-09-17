"""Tests for system configuration, models, and multi-system ScreenScraper mapping."""

from unittest.mock import MagicMock, patch
import pytest

from library.lookup.screenscraper import ScreenScraperLookupService
from library.models import System
from library.system_loader import get_systems_config, sync_systems


class TestSystemModelScreenscraperIds:
    """Tests for System.screenscraper_id and System.all_screenscraper_ids."""

    def test_screenscraper_id_returns_primary(self):
        """screenscraper_id should return the first ID in the list."""
        sys = System(name="Sega Master System", slug="sms", screenscraper_ids=[2, 21, 109])
        assert sys.screenscraper_id == 2

    def test_screenscraper_id_returns_none_when_empty(self):
        """screenscraper_id should return None when list is empty."""
        sys = System(name="Unknown", slug="unknown", screenscraper_ids=[])
        assert sys.screenscraper_id is None

    def test_all_screenscraper_ids_returns_list_in_priority_order(self):
        """all_screenscraper_ids should return all IDs in priority order."""
        sys = System(name="Neo Geo", slug="neogeo", screenscraper_ids=[142, 82, 25])
        assert sys.all_screenscraper_ids == [142, 82, 25]

    def test_all_screenscraper_ids_returns_empty_list_when_empty(self):
        """all_screenscraper_ids should return empty list when no IDs configured."""
        sys = System(name="Custom", slug="custom", screenscraper_ids=[])
        assert sys.all_screenscraper_ids == []

    def test_all_screenscraper_ids_handles_none(self):
        """all_screenscraper_ids should return empty list if screenscraper_ids is None."""
        sys = System(name="Custom", slug="custom", screenscraper_ids=None)
        assert sys.all_screenscraper_ids == []


class TestSystemsConfig:
    """Tests for systems.json configuration."""

    def test_compatible_hardware_families_screenscraper_ids(self):
        """Verify multi-system IDs configured for compatible hardware families."""
        config = get_systems_config()
        systems_by_slug = {s["slug"]: s for s in config}

        expected = {
            "sms": [2, 21, 109],       # Master System, Game Gear, SG-1000
            "neogeo": [142, 82, 25],   # Neo Geo MVS/AES, NGPC, NGP
            "nes": [3, 106],           # NES, Famicom Disk System
            "pce": [31, 105],          # PC Engine / TG16, SuperGrafx
            "msx": [113, 116, 117, 118],  # MSX, MSX2, MSX2+, Turbo R
            "ws": [45, 46],            # WonderSwan, WonderSwan Color
        }

        for slug, expected_ids in expected.items():
            assert slug in systems_by_slug, f"System {slug} missing from systems.json"
            actual_ids = systems_by_slug[slug].get("screenscraper_ids")
            assert actual_ids == expected_ids, (
                f"System {slug} has screenscraper_ids {actual_ids}, expected {expected_ids}"
            )


@pytest.mark.django_db
class TestSyncSystems:
    """Tests for syncing systems config to the database."""

    def test_sync_systems_populates_multi_screenscraper_ids(self):
        """sync_systems updates database records with multi-system IDs in priority order."""
        sync_systems()

        expected = {
            "sms": ([2, 21, 109], 2),
            "neogeo": ([142, 82, 25], 142),
            "nes": ([3, 106], 3),
            "pce": ([31, 105], 31),
        }

        for slug, (expected_ids, expected_primary) in expected.items():
            system = System.objects.get(slug=slug)
            assert system.all_screenscraper_ids == expected_ids
            assert system.screenscraper_id == expected_primary

    def test_sms_lookup_tries_ids_in_priority_order(self):
        """ScreenScraper lookup for SMS tries primary (2) then GG (21) then SG-1000 (109)."""
        sms = System.objects.create(
            name="Sega Master System",
            slug="test_sms_lookup",
            extensions=[".sms"],
            folder_names=["SMS"],
            screenscraper_ids=[2, 21, 109],
        )

        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_by_romnom.return_value = None

        called_system_ids = []

        def mock_search_by_crc(crc, sys_id):
            called_system_ids.append(sys_id)
            if sys_id == 21:
                return {"id": 5555, "name": "Sonic The Hedgehog (GG)", "system_id": 21}
            return None

        mock_client.search_by_crc.side_effect = mock_search_by_crc

        with patch.object(service, "_get_client", return_value=mock_client):
            result = service.lookup(
                system=sms,
                crc32="ABC12345",
                file_path="/roms/sms/Sonic (GG).sms",
            )

        assert result is not None
        assert result.screenscraper_id == 5555
        # It checked primary (2), then alternate (21), and stopped before (109)
        assert called_system_ids == [2, 21]

    def test_neogeo_lookup_tries_ids_in_priority_order(self):
        """ScreenScraper lookup for Neo Geo tries MVS (142) then NGPC (82) then NGP (25)."""
        neogeo = System.objects.create(
            name="Neo Geo",
            slug="test_neogeo_lookup",
            extensions=[],
            folder_names=["Neo Geo"],
            archive_as_rom=True,
            screenscraper_ids=[142, 82, 25],
        )

        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True

        called_system_ids = []

        def mock_search_by_romnom(romnom, sys_id):
            called_system_ids.append(sys_id)
            if sys_id == 82:
                return {"id": 8888, "name": "SNK Gals Fighters", "system_id": 82}
            return None

        mock_client.search_by_romnom.side_effect = mock_search_by_romnom

        with patch.object(service, "_get_client", return_value=mock_client):
            result = service.lookup(
                system=neogeo,
                crc32="11223344",
                file_path="/roms/neogeo/galsfight.zip",
            )

        assert result is not None
        assert result.screenscraper_id == 8888
        assert called_system_ids == [142, 82]

    def test_nes_lookup_tries_ids_in_priority_order(self):
        """ScreenScraper lookup for NES tries NES (3) then FDS (106)."""
        nes = System.objects.create(
            name="NES",
            slug="test_nes_lookup",
            extensions=[".nes"],
            folder_names=["NES"],
            screenscraper_ids=[3, 106],
        )

        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_by_romnom.return_value = None

        called_system_ids = []

        def mock_search_by_crc(crc, sys_id):
            called_system_ids.append(sys_id)
            if sys_id == 106:
                return {"id": 3333, "name": "Metroid (FDS Conversion)", "system_id": 106}
            return None

        mock_client.search_by_crc.side_effect = mock_search_by_crc

        with patch.object(service, "_get_client", return_value=mock_client):
            result = service.lookup(
                system=nes,
                crc32="FEDCBA98",
                file_path="/roms/nes/Metroid (FDS).nes",
            )

        assert result is not None
        assert result.screenscraper_id == 3333
        assert called_system_ids == [3, 106]

    def test_pce_lookup_tries_ids_in_priority_order(self):
        """ScreenScraper lookup for PCE tries PCE (31) then SuperGrafx (105)."""
        pce = System.objects.create(
            name="NEC PC Engine",
            slug="test_pce_lookup",
            extensions=[".pce"],
            folder_names=["PCE"],
            screenscraper_ids=[31, 105],
        )

        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_by_romnom.return_value = None

        called_system_ids = []

        def mock_search_by_crc(crc, sys_id):
            called_system_ids.append(sys_id)
            if sys_id == 105:
                return {"id": 4444, "name": "1941 Counter Attack (SGX)", "system_id": 105}
            return None

        mock_client.search_by_crc.side_effect = mock_search_by_crc

        with patch.object(service, "_get_client", return_value=mock_client):
            result = service.lookup(
                system=pce,
                crc32="44332211",
                file_path="/roms/pce/1941 Counter Attack.pce",
            )

        assert result is not None
        assert result.screenscraper_id == 4444
        assert called_system_ids == [31, 105]
