from rest_framework import serializers
from django.utils.translation import gettext as _

from core.serializers import FieldSelectorSerializer
from xforms.mixins.model_helper import ModelHelper

from pyxform.xform2json import XFormToDict


class XFormListSerializer(FieldSelectorSerializer,
                          serializers.Serializer):
    """
    Serves up the /collect/formList/ api requests
    Returns back valid xml for xforms
    """
    formID = serializers.CharField(source='id_string')
    name = serializers.CharField(source='title')
    version = serializers.IntegerField()
    hash = serializers.CharField(source='md5_hash')
    downloadUrl = serializers.SerializerMethodField('get_xml_form')

    def get_xml_form(self, obj):
        if self.context['request'].META['SERVER_PROTOCOL'] == 'HTTP/1.1':
            url = 'http://'
        else:
            url = 'https://'
        url += self.context['request'].META.get('HTTP_HOST', 'localhost:8000')
        return url + obj.xml_form.url


class XFormSubmissionSerializer(FieldSelectorSerializer,
                                serializers.Serializer):
    """
    Serves up the /collect/submissions/ api requests
    Serializes and creates party, spatial, and tunure relationships models
    Stores images in S3 buckets
    Returns number of successful forms submitted
    """
    xml_submission_file = serializers.FileField()

    def create(self, request, *args, **kwargs):
        data = XFormToDict(
            request['xml_submission_file'].read().decode('ascii')
            ).get_dict()
        survey = data[list(data.keys())[0]]
        model_helper = ModelHelper()
        create_models = model_helper.add_data_to_models(survey)

        return {
                'message': _("Successful submission."),
                'formid': list(data.keys())[0],
                'instanceID': survey['meta']['instanceID'],
                'project': create_models['project'],
                'location_photo': survey['location_photo'],
                'location': create_models['location'].id,
                'party_photo': survey['party_photo'],
                'party': create_models['party'].id
            }