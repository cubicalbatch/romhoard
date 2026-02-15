from django.contrib import admin

from .models import Collection, CollectionEntry


class CollectionEntryInline(admin.TabularInline):
    model = CollectionEntry
    extra = 0
    fields = ["game_name", "system_slug", "position", "notes"]
    ordering = ["position"]


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "entry_count", "creator", "is_public", "created_at"]
    list_filter = ["is_public", "created_at"]
    search_fields = ["name", "slug", "description", "creator"]
    prepopulated_fields = {"slug": ("name",)}
    inlines = [CollectionEntryInline]

    def entry_count(self, obj):
        return obj.entries.count()

    entry_count.short_description = "Entries"


@admin.register(CollectionEntry)
class CollectionEntryAdmin(admin.ModelAdmin):
    list_display = ["game_name", "system_slug", "collection", "position", "is_matched"]
    list_filter = ["system_slug", "collection"]
    search_fields = ["game_name", "collection__name"]
    raw_id_fields = ["collection"]

    def is_matched(self, obj):
        return obj.is_matched

    is_matched.boolean = True
    is_matched.short_description = "In Library"
