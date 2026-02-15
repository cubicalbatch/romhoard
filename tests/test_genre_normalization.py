"""Tests for genre normalization and hierarchy."""

import pytest

from library.metadata.normalize import normalize_genre, normalize_genres


class TestNormalizeGenre:
    """Tests for the normalize_genre function."""

    def test_normalize_rpg(self):
        """Test: Role Playing Game -> RPG"""
        assert normalize_genre("Role Playing Game") == "RPG"

    def test_normalize_jrpg(self):
        """Test: Japanese RPG -> JRPG"""
        assert normalize_genre("Japanese RPG") == "JRPG"

    def test_normalize_dungeon_crawler(self):
        """Test: Dungeon Crawler RPG -> Dungeon Crawler"""
        assert normalize_genre("Dungeon Crawler RPG") == "Dungeon Crawler"

    def test_normalize_shooter_fpv_to_fps(self):
        """Test: Shooter / FPV -> FPS"""
        assert normalize_genre("Shooter / FPV") == "FPS"

    def test_normalize_shooter_tpv(self):
        """Test: Shooter / TPV -> Third Person Shooter"""
        assert normalize_genre("Shooter / TPV") == "Third Person Shooter"

    def test_normalize_shooter_run_and_gun(self):
        """Test: Shooter / Run and Gun -> Run and Gun"""
        assert normalize_genre("Shooter / Run and Gun") == "Run and Gun"

    def test_normalize_board_game(self):
        """Test: Board game -> Board Game"""
        assert normalize_genre("Board game") == "Board Game"

    def test_normalize_asiatic_board_game(self):
        """Test: Asiatic board game -> Board Game"""
        assert normalize_genre("Asiatic board game") == "Board Game"

    def test_normalize_adventure_point_and_click(self):
        """Test: Adventure / Point and Click -> Point and Click"""
        assert normalize_genre("Adventure / Point and Click") == "Point and Click"

    def test_normalize_adventure_survival_horror(self):
        """Test: Adventure / Survival Horror -> Survival Horror"""
        assert normalize_genre("Adventure / Survival Horror") == "Survival Horror"

    def test_normalize_adventure_visual_novel(self):
        """Test: Adventure / Visual Novel -> Visual Novel"""
        assert normalize_genre("Adventure / Visual Novel") == "Visual Novel"

    def test_normalize_adventure_text(self):
        """Test: Adventure / Text -> Text Adventure"""
        assert normalize_genre("Adventure / Text") == "Text Adventure"

    def test_normalize_fishing(self):
        """Test: Fishing -> Hunting and Fishing"""
        assert normalize_genre("Fishing") == "Hunting and Fishing"

    def test_normalize_hunting(self):
        """Test: Hunting -> Hunting and Fishing"""
        assert normalize_genre("Hunting") == "Hunting and Fishing"

    def test_normalize_music_and_dancing(self):
        """Test: Music and Dancing -> Rhythm"""
        assert normalize_genre("Music and Dancing") == "Rhythm"

    def test_normalize_various(self):
        """Test: Various -> Misc"""
        assert normalize_genre("Various") == "Misc"

    def test_normalize_various_utilities(self):
        """Test: Various / Utilities -> Misc"""
        assert normalize_genre("Various / Utilities") == "Misc"

    def test_normalize_horse_racing(self):
        """Test: Horse racing -> Racing"""
        assert normalize_genre("Horse racing") == "Racing"

    def test_normalize_racing_driving(self):
        """Test: Racing, Driving -> Racing"""
        assert normalize_genre("Racing, Driving") == "Racing"

    # Hierarchy preservation tests - these should NOT be flattened
    def test_preserve_fighting_2d(self):
        """Test: Fighting / 2D is preserved (hierarchy, not flattened)"""
        assert normalize_genre("Fighting / 2D") == "Fighting / 2D"

    def test_preserve_fighting_3d(self):
        """Test: Fighting / 3D is preserved (hierarchy, not flattened)"""
        assert normalize_genre("Fighting / 3D") == "Fighting / 3D"

    def test_preserve_fighting_versus(self):
        """Test: Fighting / Versus is preserved"""
        assert normalize_genre("Fighting / Versus") == "Fighting / Versus"

    def test_preserve_shootemup_horizontal(self):
        """Test: Shoot'em Up / Horizontal is preserved"""
        assert normalize_genre("Shoot'em Up / Horizontal") == "Shoot'em Up / Horizontal"

    def test_preserve_shootemup_vertical(self):
        """Test: Shoot'em Up / Vertical is preserved"""
        assert normalize_genre("Shoot'em Up / Vertical") == "Shoot'em Up / Vertical"

    def test_preserve_shooter_horizontal(self):
        """Test: Shooter / Horizontal is preserved"""
        assert normalize_genre("Shooter / Horizontal") == "Shooter / Horizontal"

    def test_preserve_puzzle_fall(self):
        """Test: Puzzle / Fall is preserved"""
        assert normalize_genre("Puzzle / Fall") == "Puzzle / Fall"

    def test_preserve_puzzle_throw(self):
        """Test: Puzzle / Throw is preserved"""
        assert normalize_genre("Puzzle / Throw") == "Puzzle / Throw"

    def test_preserve_platform_run_jump(self):
        """Test: Platform / Run & Jump is preserved"""
        assert normalize_genre("Platform / Run & Jump") == "Platform / Run & Jump"

    def test_preserve_casino_cards(self):
        """Test: Casino / Cards is preserved"""
        assert normalize_genre("Casino / Cards") == "Casino / Cards"

    def test_preserve_action_rpg(self):
        """Test: Action RPG is preserved (distinct subgenre)"""
        assert normalize_genre("Action RPG") == "Action RPG"

    def test_preserve_tactical_rpg(self):
        """Test: Tactical RPG is preserved (distinct subgenre)"""
        assert normalize_genre("Tactical RPG") == "Tactical RPG"

    def test_preserve_action(self):
        """Test: Action is preserved"""
        assert normalize_genre("Action") == "Action"

    def test_preserve_adventure(self):
        """Test: Adventure is preserved"""
        assert normalize_genre("Adventure") == "Adventure"

    def test_preserve_fighting_base(self):
        """Test: Fighting is preserved (no variant)"""
        assert normalize_genre("Fighting") == "Fighting"

    def test_preserve_platform_fighter_scrolling(self):
        """Test: Platform / Fighter Scrolling is preserved"""
        assert (
            normalize_genre("Platform / Fighter Scrolling")
            == "Platform / Fighter Scrolling"
        )

    def test_preserve_shooter_space_invaders(self):
        """Test: Shooter / Space Invaders Like is preserved"""
        assert (
            normalize_genre("Shooter / Space Invaders Like")
            == "Shooter / Space Invaders Like"
        )

    def test_preserve_simulation(self):
        """Test: Simulation is preserved"""
        assert normalize_genre("Simulation") == "Simulation"

    def test_preserve_sports(self):
        """Test: Sports categories are preserved"""
        assert normalize_genre("Baseball") == "Baseball"
        assert normalize_genre("Soccer") == "Soccer"
        assert normalize_genre("Golf") == "Golf"

    def test_preserve_action_labyrinth(self):
        """Test: Action / Labyrinth is preserved (hierarchy)"""
        assert normalize_genre("Action / Labyrinth") == "Action / Labyrinth"


