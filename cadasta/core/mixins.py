from collections import OrderedDict

from accounts.models import PublicRole
from collections.abc import Sequence
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import mixins as auth_mixins
from django.contrib.auth.views import redirect_to_login
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.core.urlresolvers import reverse
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from jsonattrs.models import Schema, compose_schemas
from organization.models import OrganizationRole
from tutelary import mixins

from .roles import AnonymousUserRole, SuperUserRole


# tutelary
def update_permissions(permission, obj=None):
    def set_permissions(self, request, view=None):
        if (hasattr(self, 'get_organization') and
                self.get_organization().archived):
            return False
        if (hasattr(self, 'get_project') and self.get_project().archived):
            return False
        if obj and self.get_object().archived:
            return False
        return permission
    return set_permissions


# role based
def update_role_permissions(permission, obj=None):
    def set_permissions(self, request):
        if (hasattr(self, 'get_organization') and
                self.get_organization().archived):
            return ''
        if (hasattr(self, 'get_project') and self.get_project().archived):
            return ''
        if obj and self.get_object().archived:
            return ''
        return permission
    return set_permissions


# role based object permission check
def check_perms(user, actions, objs, method=None):
    if actions is False:
        return False
    if actions is not None:
        for a in actions:
            for o in objs:
                test_obj = None
                if o is not None:
                    test_obj = o.get_permissions_object(a)
                if not user.has_perm(a, test_obj):
                    return False
    return True


# Tutelary mixin
class PermissionRequiredMixin(mixins.PermissionRequiredMixin):

    def handle_no_permission(self):
        msg = super().handle_no_permission()
        messages.add_message(self.request, messages.WARNING,
                             msg[0] if len(msg) > 0 and len(msg[0]) > 0
                             else _("You don't have permission "
                                    "for this action."))

        referer = self.request.META.get('HTTP_REFERER')
        redirect_url = self.request.META.get('HTTP_REFERER', '/')

        if (referer and '/account/login/' in referer and
                not self.request.user.is_anonymous):

            if 'organization' in self.kwargs and 'project' in self.kwargs:
                redirect_url = reverse(
                    'organization:project-dashboard',
                    kwargs={'organization': self.kwargs['organization'],
                            'project': self.kwargs['project']}
                )
                if redirect_url == self.request.get_full_path():
                    redirect_url = reverse(
                        'organization:dashboard',
                        kwargs={'slug': self.kwargs['organization']}
                    )

            elif 'slug' in self.kwargs:
                redirect_url = reverse(
                    'organization:dashboard',
                    kwargs={'slug': self.kwargs['slug']}
                )
                if redirect_url == self.request.get_full_path():
                    redirect_url = reverse('core:dashboard')

        return redirect(redirect_url)


# Tutelary mixin
class LoginPermissionRequiredMixin(PermissionRequiredMixin,
                                   mixins.LoginPermissionRequiredMixin):
    pass


# Role permission mixin
class BaseRolePermissionMixin():

    def get_permission_required(self):
        if (not hasattr(self, 'permission_required') or
           self.permission_required is None):
            raise ImproperlyConfigured(
                '{0} is missing the permission_required attribute. Define '
                '{0}.permission_required, or override '
                '{0}.get_permission_required().'.format(
                    self.__class__.__name__)
            )

        perms = self.permission_required

        # permission_required defined as a dict with either
        # strings or methods as dict values
        if isinstance(self.permission_required, dict):
            perms = self.permission_required.get(self.request.method, ())
            if callable(perms) and hasattr(self, perms.__name__):
                perms = getattr(self, perms.__name__)

        # method assigned directly to permission_required
        if callable(perms):
            perms = perms(self.request)

        # string assigned directly to permission_required
        if isinstance(perms, str):
            perms = (perms, )

        return perms

    def get_queryset(self):
        if hasattr(self, 'filtered_queryset'):
            return self.filtered_queryset
        else:
            return super().get_queryset()

    def perms_filter_queryset(self, objs):
        actions = self.get_permission_required()
        if isinstance(self.permission_filter_queryset, Sequence):
            actions += tuple(self.permission_filter_queryset)

        # we dont want to check each
        # we just need to build a query based on the permissions
        def check_each(obj):
            check_actions = actions
            if callable(self.permission_filter_queryset):
                check_actions += self.permission_filter_queryset(self, obj)
            import pytest
            pytest.set_trace()
            return check_perms(self.request.user, check_actions,
                               [obj], self.request.method)

        filtered_pks = [o.pk for o in filter(check_each, objs)]
        self.filtered_queryset = self.get_queryset().filter(
            pk__in=filtered_pks
        )

    def get_user_roles(self):
        """Set user roles."""
        self._roles = []
        user = self.request.user
        # check for anonymous and su roles
        if user.is_anonymous:
            self._roles.append(AnonymousUserRole())
            return
        elif user.is_superuser:
            self._roles.append(SuperUserRole())
            return
        # for org mixins get the org role
        if hasattr(self, 'get_organization'):
            try:
                role = OrganizationRole.objects.get(
                    organization=self.get_organization(),
                    user=self.request.user,
                )
                self._roles.append(role)
            except OrganizationRole.DoesNotExist:
                pass
        # for project mixins get org and project roles
        if hasattr(self, 'get_org_role') and self.get_org_role():
            if self._org_role not in self._roles:
                self._roles.append(self._org_role)
        if hasattr(self, 'get_prj_role') and self.get_prj_role():
            self._roles.append(self._prj_role)
        # get the default public role
        try:
            role = PublicRole.objects.get(user=self.request.user)
            self._roles.append(role)
        except PublicRole.DoesNotExist:
            pass

    @property
    def permissions(self):
        # compose permissions for all roles
        self._perms = []
        if hasattr(self, '_roles'):
            [self._perms.extend(role.permissions) for role in self._roles]
        perms = sorted(set(self._perms))
        return perms


