# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import errno
import hashlib
import os
from os.path import join, exists, dirname
import re
import requests
import shutil

from PIL import Image as PillowImage

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import migrations

from popolo.importers.popit import PopItImporter, show_data_on_error

PILLOW_FORMAT_EXTENSIONS = {
    'JPEG': 'jpg',
    'PNG': 'png',
    'GIF': 'gif',
    'BMP': 'bmp',
}

CACHE_DIRECTORY = join(dirname(__file__), '.download-cache')

def get_url_cached(url):
    try:
        os.makedirs(CACHE_DIRECTORY)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
    filename = join(CACHE_DIRECTORY, hashlib.md5(url).hexdigest())
    if exists(filename):
        return filename
    else:
        print "\nDownloading {0} ...".format(url)
        with open(filename, 'wb') as f:
            r = requests.get(url, stream=True)
            r.raise_for_status()
            shutil.copyfileobj(r.raw, f)
        print "done"
    return filename

class YNRPopItImporter(PopItImporter):

    person_id_to_json_data = {}

    def __init__(self, apps, schema_editor):
        self.apps = apps
        self.schema_editor = schema_editor
        self.image_storage = FileSystemStorage()

    def get_model_class(self, app_label, model_name):
        return self.apps.get_model(app_label, model_name)

    def get_images_for_object(self, images_data, django_extra_object):
        ContentType = self.get_model_class('contenttypes', 'ContentType')
        person_extra_content_type = ContentType.objects.get_for_model(django_extra_object)

        # Now download and import all the images:
        Image = self.get_model_class('images', 'Image')
        first_image = True
        for image_data in images_data:
            with show_data_on_error('image_data', image_data):
                url = image_data['url']
                try:
                    image_filename = get_url_cached(url)
                except requests.exceptions.HTTPError as e:
                    msg = "Ignoring image URL {url}, with status code {status}"
                    print msg.format(
                        url=url,
                        status=e.response.status_code
                    )
                    continue
                with open(image_filename, 'rb') as f:
                    try:
                        pillow_image = PillowImage.open(f)
                    except IOError as e:
                        if 'cannot identify image file' in unicode(e):
                            print "Ignoring a non-image file {0}".format(
                                image_filename
                            )
                            continue
                        raise
                    extension = PILLOW_FORMAT_EXTENSIONS[pillow_image.format]
                suggested_filename = join(
                    'images', image_data['id'] + '.' + extension
                )
                with open(image_filename, 'rb') as f:
                    storage_filename = self.image_storage.save(
                        suggested_filename, f
                    )
                image_uploaded_by = image_data.get('uploaded_by_user', '')
                image_notes = image_data.get('notes', '')
                source = 'Uploaded by {uploaded_by}: {notes}'.format(
                    uploaded_by=image_uploaded_by,
                    notes=image_notes,
                )
                Image.objects.create(
                    image=storage_filename,
                    source=source,
                    is_primary=first_image,
                    object_id=django_extra_object.id,
                    content_type_id=person_extra_content_type.id
                )
            if first_image:
                first_image = False

    def update_person(self, person_data):
        new_person_data = person_data.copy()
        # There are quite a lot of summary fields in PopIt that are
        # way longer than 1024 characters.
        new_person_data['summary'] = (person_data.get('summary') or '')[:1024]
        # Surprisingly, quite a lot of PopIt email addresses have
        # extraneous whitespace in them, so strip any out to avoid
        # the 'Enter a valid email address' ValidationError on saving:
        email = person_data.get('email') or None
        if email:
            email = re.sub(r'\s*', '', email)
        new_person_data['email'] = email
        person_id, person = super(YNRPopItImporter, self).update_person(
            new_person_data
        )

        self.person_id_to_json_data[person_id] = new_person_data

        # Create the extra person object:
        PersonExtra = self.get_model_class('candidates', 'PersonExtra')
        extra = PersonExtra.objects.create(base=person)

        self.get_images_for_object(person_data['images'], extra)

        return person_id, person

    def update_organization(self, org_data, area):
        org_id, org = super(YNRPopItImporter, self).update_organization(
            org_data, area
        )

        # Create the extra organization object:
        OrganizationExtra = self.get_model_class('candidates', 'OrganizationExtra')
        extra = OrganizationExtra.objects.create(base=org)

        self.get_images_for_object(org_data['images'], extra)

        return org_id, org

    def make_contact_detail_dict(self, contact_detail_data):
        new_contact_detail_data = contact_detail_data.copy()
        # There are some contact types that are used in PopIt that are
        # longer than 12 characters...
        new_contact_detail_data['type'] = contact_detail_data['type'][:12]
        return super(YNRPopItImporter, self).make_contact_detail_dict(new_contact_detail_data)

    def make_link_dict(self, link_data):
        new_link_data = link_data.copy()
        # There are some really long URLs in PopIt, which exceed the
        # 200 character limit in django-popolo.
        new_link_data['url'] = new_link_data['url'][:200]
        return super(YNRPopItImporter, self).make_link_dict(new_link_data)


def import_from_popit(apps, schema_editor):
    importer = YNRPopItImporter(apps, schema_editor)
    url = 'http://{instance}.{hostname}:{port}/api/v0.1/export.json'.format(
        instance=settings.POPIT_INSTANCE,
        hostname=settings.POPIT_HOSTNAME,
        port=settings.POPIT_PORT,
    )
    export_filename = get_url_cached(url)
    importer.import_from_export_json(export_filename)


class Migration(migrations.Migration):

    dependencies = [
        ('candidates', '0008_membershipextra_organizationextra_personextra_postextra'),
        ('images', '0001_initial'),
        ('popolo', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(
            import_from_popit,
            lambda apps, schema_editor: None
        ),
    ]
