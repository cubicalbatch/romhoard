from django.contrib import admin

from .models import (
    DownloadJob,
    Game,
    GameImage,
    Genre,
    HasheousCache,
    MetadataBatch,
    MetadataJob,
    ROM,
    ROMSet,
    ScanJob,
    ScanPath,
    SendJob,
    Setting,
    System,
    SystemMetadataJob,
)


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "game_count"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}

    def game_count(self, obj):
        return obj.games.count()

    game_count.short_description = "Games"


@admin.register(System)
class SystemAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "extensions"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ["name", "system", "romset_count", "created_at"]
    list_filter = ["system"]
    search_fields = ["name"]
    raw_id_fields = ["default_rom_set"]

    def romset_count(self, obj):
        return obj.rom_sets.count()

    romset_count.short_description = "ROM Sets"


@admin.register(ROMSet)
class ROMSetAdmin(admin.ModelAdmin):
    list_display = [
        "__str__",
        "game",
        "region",
        "revision",
        "rom_count",
        "is_multi_disc",
    ]
    list_filter = ["game__system", "region"]
    search_fields = ["game__name", "region", "revision"]
    raw_id_fields = ["game"]

    def rom_count(self, obj):
        return obj.rom_count

    rom_count.short_description = "ROM Count"


@admin.register(ROM)
class ROMAdmin(admin.ModelAdmin):
    list_display = ["file_name", "get_game", "rom_number", "disc"]
    list_filter = ["rom_set__game__system"]
    search_fields = ["file_name", "rom_set__game__name", "rom_number"]
    raw_id_fields = ["rom_set"]

    def get_game(self, obj):
        return obj.game

    get_game.short_description = "Game"
    get_game.admin_order_field = "rom_set__game"


@admin.register(GameImage)
class GameImageAdmin(admin.ModelAdmin):
    list_display = ["file_name", "game", "file_size", "created_at"]
    list_filter = ["game__system"]
    search_fields = ["file_name", "game__name"]
    raw_id_fields = ["game"]


@admin.register(ScanPath)
class ScanPathAdmin(admin.ModelAdmin):
    list_display = ["path", "last_scanned", "created_at"]
    search_fields = ["path"]


@admin.register(Setting)
class SettingAdmin(admin.ModelAdmin):
    list_display = ["key", "value"]
    search_fields = ["key"]


@admin.register(ScanJob)
class ScanJobAdmin(admin.ModelAdmin):
    list_display = ["path", "status", "started_at", "completed_at", "added", "skipped"]
    list_filter = ["status"]
    search_fields = ["path"]
    readonly_fields = ["task_id", "started_at", "completed_at"]


@admin.register(DownloadJob)
class DownloadJobAdmin(admin.ModelAdmin):
    list_display = [
        "system_slug",
        "status",
        "games_total",
        "games_included",
        "created_at",
    ]
    list_filter = ["status", "system_slug"]
    readonly_fields = ["task_id", "created_at", "completed_at", "expires_at"]


@admin.register(SendJob)
class SendJobAdmin(admin.ModelAdmin):
    list_display = [
        "device",
        "status",
        "files_total",
        "files_uploaded",
        "files_skipped",
        "files_failed",
        "created_at",
    ]
    list_filter = ["status"]
    readonly_fields = ["task_id", "created_at", "started_at", "completed_at"]
    raw_id_fields = ["device"]


@admin.register(MetadataBatch)
class MetadataBatchAdmin(admin.ModelAdmin):
    list_display = [
        "system_slug",
        "status",
        "total_count",
        "completed_count",
        "matched_count",
        "created_at",
        "completed_at",
    ]
    list_filter = ["status", "system_slug"]
    readonly_fields = ["created_at", "started_at", "completed_at"]


@admin.register(MetadataJob)
class MetadataJobAdmin(admin.ModelAdmin):
    list_display = [
        "game",
        "batch",
        "status",
        "matched",
        "images_downloaded",
        "created_at",
    ]
    list_filter = ["status", "matched"]
    readonly_fields = ["task_id", "created_at", "started_at", "completed_at"]
    raw_id_fields = ["batch", "game"]


@admin.register(SystemMetadataJob)
class SystemMetadataJobAdmin(admin.ModelAdmin):
    list_display = [
        "status",
        "systems_processed",
        "systems_updated",
        "created_at",
        "completed_at",
    ]
    list_filter = ["status"]
    readonly_fields = ["task_id", "created_at", "started_at", "completed_at"]


@admin.register(HasheousCache)
class HasheousCacheAdmin(admin.ModelAdmin):
    list_display = [
        "hash_type",
        "hash_value_short",
        "matched",
        "game_name",
        "source",
        "created_at",
    ]
    list_filter = ["hash_type", "matched", "source"]
    search_fields = ["hash_value", "game_name", "raw_name"]
    readonly_fields = ["created_at"]

    def hash_value_short(self, obj):
        return (
            f"{obj.hash_value[:12]}..." if len(obj.hash_value) > 12 else obj.hash_value
        )

    hash_value_short.short_description = "Hash"