# Role permission mixin
class RolePermissionRequiredMixin(BaseRolePermissionMixin,
                                  auth_mixins.PermissionRequiredMixin):
    def has_permission(self):
        if not hasattr(self, '_roles'):
            self.get_user_roles()
        # # superusers have all permissions
        # if hasattr(self, 'is_superuser'):
        #     if self.is_superuser:
        #         return True
        # else:
        #     if self.request.user.is_superuser:
        #         return True
        perms = self.get_permission_required()
        objs = self.get_queryset()
        if (hasattr(self, 'permission_filter_queryset') and
           self.permission_filter_queryset is not False and
           self.request.method == 'GET'):
            if objs != [None]:
                self.perms_filter_queryset(objs)
            return True

        # replace when we eventually use role authorization backend
        # user = self.request.user
        # return all(False for perm in perms
        #            if not user.has_perm(perm, obj=self.permissions))

        return all(False for perm in perms
                   if perm not in self.permissions)

    def handle_no_permission(self):
        msg = _("You don't have permission to perform this action.")
        if hasattr(self, 'permission_denied_message'):
            msg = self.get_permission_denied_message()
        messages.add_message(
            self.request, messages.WARNING, msg)

        referer = self.request.META.get('HTTP_REFERER')
        redirect_url = self.request.META.get('HTTP_REFERER', '/')

        if (referer and '/account/login/' in referer and
                not self.request.user.is_anonymous):

            if 'organization' in self.kwargs and 'project' in self.kwargs:
                redirect_url = reverse(
                    'organization:project-dashboard',
                    kwargs={'organization': self.kwargs['organization'],
                            'project': self.kwargs['project']}
                )
                if redirect_url == self.request.get_full_path():
                    redirect_url = reverse(
                        'organization:dashboard',
                        kwargs={'slug': self.kwargs['organization']}
                    )

            elif 'slug' in self.kwargs:
                redirect_url = reverse(
                    'organization:dashboard',
                    kwargs={'slug': self.kwargs['slug']}
                )
                if redirect_url == self.request.get_full_path():
                    redirect_url = reverse('core:dashboard')

        return redirect(redirect_url)


# Role permission mixin
class RoleLoginPermissionRequiredMixin(RolePermissionRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated():
            if hasattr(self, 'raise_exception') and self.raise_exception:
                raise PermissionDenied(self.get_permission_denied_message())
            return redirect_to_login(self.request.get_full_path(),
                                     self.get_login_url(),
                                     self.get_redirect_field_name())

        return super().dispatch(request, *args, **kwargs)


# Role permission mixin
class APIPermissionRequiredMixin(BaseRolePermissionMixin):

    def get_permission_denied_message(self, default=None):
        if hasattr(self, 'permission_denied_message'):
            return (self.permission_denied_message,)

    def check_permissions(self, request):
        if not hasattr(self, '_roles'):
            self.get_user_roles()
        # # superusers have all permissions
        # if hasattr(self, 'is_superuser'):
        #     if self.is_superuser:
        #         return True
        # else:
        #     if self.request.user.is_superuser:
        #         return True
        perms = self.get_permission_required()

        has_perm = all(False for perm in perms
                       if perm not in self.permissions)
        if not has_perm:
            self.permission_denied(request)


class SchemaSelectorMixin():

    def get_attributes(self, project):
        content_type_to_selectors = self._get_content_types_to_selectors()

        attributes_for_models = {}
        for content_type, selector_fields in content_type_to_selectors.items():
            label = '{}.{}'.format(content_type.app_label, content_type.model)
            model = content_type.model_class()
            choices = []
            selectors = OrderedDict({})
            attributes_for_models[label] = OrderedDict({})

            for selector_field in selector_fields:
                field = model._meta.get_field(selector_field.partition('.')[0])
                if field.choices:
                    choices = [choice[0] for choice in field.choices]
                else:
                    selector = project
                    sf = selector_field.partition('.')[-1]
                    sf = sf.replace('.pk', '_id')
                    selector = getattr(selector, sf, None)
                    if selector:
                        selectors[sf] = selector

            if selectors and not choices:
                defaults = list(selectors.values())
                schemas = Schema.objects.lookup(
                    content_type=content_type, selectors=defaults)
                if schemas:
                    attributes, _, _ = compose_schemas(*schemas)
                    attributes_for_models[label]['DEFAULT'] = attributes

            if selectors and choices:
                for choice in choices:
                    conditionals = list(selectors.values()) + [choice]
                    schemas = Schema.objects.lookup(
                        content_type=content_type,
                        selectors=conditionals)
                    if schemas:
                        attributes, _, _ = compose_schemas(*schemas)
                        attributes_for_models[label][choice] = attributes

        return attributes_for_models

    def get_model_attributes(self, project, content_type):
        attributes_for_models = self.get_attributes(project)
        return attributes_for_models[content_type]

    def get_conditional_selector(self, content_type):
        content_type_to_selectors = self._get_content_types_to_selectors()
        selectors = list(content_type_to_selectors[content_type])
        if '.' in selectors[-1]:
            return None
        else:
            return selectors[-1]

    def _get_content_types_to_selectors(self):
        content_type_to_selectors = dict()
        for k, v in settings.JSONATTRS_SCHEMA_SELECTORS.items():
            a, m = k.split('.')
            content_type_to_selectors[
                ContentType.objects.get(app_label=a, model=m)
            ] = v
        return content_type_to_selectors
