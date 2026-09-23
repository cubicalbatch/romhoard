from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("library", "0003_screenscraper_cache_confidence"),
    ]

    operations = [
        migrations.AlterField(
            model_name="romset",
            name="revision",
            field=models.CharField(max_length=100, blank=True),
        ),
    ]
