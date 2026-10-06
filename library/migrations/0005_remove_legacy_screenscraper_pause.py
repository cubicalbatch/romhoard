"""Drop the pre-reason ScreenScraper pause setting (replaced by `screenscraper_pause`)."""

from django.db import migrations


def remove_legacy_pause(apps, schema_editor):
    apps.get_model("library", "Setting").objects.filter(
        key="screenscraper_pause_until"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("library", "0004_romset_revision_max_length_100")]

    operations = [migrations.RunPython(remove_legacy_pause, migrations.RunPython.noop)]