class TestNormalizeGenres:
    """Tests for the normalize_genres function."""

    def test_normalize_genres_preserves_hierarchy(self):
        """Test: Hierarchical genres are preserved, not flattened"""
        result = normalize_genres(["Fighting / 2D", "Fighting / 3D", "Action"])
        assert result == ["Fighting / 2D", "Fighting / 3D", "Action"]

    def test_normalize_genres_preserves_order(self):
        """Test: Order of first occurrence is preserved"""
        result = normalize_genres(["Action", "Puzzle / Fall", "RPG"])
        assert result == ["Action", "Puzzle / Fall", "RPG"]

    def test_normalize_genres_empty_list(self):
        """Test: Empty list returns empty list"""
        result = normalize_genres([])
        assert result == []

    def test_normalize_genres_no_duplicates(self):
        """Test: Genres without duplicates pass through"""
        result = normalize_genres(["Action", "Adventure", "RPG"])
        assert result == ["Action", "Adventure", "RPG"]

    def test_normalize_genres_mixed_normalized_and_canonical(self):
        """Test: Mix of normalized and already-canonical genres"""
        result = normalize_genres(["Role Playing Game", "Action RPG", "RPG"])
        # "Role Playing Game" normalizes to "RPG", then "RPG" is a duplicate
        assert result == ["RPG", "Action RPG"]

    def test_normalize_genres_meaningful_renames(self):
        """Test: Meaningful shooter variants are renamed appropriately"""
        result = normalize_genres(
            [
                "Shooter / FPV",
                "Shooter / TPV",
                "Shooter / Run and Gun",
                "Shooter / Horizontal",
            ]
        )
        # FPV -> FPS, TPV -> Third Person Shooter, Run and Gun stays
        # Horizontal is preserved (no longer flattened to "Shooter")
        assert result == [
            "FPS",
            "Third Person Shooter",
            "Run and Gun",
            "Shooter / Horizontal",
        ]

    def test_normalize_genres_racing_base_only(self):
        """Test: Racing base formats consolidate to Racing"""
        result = normalize_genres(
            [
                "Racing, Driving",
                "Horse racing",
                "Racing FPV",
            ]
        )
        assert result == ["Racing"]

    def test_normalize_genres_racing_with_hierarchy(self):
        """Test: Racing hierarchy is preserved"""
        result = normalize_genres(
            [
                "Racing, Driving / Motorcycle",
                "Racing, Driving / Plane",
            ]
        )
        # These have " / " so hierarchy is preserved
        assert result == ["Racing, Driving / Motorcycle", "Racing, Driving / Plane"]


