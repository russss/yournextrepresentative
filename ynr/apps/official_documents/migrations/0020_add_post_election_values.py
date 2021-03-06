# -*- coding: utf-8 -*-
# Generated by Django 1.9.13 on 2018-04-06 10:23
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


def add_post_elections(apps, schema_editor):
    PostExtraElection = apps.get_model('candidates', 'PostExtraElection')
    OfficialDocument = apps.get_model('official_documents', 'OfficialDocument')

    for doc in OfficialDocument.objects.all().select_related('post__extra'):
        pee = PostExtraElection.objects.get(
            election=doc.election,
            postextra=doc.post.extra
        )
        doc.post_election = pee
        doc.save()


def do_nothing(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('official_documents', '0019_officialdocument_post_election'),
    ]

    operations = [
        migrations.RunPython(add_post_elections, do_nothing),
    ]
