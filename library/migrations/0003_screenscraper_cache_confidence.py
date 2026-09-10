from django.db import migrations, models
from django.db.models import Q


def invalidate_untrusted_entries(apps, schema_editor):
    cache = apps.get_model("library", "ScreenScraperLookupCache")
    cache.objects.filter(Q(lookup_type="name") | Q(matched=False)).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("library", "0002_add_switch_content_type_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="screenscraperlookupcache",
            name="confidence",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="screenscraperlookupcache",
            name="matched_system_id",
            field=models.IntegerField(
                blank=True,
                help_text="ScreenScraper system ID the match actually belongs to",
                null=True,
            ),
        ),
        migrations.RunPython(invalidate_untrusted_entries, migrations.RunPython.noop),
    ]
