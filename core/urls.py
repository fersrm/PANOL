from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path

from UsuarioApp.views import PasswordRecoveryHelpView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Antes de allauth: ningún enlace/token antiguo habilita reset por correo.
    path(
        "accounts/password/reset/",
        PasswordRecoveryHelpView.as_view(),
        name="account_reset_password",
    ),
    path(
        "accounts/password/reset/done/",
        PasswordRecoveryHelpView.as_view(),
        name="account_reset_password_done",
    ),
    path(
        "accounts/password/reset/key/done/",
        PasswordRecoveryHelpView.as_view(),
        name="account_reset_password_from_key_done",
    ),
    re_path(
        r"^accounts/password/reset/key/(?P<uidb36>[0-9A-Za-z]+)-(?P<key>.+)/$",
        PasswordRecoveryHelpView.as_view(),
        name="account_reset_password_from_key",
    ),
    path("accounts/", include("allauth.urls")),
    path("", include("homeApp.urls")),
    path("", include("UsuarioApp.urls")),
    path("panol/", include("PanolApp.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
