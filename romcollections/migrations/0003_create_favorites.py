from django.db import migrations


def create_favorites_collection(apps, schema_editor):
    Collection = apps.get_model("romcollections", "Collection")
    Collection.objects.get_or_create(
        is_favorites=True,
        defaults={
            "slug": "favorites",
            "name": "Favorites",
            "is_community": False,
            "is_public": False,
        },
    )


def delete_favorites_collection(apps, schema_editor):
    Collection = apps.get_model("romcollections", "Collection")
    Collection.objects.filter(is_favorites=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("romcollections", "0002_collection_romcollecti_is_publ_dcd6a8_idx_and_more"),
    ]

    operations = [
        migrations.RunPython(create_favorites_collection, delete_favorites_collection),
    ]