@pytest.mark.django_db
class TestGenreModel:
    """Tests for Genre model short_name and parent relationship."""

    def test_short_name_simple(self):
        """Test: Simple genre returns itself as short_name"""
        from library.models import Genre

        genre = Genre(name="Action", slug="action")
        assert genre.short_name == "Action"

    def test_short_name_hierarchical(self):
        """Test: Hierarchical genre returns only the leaf part"""
        from library.models import Genre

        genre = Genre(name="Action / Labyrinth", slug="action-labyrinth")
        assert genre.short_name == "Labyrinth"

    def test_short_name_multi_level(self):
        """Test: Multi-level hierarchy returns only the last part"""
        from library.models import Genre

        genre = Genre(
            name="Action / Platform / Run & Jump", slug="action-platform-run-jump"
        )
        assert genre.short_name == "Run & Jump"


@pytest.mark.django_db
class TestGetOrCreateGenreWithParent:
    """Tests for the get_or_create_genre_with_parent helper."""

    def test_creates_simple_genre(self):
        """Test: Creates a simple genre without parent"""
        from library.metadata.genres import get_or_create_genre_with_parent
        from library.models import Genre

        genre = get_or_create_genre_with_parent("Action")
        assert genre.name == "Action"
        assert genre.parent is None

    def test_creates_hierarchical_genre_with_parent(self):
        """Test: Creates a hierarchical genre and its parent"""
        from library.metadata.genres import get_or_create_genre_with_parent
        from library.models import Genre

        genre = get_or_create_genre_with_parent("Action / Labyrinth")
        assert genre.name == "Action / Labyrinth"
        assert genre.parent is not None
        assert genre.parent.name == "Action"

    def test_reuses_existing_parent(self):
        """Test: Reuses an existing parent genre"""
        from library.metadata.genres import get_or_create_genre_with_parent
        from library.models import Genre

        # Create the parent first
        parent = Genre.objects.create(name="Fighting", slug="fighting")

        # Create the child
        genre = get_or_create_genre_with_parent("Fighting / 2D")
        assert genre.parent == parent

    def test_links_existing_genre_to_parent(self):
        """Test: Links an existing unlinked genre to its parent"""
        from library.metadata.genres import get_or_create_genre_with_parent
        from library.models import Genre

        # Create the genre without parent
        genre = Genre.objects.create(name="Puzzle / Fall", slug="puzzle-fall")
        assert genre.parent is None

        # Call the helper, which should link it
        updated = get_or_create_genre_with_parent("Puzzle / Fall")
        assert updated.pk == genre.pk
        assert updated.parent is not None
        assert updated.parent.name == "Puzzle"


@pytest.mark.django_db
class TestGameDisplayGenres:
    """Tests for Game.display_genres method."""

    def test_display_genres_no_parents(self):
        """Test: When no parent genres are present, all genres are returned"""
        from library.models import Game, Genre, System

        system = System.objects.create(
            name="Test System", slug="test", extensions=[], folder_names=[]
        )
        game = Game.objects.create(name="Test Game", system=system)

        g1 = Genre.objects.create(name="Action", slug="action")
        g2 = Genre.objects.create(name="Adventure", slug="adventure")
        game.genres.add(g1, g2)

        display = game.display_genres()
        assert len(display) == 2
        assert g1 in display
        assert g2 in display

    def test_display_genres_excludes_parent_when_child_present(self):
        """Test: Parent is excluded when its child is also present"""
        from library.models import Game, Genre, System

        system = System.objects.create(
            name="Test System", slug="test2", extensions=[], folder_names=[]
        )
        game = Game.objects.create(name="Test Game 2", system=system)

        parent = Genre.objects.create(name="Action", slug="action2")
        child = Genre.objects.create(
            name="Action / Labyrinth", slug="action-labyrinth", parent=parent
        )
        game.genres.add(parent, child)

        display = game.display_genres()
        assert len(display) == 1
        assert child in display
        assert parent not in display

    def test_display_genres_keeps_unrelated_parent(self):
        """Test: Unrelated parent genres are kept"""
        from library.models import Game, Genre, System

        system = System.objects.create(
            name="Test System", slug="test3", extensions=[], folder_names=[]
        )
        game = Game.objects.create(name="Test Game 3", system=system)

        action = Genre.objects.create(name="Action", slug="action3")
        puzzle = Genre.objects.create(name="Puzzle", slug="puzzle3")
        puzzle_child = Genre.objects.create(
            name="Puzzle / Fall", slug="puzzle-fall3", parent=puzzle
        )
        game.genres.add(action, puzzle, puzzle_child)

        display = game.display_genres()
        # Action should remain (no child present)
        # Puzzle should be excluded (Puzzle / Fall is present)
        # Puzzle / Fall should remain
        assert len(display) == 2
        assert action in display
        assert puzzle_child in display
        assert puzzle not in display
