"""Migration to change collection URLs to creator/slug pattern.

Changes:
- Sets creator='local' for any existing collections with empty creator
- Changes creator from CharField to SlugField (required)
- Removes unique constraint from slug
- Adds unique_together constraint for (creator, slug)
"""

from django.db import migrations, models


def set_default_creator(apps, schema_editor):
    """Set creator='local' for collections with empty creator and slugify existing creators."""
    Collection = apps.get_model("romcollections", "Collection")
    # First, handle empty creators
    Collection.objects.filter(creator="").update(creator="local")
    # Then slugify existing non-slug creators (e.g., "RomHoard Research" -> "romhoard-research")
    for collection in Collection.objects.all():
        if (
            collection.creator
            and not collection.creator.islower()
            or " " in collection.creator
            or any(c.isupper() for c in collection.creator)
        ):
            # This is not a valid slug, convert it
            slugified = collection.creator.lower().replace(" ", "-").replace("_", "-")
            # Remove any non-slug characters
            import re

            slugified = re.sub(r"[^a-z0-9-]", "", slugified)
            collection.creator = slugified
            collection.save(update_fields=["creator"])


def reverse_default_creator(apps, schema_editor):
    """No-op reverse - we don't want to blank out creators."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("romcollections", "0003_create_favorites"),
    ]

    operations = [
        # First, set default creator for any existing collections
        migrations.RunPython(set_default_creator, reverse_default_creator),
        # Remove unique from slug, change to non-unique SlugField
        migrations.AlterField(
            model_name="collection",
            name="slug",
            field=models.SlugField(),
        ),
        # Change creator to required SlugField
        migrations.AlterField(
            model_name="collection",
            name="creator",
            field=models.SlugField(max_length=255),
        ),
        # Add unique_together constraint
        migrations.AlterUniqueTogether(
            name="collection",
            unique_together={("creator", "slug")},
        ),
    ]
