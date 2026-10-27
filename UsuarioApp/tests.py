import sys
from io import BytesIO
from unittest.mock import patch

from allauth.account.adapter import get_adapter
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.mail import send_mail
from django.db import (
    IntegrityError,
    OperationalError,
    ProgrammingError,
    connection,
    transaction,
)
from django.db.migrations.executor import MigrationExecutor
from django.test import (
    Client,
    RequestFactory,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.urls import reverse
from PIL import Image

from core.context_processors import permisos_navegacion, preferencias_visuales
from core.mixins import usuario_tiene_cargo
from UsuarioApp.adapters import NoSignupAccountAdapter
from UsuarioApp.models import Position, Profile, UserVisualPreference
from UsuarioApp.views import (
    AdministrativePasswordView,
    UserActivateView,
    UserDeactivateView,
)

PASSWORD = "CalidadBase-2026-Segura!"
NEW_PASSWORD = "Renovada-2026-Fuerte!"


class BaseUsuariosTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # El formulario excluye pk=1; no se atribuye un cargo a ese PK.
        cls.excluded_position = Position.objects.create(
            user_position="Cargo excluido", permission_code="RESTRICTED"
        )
        cls.admin_position = Position.objects.create(
            user_position="Administrador", permission_code="ADMIN"
        )
        cls.manager_position = Position.objects.create(
            user_position="Gestor", permission_code="MANAGER"
        )
        cls.restricted_position = Position.objects.create(
            user_position="Restringido", permission_code="RESTRICTED"
        )

    @classmethod
    def create_actor(
        cls,
        username,
        *,
        position=None,
        superuser=False,
        active=True,
        staff=False,
        with_profile=True,
    ):
        user = User.objects.create_user(
            username=username,
            password=PASSWORD,
            is_active=active,
            is_staff=staff or superuser,
            is_superuser=superuser,
        )
        if with_profile:
            Profile.objects.create(user_FK=user, position_FK=position)
        return user

    def valid_payload(self, suffix="nuevo", position=None):
        return {
            "username": f"usuario_{suffix}",
            "email": f"{suffix}@example.test",
            "first_name": "Nombre",
            "last_name": "Prueba",
            "password1": PASSWORD,
            "password2": PASSWORD,
            "position_FK": str((position or self.manager_position).pk),
        }


class AdministracionUsuariosTests(BaseUsuariosTestCase):
    def setUp(self):
        self.admin = self.create_actor("operador", position=self.admin_position)
        self.target = self.create_actor(
            "destinatario", position=self.restricted_position
        )
        self.client.force_login(self.admin)
        self.url = reverse("UserPasswordReset", args=[self.target.pk])

    def payload(self, **changes):
        return {
            "admin_password": PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
            **changes,
        }

    def test_matriz_por_destinatario_y_controles_coinciden(self):
        unknown = Position.objects.create(
            user_position="Cargo desconocido", permission_code="OTHER"
        )
        actors = [
            (self.admin, "ADMIN"),
            (self.create_actor("gestor", position=self.manager_position), "MANAGER"),
            (
                self.create_actor("limitado", position=self.restricted_position),
                "RESTRICTED",
            ),
            (self.create_actor("sinperfil", with_profile=False), None),
            (self.create_actor("sincargo"), None),
            (self.create_actor("staff", staff=True), None),
            (self.create_actor("desconocido", position=unknown), None),
            (
                self.create_actor("root_sinperfil", superuser=True, with_profile=False),
                None,
            ),
            (
                self.create_actor(
                    "root_limitado", superuser=True, position=self.restricted_position
                ),
                "RESTRICTED",
            ),
            (
                self.create_actor(
                    "root_gestor", superuser=True, position=self.manager_position
                ),
                "MANAGER",
            ),
            (
                self.create_actor(
                    "root_admin", superuser=True, position=self.admin_position
                ),
                "ADMIN",
            ),
        ]
        targets = [
            (self.target, "RESTRICTED"),
            (
                self.create_actor("objetivo_gestor", position=self.manager_position),
                "MANAGER",
            ),
            (self.create_actor("objetivo_admin", position=self.admin_position), None),
            (
                self.create_actor(
                    "objetivo_root", superuser=True, position=self.restricted_position
                ),
                None,
            ),
            (self.create_actor("objetivo_sinperfil", with_profile=False), None),
            (self.create_actor("objetivo_sincargo"), None),
            (self.create_actor("objetivo_desconocido", position=unknown), None),
        ]
        for actor, role in actors:
            self.client.force_login(actor)
            for target, target_role in targets + [(actor, None)]:
                for route, action, starting_active in (
                    ("UserDeactivate", "deactivate", True),
                    ("UserActivate", "activate", False),
                    ("UserPasswordReset", "reset", True),
                ):
                    with self.subTest(
                        actor=actor.username, target=target.username, action=action
                    ):
                        # Propio actor permanece activo; siempre es destinatario protegido.
                        actual_start = (
                            True if actor.pk == target.pk else starting_active
                        )
                        User.objects.filter(pk=target.pk).update(is_active=actual_start)
                        target.refresh_from_db()
                        before_hash = target.password
                        allowed = (
                            role == "ADMIN" and target_role in ("MANAGER", "RESTRICTED")
                        ) or (
                            role == "MANAGER"
                            and target_role == "RESTRICTED"
                            and action != "reset"
                        )
                        url = reverse(route, args=[target.pk])
                        listing = self.client.get(
                            reverse("User"),
                            {
                                "search": target.username,
                                "inactive": "0" if actual_start else "1",
                            },
                        )
                        marker = (
                            f'data-open-dialog="deactivate-{target.pk}"'
                            if action == "deactivate"
                            else f'action="{url}"'
                            if action == "activate"
                            else f'href="{url}"'
                        )
                        if allowed:
                            self.assertContains(listing, marker)
                        else:
                            self.assertNotContains(listing, marker)
                        get = self.client.get(url)
                        # GET reset verifica destinatario; GET de estado solo verifica actor.
                        expected_get = (
                            (200 if allowed else 403)
                            if action == "reset"
                            else (405 if role in ("ADMIN", "MANAGER") else 403)
                        )
                        self.assertEqual(get.status_code, expected_get)
                        result = self.client.post(
                            url, self.payload() if action == "reset" else {}
                        )
                        self.assertEqual(result.status_code, 302 if allowed else 403)
                        target.refresh_from_db()
                        self.assertEqual(
                            target.is_active,
                            action == "activate"
                            if allowed and action != "reset"
                            else actual_start,
                        )
                        if action == "reset" and allowed:
                            self.assertTrue(target.check_password(NEW_PASSWORD))
                        else:
                            self.assertEqual(target.password, before_hash)
                        User.objects.filter(pk=target.pk).update(is_active=True)

    def test_anonimo_e_inactivo_no_escriben(self):
        self.client.logout()
        before = self.target.password
        for route, view in (
            ("UserPasswordReset", AdministrativePasswordView),
            ("UserActivate", UserActivateView),
            ("UserDeactivate", UserDeactivateView),
        ):
            url = reverse(route, args=[self.target.pk])
            for method in (self.client.get, self.client.post):
                self.assertEqual(method(url).status_code, 302)
            # RequestFactory comprueba también la barrera de actividad si otro backend autentica inactivos.
            User.objects.filter(pk=self.admin.pk).update(is_active=False)
            request = RequestFactory().post(url, self.payload())
            request.user = self.admin
            with self.assertRaises(PermissionDenied):
                view.as_view()(request, pk=self.target.pk)
            User.objects.filter(pk=self.admin.pk).update(is_active=True)
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_active)
        self.assertEqual(self.target.password, before)

    def test_ids_inexistentes_y_manipulados_no_eluden_autorizacion(self):
        for route in ("UserPasswordReset", "UserActivate", "UserDeactivate"):
            self.assertEqual(
                self.client.post(
                    reverse(route, args=[999999]), self.payload()
                ).status_code,
                404,
            )
        self.assertEqual(
            self.client.get(reverse("UserPasswordReset", args=[999999])).status_code,
            404,
        )
        restricted = self.create_actor(
            "sinprivilegios", position=self.restricted_position
        )
        self.client.force_login(restricted)
        for route in ("UserPasswordReset", "UserActivate", "UserDeactivate"):
            self.assertEqual(
                self.client.post(
                    reverse(route, args=[999999]), self.payload()
                ).status_code,
                403,
            )
        manager = self.create_actor("gestor_manipula", position=self.manager_position)
        self.client.force_login(manager)
        result = self.client.post(
            self.url, self.payload(actor=self.admin.pk, user=self.admin.pk)
        )
        self.assertEqual(result.status_code, 403)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password(PASSWORD))

    def test_csrf_requerido_y_formulario_no_escribe_en_get(self):
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.admin)
        before = self.target.password
        form = secure.get(self.url)
        self.assertEqual(form.status_code, 200)
        self.assertIn("no-store", form["Cache-Control"])
        self.assertContains(form, 'autocomplete="current-password"')
        self.assertContains(form, 'autocomplete="new-password"', count=2)
        self.target.refresh_from_db()
        self.assertEqual(self.target.password, before)
        for route in ("UserPasswordReset", "UserActivate", "UserDeactivate"):
            self.assertEqual(
                secure.post(
                    reverse(route, args=[self.target.pk]), self.payload()
                ).status_code,
                403,
            )
        csrf = secure.cookies["csrftoken"].value
        result = secure.post(self.url, self.payload(csrfmiddlewaretoken=csrf))
        self.assertEqual(result.status_code, 302)
        for route in ("UserDeactivate", "UserActivate"):
            self.assertEqual(
                secure.post(
                    reverse(route, args=[self.target.pk]), {"csrfmiddlewaretoken": csrf}
                ).status_code,
                302,
            )

    def test_restablece_inferiores_sin_email_ni_llamadas_a_correo(self):
        for position in (self.manager_position, self.restricted_position):
            target = self.create_actor(f"sin_correo_{position.pk}", position=position)
            with (
                self.subTest(position=position.permission_code),
                patch(
                    "django.core.mail.backends.locmem.EmailBackend.send_messages",
                    side_effect=AssertionError("No debe enviar correo"),
                ),
            ):
                self.assertEqual(target.email, "")
                response = self.client.post(
                    reverse("UserPasswordReset", args=[target.pk]), self.payload()
                )
                self.assertEqual(response.status_code, 302)
                target.refresh_from_db()
                self.assertTrue(target.check_password(NEW_PASSWORD))
                self.assertFalse(target.check_password(PASSWORD))
                self.assertNotIn(NEW_PASSWORD, response.get("Location", ""))
        self.assertEqual(len(mail.outbox), 0)

    def test_campos_invalidos_no_guardan_ni_revelan_secretos(self):
        cases = [
            ({}, "admin_password"),
            ({"admin_password": PASSWORD}, "new_password1"),
            (self.payload(new_password1=""), "new_password1"),
            (self.payload(new_password2=""), "new_password2"),
            (
                self.payload(admin_password="Operador-Incorrecto-2026!"),
                "admin_password",
            ),
            (self.payload(new_password2="No-Coincide-2026!"), "new_password2"),
            (
                self.payload(new_password1="12345678", new_password2="12345678"),
                "new_password2",
            ),
            (
                self.payload(new_password1="password", new_password2="password"),
                "new_password2",
            ),
            (self.payload(new_password1="abc", new_password2="abc"), "new_password2"),
            (
                self.payload(
                    new_password1="destinatario", new_password2="destinatario"
                ),
                "new_password2",
            ),
        ]
        for payload, field in cases:
            with self.subTest(field=field, case=len(payload)):
                before = self.target.password
                result = self.client.post(self.url, payload)
                self.assertEqual(result.status_code, 200)
                self.assertIn(field, result.context["form"].errors)
                self.assertIn("no-store", result["Cache-Control"])
                self.assertNotContains(result, 'value="' + PASSWORD + '"')
                for value in payload.values():
                    self.assertNotContains(result, 'value="' + str(value) + '"')
                self.target.refresh_from_db()
                self.assertEqual(self.target.password, before)
        self.admin.set_unusable_password()
        self.admin.save(update_fields=["password"])
        self.client.force_login(self.admin)
        result = self.client.post(self.url, self.payload())
        self.assertIn("admin_password", result.context["form"].errors)

    def test_invalida_dos_sesiones_del_destinatario_y_conserva_operador(self):
        sessions = [Client(), Client()]
        for browser in sessions:
            browser.force_login(self.target)
            self.assertEqual(browser.get(reverse("Home")).status_code, 200)
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 302)
        for browser in sessions:
            self.assertEqual(browser.get(reverse("Home")).status_code, 302)
            self.assertNotIn("_auth_user_id", browser.session)
        self.assertEqual(self.client.get(reverse("Home")).status_code, 200)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.admin.pk)
        self.assertFalse(
            sessions[0].login(username=self.target.username, password=PASSWORD)
        )
        self.assertTrue(
            sessions[0].login(username=self.target.username, password=NEW_PASSWORD)
        )

    def test_confirmacion_conserva_espacios_de_la_contrasena_del_operador(self):
        password_with_spaces = "  " + PASSWORD + "  "
        self.admin.set_password(password_with_spaces)
        self.admin.save(update_fields=["password"])
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.post(
                self.url, self.payload(admin_password=password_with_spaces)
            ).status_code,
            302,
        )
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password(NEW_PASSWORD))

    def test_preserva_datos_mfa_preferencia_y_estado_inactivo(self):
        from allauth.mfa.models import Authenticator

        self.target.is_active = False
        self.target.email = "conservado@example.test"
        self.target.first_name = "Nombre conservado"
        self.target.is_staff = True
        self.target.save()
        UserVisualPreference.objects.create(user=self.target, mode="dark")
        EmailAddress.objects.create(
            user=self.target, email=self.target.email, verified=True
        )
        before_user = User.objects.filter(pk=self.target.pk).values().get()
        before_profile = Profile.objects.filter(user_FK=self.target).values().get()
        auth = Authenticator.objects.create(
            user=self.target, type=Authenticator.Type.TOTP, data={"secret": "synthetic"}
        )
        before_mfa = Authenticator.objects.filter(pk=auth.pk).values().get()
        with (
            patch.object(
                Profile, "save", side_effect=AssertionError("No guardar perfil")
            ),
            patch.object(
                UserVisualPreference,
                "save",
                side_effect=AssertionError("No guardar preferencia"),
            ),
        ):
            self.assertEqual(
                self.client.post(self.url, self.payload()).status_code, 302
            )
        after_user = User.objects.filter(pk=self.target.pk).values().get()
        self.assertNotEqual(before_user.pop("password"), after_user.pop("password"))
        self.assertEqual(before_user, after_user)
        self.assertEqual(
            before_profile, Profile.objects.filter(user_FK=self.target).values().get()
        )
        self.assertEqual(
            before_mfa, Authenticator.objects.filter(pk=auth.pk).values().get()
        )
        self.assertEqual(
            UserVisualPreference.objects.get(user=self.target).mode, "dark"
        )
        self.assertTrue(EmailAddress.objects.get(user=self.target).verified)

    def test_login_nuevo_conserva_desafio_mfa(self):
        from allauth.mfa.models import Authenticator
        from allauth.mfa.totp.internal import auth as totp

        browser = Client()
        browser.post(
            reverse("account_login"),
            {"login": self.target.username, "password": PASSWORD},
        )
        setup = browser.get(reverse("mfa_activate_totp"))
        secret = setup.context["form"].secret
        code = totp.format_hotp_value(
            totp.hotp_value(secret, next(totp.yield_hotp_counters_from_time()))
        )
        self.assertEqual(
            browser.post(reverse("mfa_activate_totp"), {"code": code}).status_code, 302
        )
        before = list(Authenticator.objects.filter(user=self.target).values())
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 302)
        browser.get(reverse("Home"))
        result = browser.post(
            reverse("account_login"),
            {"login": self.target.username, "password": NEW_PASSWORD},
        )
        self.assertEqual(result.status_code, 302)
        self.assertNotIn("_auth_user_id", browser.session)
        self.assertEqual(
            list(Authenticator.objects.filter(user=self.target).values()), before
        )
        code = totp.format_hotp_value(
            totp.hotp_value(secret, next(totp.yield_hotp_counters_from_time()))
        )
        self.assertEqual(
            browser.post(reverse("mfa_authenticate"), {"code": code}).status_code, 302
        )
        self.assertIn("_auth_user_id", browser.session)

    def test_permisos_actualizados_entre_get_y_post(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        Profile.objects.filter(user_FK=self.target).update(
            position_FK=self.admin_position
        )
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 403)
        Profile.objects.filter(user_FK=self.target).update(
            position_FK=self.restricted_position
        )
        Profile.objects.filter(user_FK=self.admin).update(
            position_FK=self.manager_position
        )
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 403)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password(PASSWORD))

    def test_fallos_de_escritura_no_cambian_datos_ni_muestran_exito(self):
        original_save = User.save
        for error in (IntegrityError("simulado"), OperationalError("simulado")):
            for route in ("UserPasswordReset", "UserActivate", "UserDeactivate"):
                with self.subTest(route=route, error=type(error).__name__):
                    before = User.objects.filter(pk=self.target.pk).values().get()

                    def write_then_fail(instance, *args, **kwargs):
                        original_save(instance, *args, **kwargs)
                        raise error

                    with patch.object(User, "save", new=write_then_fail):
                        result = self.client.post(
                            reverse(route, args=[self.target.pk]), self.payload()
                        )
                    self.assertEqual(result.status_code, 500)
                    self.assertContains(result, "Volver al listado", status_code=500)
                    self.assertNotContains(result, NEW_PASSWORD, status_code=500)
                    self.assertNotContains(
                        result, "Usuario desactivado correctamente", status_code=500
                    )
                    self.assertNotContains(
                        result, "Contraseña restablecida.", status_code=500
                    )
                    self.assertEqual(
                        User.objects.filter(pk=self.target.pk).values().get(), before
                    )

    def test_error_inesperado_no_se_oculta_y_post_se_marca_sensible(self):
        with patch.object(User, "save", side_effect=ProgrammingError("simulado")):
            with self.assertRaises(ProgrammingError):
                self.client.post(self.url, self.payload())
        result = self.client.post(
            self.url, self.payload(admin_password="Incorrecta-Sintetica!")
        )
        self.assertEqual(
            set(result.wsgi_request.sensitive_post_parameters),
            {"admin_password", "new_password1", "new_password2"},
        )

    def test_estado_idempotente_preserva_otros_atributos(self):
        before = User.objects.filter(pk=self.target.pk).values().get()
        before.pop("is_active")
        for route, active in (("UserDeactivate", False), ("UserActivate", True)):
            for _ in range(2):
                self.assertEqual(
                    self.client.post(reverse(route, args=[self.target.pk])).status_code,
                    302,
                )
                values = User.objects.filter(pk=self.target.pk).values().get()
                self.assertEqual(values.pop("is_active"), active)
                self.assertEqual(values, before)


class ConfiguracionAisladaTests(TestCase):
    def test_recursos_de_prueba_estan_aislados(self):
        database = settings.DATABASES["default"]
        self.assertEqual(database["TEST"]["NAME"], ":memory:")
        self.assertIn("memory", str(database["NAME"]))
        self.assertEqual(
            settings.EMAIL_BACKEND,
            "django.core.mail.backends.locmem.EmailBackend",
        )
        self.assertEqual(
            settings.PASSWORD_HASHERS,
            ["django.contrib.auth.hashers.MD5PasswordHasher"],
        )
        self.assertFalse(settings.SECURE_SSL_REDIRECT)
        self.assertFalse(settings.SESSION_COOKIE_SECURE)
        self.assertFalse(settings.CSRF_COOKIE_SECURE)
        self.assertEqual(settings.SECURE_HSTS_SECONDS, 0)
        self.assertIn("testserver", settings.ALLOWED_HOSTS)
        self.assertNotEqual(settings.MEDIA_ROOT, settings.BASE_DIR / "media")
        self.assertNotIn("core.settings", sys.modules)

    def test_correo_se_conserva_en_memoria(self):
        send_mail(
            "Prueba", "Contenido", "origen@example.test", ["destino@example.test"]
        )
        self.assertEqual(len(mail.outbox), 1)


class RegistroPublicoTests(BaseUsuariosTestCase):
    def test_adapter_configurado_cierra_registro(self):
        adapter = get_adapter()
        self.assertIsInstance(adapter, NoSignupAccountAdapter)
        self.assertFalse(adapter.is_open_for_signup(None))

    def test_get_signup_muestra_registro_cerrado_sin_crear_datos(self):
        counts = (
            User.objects.count(),
            Profile.objects.count(),
            EmailAddress.objects.count(),
        )
        response = self.client.get("/accounts/signup/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/signup_closed.html")
        self.assertContains(response, "Registro Cerrado")
        self.assertEqual(
            (
                User.objects.count(),
                Profile.objects.count(),
                EmailAddress.objects.count(),
            ),
            counts,
        )

    def test_post_signup_no_crea_cuenta(self):
        counts = (
            User.objects.count(),
            Profile.objects.count(),
            EmailAddress.objects.count(),
        )
        response = self.client.post(
            "/accounts/signup/",
            {
                "username": "publico",
                "email": "publico@example.test",
                "password1": PASSWORD,
                "password2": PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/signup_closed.html")
        self.assertEqual(
            (
                User.objects.count(),
                Profile.objects.count(),
                EmailAddress.objects.count(),
            ),
            counts,
        )


class PermisosRegistroInternoTests(BaseUsuariosTestCase):
    def setUp(self):
        self.url = reverse("Register")

    def assert_denied_without_creation(self, actor, expected_target):
        before = (User.objects.count(), Profile.objects.count())
        if actor is not None:
            self.client.force_login(actor)
        response = self.client.post(
            self.url, self.valid_payload(actor.username if actor else "anon")
        )
        self.assertRedirects(response, expected_target, fetch_redirect_response=False)
        self.assertEqual((User.objects.count(), Profile.objects.count()), before)
        self.client.logout()

    def test_anonimo_no_accede_por_get_ni_post(self):
        login = f"{reverse('account_login')}?next={self.url}"
        self.assertRedirects(
            self.client.get(self.url), login, fetch_redirect_response=False
        )
        self.assert_denied_without_creation(None, login)

    def test_restricted_sin_perfil_sin_cargo_y_staff_no_acceden(self):
        actors = [
            self.create_actor("restricted", position=self.restricted_position),
            self.create_actor("sin_perfil", with_profile=False),
            self.create_actor("sin_cargo", position=None),
            self.create_actor("solo_staff", position=None, staff=True),
        ]
        for actor in actors:
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                self.assertRedirects(
                    self.client.get(self.url),
                    reverse("Home"),
                    fetch_redirect_response=False,
                )
                self.client.logout()
                self.assert_denied_without_creation(actor, reverse("Home"))

    def test_admin_manager_y_superusuario_pueden_acceder(self):
        actors = [
            self.create_actor("admin", position=self.admin_position),
            self.create_actor("manager", position=self.manager_position),
            self.create_actor("root", superuser=True, position=self.admin_position),
        ]
        for actor in actors:
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(
                    response, "pages/UsuarioApp/registro_usuario.html"
                )
                self.client.logout()

    def test_usuario_inactivo_es_rechazado_por_politica_y_por_sesion(self):
        inactive = self.create_actor(
            "inactive_admin", position=self.admin_position, active=False
        )
        inactive_root = self.create_actor(
            "inactive_root", superuser=True, active=False, with_profile=False
        )
        factory = RequestFactory()
        for actor in (inactive, inactive_root):
            with self.subTest(actor=actor.username):
                self.assertFalse(usuario_tiene_cargo(actor))
                request = factory.get(self.url)
                request.user = actor
                self.assertFalse(
                    permisos_navegacion(request)["puede_registrar_usuarios"]
                )
                self.client.force_login(actor)
                response = self.client.get(self.url)
                self.assertRedirects(
                    response,
                    f"{reverse('account_login')}?next={self.url}",
                    fetch_redirect_response=False,
                )
                self.client.logout()


class CreacionUsuarioTests(BaseUsuariosTestCase):
    def setUp(self):
        self.url = reverse("Register")

    def test_actores_permitidos_crean_usuario_y_perfil(self):
        actors = [
            self.create_actor("creator_admin", position=self.admin_position),
            self.create_actor("creator_manager", position=self.manager_position),
            self.create_actor(
                "creator_root", superuser=True, position=self.admin_position
            ),
        ]
        for index, actor in enumerate(actors):
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                before = (User.objects.count(), Profile.objects.count())
                username = f"creado_{index}"
                response = self.client.post(
                    self.url,
                    self.valid_payload(username, self.restricted_position),
                    follow=True,
                )
                self.assertRedirects(response, reverse("User"))
                self.assertEqual(response.redirect_chain, [(reverse("User"), 302)])
                self.assertTemplateUsed(response, "pages/UsuarioApp/usuarios_lista.html")
                self.assertContains(response, "Usuario creado con éxito.", count=1)
                self.assertEqual(
                    (User.objects.count(), Profile.objects.count()),
                    (before[0] + 1, before[1] + 1),
                )
                created = User.objects.get(username=f"usuario_{username}")
                self.assertTrue(created.check_password(PASSWORD))
                self.assertFalse(created.is_staff)
                self.assertFalse(created.is_superuser)
                self.assertEqual(created.profile.position_FK, self.restricted_position)
                self.assertEqual(response.context["users"][0], created)
                self.assertContains(response, created.username)
                refreshed = self.client.get(reverse("User"))
                self.assertNotContains(refreshed, "Usuario creado con éxito.")
                self.assertEqual(
                    (User.objects.count(), Profile.objects.count()),
                    (before[0] + 1, before[1] + 1),
                )
                self.client.logout()

    def test_username_duplicado_y_password_no_coincidente_no_crean(self):
        actor = self.create_actor("validator", position=self.admin_position)
        User.objects.create_user(username="usuario_duplicado", password=PASSWORD)
        self.client.force_login(actor)
        cases = [
            self.valid_payload("duplicado"),
            {**self.valid_payload("mismatch"), "password2": "Distinta-2026!"},
        ]
        for payload in cases:
            with self.subTest(username=payload["username"]):
                before = (User.objects.count(), Profile.objects.count())
                response = self.client.post(self.url, payload)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["user_form"].errors)
                self.assertTemplateUsed(response, "pages/UsuarioApp/registro_usuario.html")
                self.assertNotContains(response, "Usuario creado con éxito.")
                self.assertEqual(
                    (User.objects.count(), Profile.objects.count()), before
                )

    def test_email_invalido_y_cargo_no_permitido_no_crean(self):
        actor = self.create_actor("validator2", position=self.admin_position)
        self.client.force_login(actor)
        cases = [
            {**self.valid_payload("badmail"), "email": "correo-invalido"},
            self.valid_payload("admin_no_asignable", self.admin_position),
            {**self.valid_payload("missing"), "position_FK": ""},
            {**self.valid_payload("unknown"), "position_FK": "999999"},
        ]
        for payload in cases:
            with self.subTest(username=payload["username"]):
                before = (User.objects.count(), Profile.objects.count())
                response = self.client.post(self.url, payload)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(
                    response.context["user_form"].errors
                    or response.context["profile_form"].errors
                )
                self.assertEqual(
                    (User.objects.count(), Profile.objects.count()), before
                )

    def test_password_debil_e_imagen_sobredimensionada_no_crean(self):
        actor = self.create_actor("validator3", position=self.admin_position)
        self.client.force_login(actor)

        weak_password = {
            **self.valid_payload("weak"),
            "password1": "12345678",
            "password2": "12345678",
        }
        before = (User.objects.count(), Profile.objects.count())
        response = self.client.post(self.url, weak_password)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["user_form"].errors)
        self.assertEqual((User.objects.count(), Profile.objects.count()), before)

        image_buffer = BytesIO()
        Image.new("RGB", (1400, 1400), "white").save(image_buffer, format="BMP")
        oversized_image = SimpleUploadedFile(
            "grande.bmp",
            image_buffer.getvalue(),
            content_type="image/bmp",
        )
        self.assertGreater(oversized_image.size, 5 * 1024 * 1024)
        before = (User.objects.count(), Profile.objects.count())
        image_payload = self.valid_payload("large_image")
        image_payload["image"] = oversized_image
        response = self.client.post(self.url, image_payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["profile_form"].errors)
        self.assertEqual((User.objects.count(), Profile.objects.count()), before)


class NavegacionRegistroTests(BaseUsuariosTestCase):
    def test_boton_del_listado_y_registro_respetan_cargo_y_estado(self):
        unknown = Position.objects.create(
            user_position="Otro cargo", permission_code="OTHER"
        )
        actors = [
            (None, False),
            (self.create_actor("list_admin", position=self.admin_position), True),
            (self.create_actor("list_manager", position=self.manager_position), True),
            (self.create_actor("list_restricted", position=self.restricted_position), False),
            (self.create_actor("list_sin_perfil", with_profile=False), False),
            (self.create_actor("list_sin_cargo"), False),
            (self.create_actor("list_desconocido", position=unknown), False),
            (self.create_actor("list_staff", staff=True, with_profile=False), False),
            (self.create_actor("list_inactivo", position=self.admin_position, active=False), False),
        ]
        for label, position in (
            ("admin", self.admin_position),
            ("manager", self.manager_position),
            ("restricted", self.restricted_position),
            ("sin_cargo", None),
            ("desconocido", unknown),
        ):
            actors.append((
                self.create_actor(f"list_root_{label}", superuser=True, position=position),
                label in ("admin", "manager"),
            ))
        actors.append((
            self.create_actor("list_root_sin_perfil", superuser=True, with_profile=False),
            False,
        ))
        button = f'<a href="{reverse("Register")}" class="button">Registrar usuario</a>'
        for index, (actor, allowed) in enumerate(actors):
            self.client.logout()
            if actor:
                self.client.force_login(actor)
            authenticated = bool(actor and actor.is_active)
            with self.subTest(actor=actor.username if actor else "anonimo"):
                for suffix in ("", "?inactive=1"):
                    url = reverse("User") + suffix
                    response = self.client.get(url)
                    if not authenticated:
                        self.assertRedirects(
                            response, f"{reverse('account_login')}?next={url}",
                            fetch_redirect_response=False,
                        )
                    elif allowed:
                        self.assertContains(response, button, count=1, html=True)
                    else:
                        self.assertNotContains(response, button, html=True)
                        self.assertNotContains(response, f'href="{reverse("Register")}"')
                page = self.client.get(reverse("Register"))
                before = (User.objects.count(), Profile.objects.count())
                result = self.client.post(
                    reverse("Register"),
                    self.valid_payload(f"listado_{index}", self.restricted_position),
                )
                if allowed:
                    self.assertEqual(page.status_code, 200)
                    self.assertRedirects(result, reverse("User"))
                    self.assertEqual(
                        (User.objects.count(), Profile.objects.count()),
                        (before[0] + 1, before[1] + 1),
                    )
                else:
                    destination = (
                        reverse("Home") if authenticated
                        else f"{reverse('account_login')}?next={reverse('Register')}"
                    )
                    for response in (page, result):
                        self.assertRedirects(response, destination, fetch_redirect_response=False)
                    self.assertEqual((User.objects.count(), Profile.objects.count()), before)

    def test_enlace_coincide_con_politica_del_servidor(self):
        register_href = f'href="{reverse("Register")}"'
        actors = [
            (self.create_actor("nav_admin", position=self.admin_position), True),
            (self.create_actor("nav_manager", position=self.manager_position), True),
            (self.create_actor("nav_root", superuser=True, with_profile=False), False),
            (
                self.create_actor("nav_restricted", position=self.restricted_position),
                False,
            ),
            (self.create_actor("nav_no_profile", with_profile=False), False),
            (self.create_actor("nav_no_position", position=None), False),
        ]
        for actor, allowed in actors:
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                response = self.client.get(reverse("Home"))
                self.assertEqual(response.status_code, 200)
                if allowed:
                    self.assertContains(response, register_href, html=False)
                else:
                    self.assertNotContains(response, register_href, html=False)
                self.client.logout()


class RenovacionUsuariosTests(BaseUsuariosTestCase):
    def test_gestion_visible_y_post_coinciden_para_toda_la_matriz(self):
        actors = [
            (None, False),
            (self.create_actor("visual_admin", position=self.admin_position), True),
            (self.create_actor("visual_manager", position=self.manager_position), True),
            (
                self.create_actor("visual_root", superuser=True, with_profile=False),
                False,
            ),
            (
                self.create_actor(
                    "visual_restricted", position=self.restricted_position
                ),
                False,
            ),
            (self.create_actor("visual_sinperfil", with_profile=False), False),
            (self.create_actor("visual_sincargo", position=None), False),
            (self.create_actor("visual_staff", staff=True, position=None), False),
            (
                self.create_actor(
                    "visual_inactivo", position=self.admin_position, active=False
                ),
                False,
            ),
            (
                self.create_actor("visual_root_inactivo", superuser=True, active=False),
                False,
            ),
        ]
        target = self.create_actor("visual_objetivo", position=self.restricted_position)
        for actor, allowed in actors:
            with self.subTest(actor=actor.username if actor else "anonimo"):
                self.client.logout()
                if actor:
                    self.client.force_login(actor)
                # Buscar evita que la paginación oculte el objetivo en la matriz.
                response = self.client.get(reverse("User"), {"search": target.username})
                if actor and actor.is_active:
                    self.assertEqual(response.status_code, 200)
                    # El flag global conserva la excepción de root para Registro;
                    # las acciones por destinatario de 004 requieren cargo.
                    self.assertEqual(
                        response.context["puede_gestionar_usuarios"],
                        allowed or actor.is_superuser,
                    )
                    opener = f'data-open-dialog="deactivate-{target.pk}"'
                    if allowed:
                        self.assertContains(response, opener)
                    else:
                        self.assertNotContains(response, opener)
                else:
                    self.assertEqual(response.status_code, 302)
                for route, starting_active in (
                    ("UserDeactivate", True),
                    ("UserActivate", False),
                ):
                    User.objects.filter(pk=target.pk).update(is_active=starting_active)
                    result = self.client.post(reverse(route, args=[target.pk]))
                    expected_status = (
                        302 if allowed or not actor or not actor.is_active else 403
                    )
                    self.assertEqual(result.status_code, expected_status)
                    target.refresh_from_db()
                    self.assertEqual(
                        target.is_active,
                        not starting_active if allowed else starting_active,
                    )
                User.objects.filter(pk=target.pk).update(is_active=True)

    def test_no_se_desactiva_propia_cuenta_ni_superusuario(self):
        actor = self.create_actor("proteccion_admin", position=self.admin_position)
        root = self.create_actor("proteccion_root", superuser=True, with_profile=False)
        self.client.force_login(actor)
        for target in (actor, root):
            with self.subTest(target=target.username):
                response = self.client.post(
                    reverse("UserDeactivate", args=[target.pk]), follow=True
                )
                self.assertEqual(response.status_code, 403)
                target.refresh_from_db()
                self.assertTrue(target.is_active)
        listing = self.client.get(reverse("User"))
        self.assertNotContains(listing, f'data-open-dialog="deactivate-{actor.pk}"')
        self.assertNotContains(listing, f'data-open-dialog="deactivate-{root.pk}"')

    def test_gestion_exige_post_y_csrf(self):
        actor = self.create_actor("csrf_admin", position=self.admin_position)
        target = self.create_actor("csrf_objetivo", position=self.restricted_position)
        self.client.force_login(actor)
        for route in ("UserActivate", "UserDeactivate"):
            url = reverse(route, args=[target.pk])
            self.assertEqual(self.client.get(url).status_code, 405)
            secure_client = Client(enforce_csrf_checks=True)
            secure_client.force_login(actor)
            self.assertEqual(secure_client.post(url).status_code, 403)
        target.refresh_from_db()
        self.assertTrue(target.is_active)

    def test_filtro_inactivo_y_busqueda_se_conservan_al_paginar(self):
        actor = self.create_actor("filtros", position=self.restricted_position)
        self.client.force_login(actor)
        for index in range(12):
            User.objects.create_user(
                f"filtrado_{index}", first_name="A&B", is_active=False
            )
        User.objects.create_user(
            "no_coincide", first_name="Otra persona", is_active=False
        )
        User.objects.create_user("coincide_activo", first_name="A&B", is_active=True)
        response = self.client.get(reverse("User"), {"inactive": "1", "search": "A&B"})
        self.assertEqual(response.context["paginator"].count, 12)
        self.assertTrue(all(not u.is_active for u in response.context["users"]))
        self.assertContains(response, "search=A%26B&amp;inactive=1#pagtable")
        second = self.client.get(
            reverse("User"), {"inactive": "1", "search": "A&B", "page": 2}
        )
        self.assertEqual(len(second.context["users"]), 3)
        self.assertNotContains(second, "no_coincide")

    def test_perfil_guarda_imagen_y_muestra_error_de_archivo_invalido(self):
        actor = self.create_actor("perfil_visual", position=self.restricted_position)
        self.client.force_login(actor)
        buffer = BytesIO()
        Image.new("RGB", (80, 80), "white").save(buffer, format="PNG")
        payload = {
            "username": actor.username,
            "email": "perfil@example.test",
            "first_name": "Persona",
            "last_name": "Sintética",
        }
        valid = {
            **payload,
            "image": SimpleUploadedFile(
                "avatar.png", buffer.getvalue(), content_type="image/png"
            ),
        }
        result = self.client.post(reverse("Profile"), valid)
        self.assertRedirects(result, reverse("Profile"))
        actor.refresh_from_db()
        self.assertEqual(actor.first_name, "Persona")
        self.assertNotEqual(actor.profile.image.name, "profile.webp")
        self.assertTrue(actor.profile.image.storage.exists(actor.profile.image.name))
        invalid = {
            **payload,
            "image": SimpleUploadedFile(
                "invalida.png", b"no es imagen", content_type="image/png"
            ),
        }
        response = self.client.post(reverse("Profile"), invalid)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["profile_form"].errors)
        self.assertContains(response, "errorlist")

    def test_cuenta_y_perfil_sin_cargo_siguen_accesibles(self):
        actor = self.create_actor("cuenta_sinperfil", with_profile=False)
        self.client.force_login(actor)
        for route in (
            "Home",
            "User",
            "Profile",
            "account_change_password",
            "account_email",
            "mfa_index",
        ):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
        self.assertTrue(Profile.objects.filter(user_FK=actor).exists())

    def test_texto_de_usuario_y_mensajes_no_inyectan_html(self):
        actor = self.create_actor("escape_admin", position=self.admin_position)
        target = self.create_actor("escape_objetivo", position=self.restricted_position)
        target.first_name = '<img src=x onerror="alert(1)">'
        target.save(update_fields=["first_name"])
        self.client.force_login(actor)
        response = self.client.get(reverse("User"), {"search": target.username})
        self.assertContains(response, "&lt;img")
        self.assertNotContains(response, '<img src=x onerror="alert(1)">')
        # Un username se incluye en notificaciones allauth; el render mantiene autoescape.
        from django.contrib import messages
        from django.contrib.messages import get_messages
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.template.loader import render_to_string

        request = RequestFactory().get("/")
        request.session = {}
        request._messages = FallbackStorage(request)
        messages.error(request, '<script>alert("prueba")</script>')
        html = render_to_string(
            "components/mensajes.html", {"messages": get_messages(request)}
        )
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)


class RenovacionCuentaTests(BaseUsuariosTestCase):
    def test_login_conserva_destino_y_logout_get_no_cierra_sesion(self):
        actor = self.create_actor("flujo_login", position=self.restricted_position)
        response = self.client.post(
            reverse("account_login"),
            {"login": actor.username, "password": PASSWORD, "next": reverse("User")},
        )
        self.assertRedirects(response, reverse("User"))
        confirmation = self.client.get(reverse("account_logout"))
        self.assertEqual(confirmation.status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)
        self.client.post(reverse("account_logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_recuperacion_y_password_conservan_validacion(self):
        actor = self.create_actor("flujo_password", position=self.restricted_position)
        actor.email = "flujo-password@example.test"
        actor.save(update_fields=["email"])
        EmailAddress.objects.create(
            user=actor, email=actor.email, verified=True, primary=True
        )
        response = self.client.post(
            reverse("account_reset_password"), {"email": actor.email}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.client.force_login(actor)
        invalid = self.client.post(
            reverse("account_change_password"),
            {"oldpassword": "incorrecta", "password1": PASSWORD, "password2": PASSWORD},
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertTrue(invalid.context["form"].errors)
        self.assertTrue(User.objects.get(pk=actor.pk).check_password(PASSWORD))

    def test_mfa_activacion_codigos_y_desactivacion_conservan_flujo(self):
        from allauth.mfa.models import Authenticator
        from allauth.mfa.totp.internal import auth as totp

        actor = self.create_actor("flujo_mfa", position=self.restricted_position)
        self.client.post(
            reverse("account_login"), {"login": actor.username, "password": PASSWORD}
        )
        activate_url = reverse("mfa_activate_totp")
        setup = self.client.get(activate_url)
        self.assertEqual(setup.status_code, 200)
        self.assertContains(setup, 'name="csrfmiddlewaretoken"')
        self.assertContains(setup, "data:image/svg+xml;base64,")
        secret = setup.context["form"].secret
        invalid = self.client.post(activate_url, {"code": "invalido"})
        self.assertEqual(invalid.status_code, 200)
        self.assertTrue(invalid.context["form"].errors)
        self.assertFalse(Authenticator.objects.filter(user=actor).exists())
        code = totp.format_hotp_value(
            totp.hotp_value(secret, next(totp.yield_hotp_counters_from_time()))
        )
        activated = self.client.post(activate_url, {"code": code})
        self.assertEqual(activated.status_code, 302)
        self.assertTrue(
            Authenticator.objects.filter(
                user=actor, type=Authenticator.Type.TOTP
            ).exists()
        )
        index = self.client.get(reverse("mfa_index"))
        for route in (
            "mfa_deactivate_totp",
            "mfa_view_recovery_codes",
            "mfa_download_recovery_codes",
            "mfa_generate_recovery_codes",
        ):
            self.assertContains(index, reverse(route))
        recovery = self.client.get(reverse("mfa_view_recovery_codes"))
        self.assertEqual(recovery.status_code, 200)
        self.assertEqual(
            len(recovery.context["unused_codes"]), settings.MFA_RECOVERY_CODE_COUNT
        )
        regenerated = self.client.post(reverse("mfa_generate_recovery_codes"))
        self.assertEqual(regenerated.status_code, 302)
        download = self.client.get(reverse("mfa_download_recovery_codes"))
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment", download["Content-Disposition"])
        UserVisualPreference.objects.create(user=actor, mode="dark")
        self.client.logout()
        first_step = self.client.post(
            reverse("account_login"), {"login": actor.username, "password": PASSWORD}
        )
        self.assertEqual(first_step.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
        bad_code = self.client.post(reverse("mfa_authenticate"), {"code": "invalido"})
        self.assertEqual(bad_code.status_code, 200)
        self.assertTrue(bad_code.context["form"].errors)
        self.assertContains(bad_code, 'data-mode="light"')
        code = totp.format_hotp_value(
            totp.hotp_value(secret, next(totp.yield_hotp_counters_from_time()))
        )
        authenticated = self.client.post(reverse("mfa_authenticate"), {"code": code})
        self.assertEqual(authenticated.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertContains(self.client.get(reverse("Home")), 'data-mode="dark"')
        deactivated = self.client.post(reverse("mfa_deactivate_totp"))
        self.assertEqual(deactivated.status_code, 302)
        self.assertFalse(
            Authenticator.objects.filter(
                user=actor, type=Authenticator.Type.TOTP
            ).exists()
        )


class ModoVisualTests(BaseUsuariosTestCase):
    def setUp(self):
        self.actor = self.create_actor("modo_propio", with_profile=False)
        self.url = reverse("VisualMode")
        self.client.force_login(self.actor)

    def test_matriz_activos_guarda_solo_preferencia_sin_exigir_cargo(self):
        actors = [
            self.create_actor("modo_admin", position=self.admin_position),
            self.create_actor("modo_manager", position=self.manager_position),
            self.create_actor("modo_restricted", position=self.restricted_position),
            self.create_actor("modo_sin_cargo"),
            self.actor,
            self.create_actor("modo_staff", staff=True, with_profile=False),
            self.create_actor("modo_root", superuser=True, with_profile=False),
        ]
        for actor in actors:
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                self.assertRedirects(
                    self.client.post(self.url, {"mode": "dark"}), reverse("Home")
                )
                self.assertEqual(
                    UserVisualPreference.objects.get(user=actor).mode, "dark"
                )
                self.assertContains(
                    self.client.get(reverse("Home")), 'aria-label="Activar modo claro"'
                )

    def test_anonimo_vuelve_a_inicio_despues_de_login_y_no_escribe(self):
        self.client.logout()
        response = self.client.post(self.url, {"mode": "dark"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url, f"{reverse('account_login')}?next={reverse('Home')}"
        )
        self.assertFalse(UserVisualPreference.objects.exists())

    @override_settings(
        AUTHENTICATION_BACKENDS=[
            "django.contrib.auth.backends.AllowAllUsersModelBackend"
        ]
    )
    def test_inactivos_incluso_root_denegados_en_servidor(self):
        for superuser in (False, True):
            actor = self.create_actor(
                f"modo_inactivo_{superuser}",
                active=False,
                superuser=superuser,
                with_profile=False,
            )
            self.client.force_login(
                actor, backend="django.contrib.auth.backends.AllowAllUsersModelBackend"
            )
            self.assertEqual(
                self.client.post(self.url, {"mode": "dark"}).status_code, 403
            )
        self.assertFalse(UserVisualPreference.objects.exists())

    def test_get_no_escribe_y_reintento_es_idempotente(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        for _ in range(2):
            self.assertEqual(
                self.client.post(self.url, {"mode": "dark"}).status_code, 302
            )
        self.assertEqual(
            UserVisualPreference.objects.filter(user=self.actor).count(), 1
        )
        self.assertEqual(UserVisualPreference.objects.get(user=self.actor).mode, "dark")

    def test_csrf_requerido_y_token_valido_guarda(self):
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.actor)
        self.assertEqual(browser.post(self.url, {"mode": "dark"}).status_code, 403)
        self.assertFalse(UserVisualPreference.objects.exists())
        browser.get(reverse("Home"))
        token = browser.cookies["csrftoken"].value
        self.assertEqual(
            browser.post(
                self.url, {"mode": "dark", "csrfmiddlewaretoken": token}
            ).status_code,
            302,
        )
        self.assertEqual(UserVisualPreference.objects.get(user=self.actor).mode, "dark")

    def test_valores_invalidos_conservan_modo_y_error_legible(self):
        preference = UserVisualPreference.objects.create(user=self.actor, mode="dark")
        for payload in (
            {},
            {"mode": ""},
            {"mode": "auto"},
            {"mode": "DARK"},
            {"mode": "<script>"},
        ):
            with self.subTest(payload=payload):
                response = self.client.post(self.url, payload)
                self.assertContains(
                    response, "Tu configuración no cambió", status_code=400
                )
                self.assertContains(response, 'data-mode="dark"', status_code=400)
                preference.refresh_from_db()
                self.assertEqual(preference.mode, "dark")

    def test_identificadores_ajenos_y_next_no_cambian_propiedad(self):
        other = self.create_actor("modo_ajeno", with_profile=False)
        UserVisualPreference.objects.create(user=other, mode="light")
        response = self.client.post(
            self.url,
            {
                "mode": "dark",
                "user": other.pk,
                "user_id": other.pk,
                "next": "https://example.test/ajeno",
            },
        )
        self.assertEqual(response.url, reverse("Home"))
        self.assertEqual(UserVisualPreference.objects.get(user=other).mode, "light")
        self.assertEqual(UserVisualPreference.objects.get(user=self.actor).mode, "dark")

    def test_modo_persiste_logout_login_y_otro_cliente(self):
        self.client.post(self.url, {"mode": "dark"})
        self.client.post(reverse("account_logout"))
        self.assertContains(
            self.client.get(reverse("account_login")), 'data-mode="light"'
        )
        self.assertEqual(UserVisualPreference.objects.get(user=self.actor).mode, "dark")
        for browser in (self.client, Client()):
            self.assertEqual(
                browser.post(
                    reverse("account_login"),
                    {"login": self.actor.username, "password": PASSWORD},
                ).status_code,
                302,
            )
            self.assertContains(browser.get(reverse("Home")), 'data-mode="dark"')
        self.client.post(self.url, {"mode": "light"})
        self.assertContains(self.client.get(reverse("Home")), 'data-mode="light"')

    def test_cuentas_del_mismo_navegador_no_heredan_modo(self):
        other = self.create_actor("modo_segundo", with_profile=False)
        self.client.post(self.url, {"mode": "dark"})
        self.client.logout()
        self.client.force_login(other)
        self.assertContains(self.client.get(reverse("Home")), 'data-mode="light"')
        self.assertFalse(UserVisualPreference.objects.filter(user=other).exists())
        self.client.logout()
        self.client.force_login(self.actor)
        self.assertContains(self.client.get(reverse("Home")), 'data-mode="dark"')

    def test_guardado_no_crea_perfil_ni_procesa_fotos(self):
        actor = self.create_actor("modo_con_foto", position=self.restricted_position)
        self.client.force_login(actor)
        profile = Profile.objects.get(user_FK=actor)
        previous = (profile.image.name, profile.position_FK_id)
        with (
            patch("UsuarioApp.models.prepare_profile_image") as prepare,
            patch("UsuarioApp.models.delete_profile_image") as delete,
        ):
            self.client.post(self.url, {"mode": "dark"})
            prepare.assert_not_called()
            delete.assert_not_called()
        profile.refresh_from_db()
        self.assertEqual((profile.image.name, profile.position_FK_id), previous)
        self.client.force_login(self.actor)
        self.client.post(self.url, {"mode": "dark"})
        self.assertFalse(Profile.objects.filter(user_FK=self.actor).exists())

    def test_restricciones_de_base_y_cascada(self):
        UserVisualPreference.objects.create(user=self.actor)
        for data in (
            {"user": self.actor, "mode": "dark"},
            {
                "user": self.create_actor("modo_constraint", with_profile=False),
                "mode": "auto",
            },
        ):
            with (
                self.subTest(data=data),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                UserVisualPreference.objects.create(**data)
        self.actor.delete()
        self.assertFalse(UserVisualPreference.objects.exists())

    def test_fallo_de_guardado_no_confirma_y_conserva_valor(self):
        preference = UserVisualPreference.objects.create(user=self.actor, mode="dark")
        for failure in (
            OperationalError("fallo sintético"),
            IntegrityError("fallo sintético"),
        ):
            with (
                self.subTest(failure=type(failure).__name__),
                patch(
                    "UsuarioApp.views.UserVisualPreference.objects.update_or_create",
                    side_effect=failure,
                ),
            ):
                response = self.client.post(self.url, {"mode": "light"})
            self.assertContains(response, "No pudimos guardar el modo", status_code=500)
            self.assertContains(response, 'data-mode="dark"', status_code=500)
            preference.refresh_from_db()
            self.assertEqual(preference.mode, "dark")
        with (
            patch(
                "UsuarioApp.views.UserVisualPreference.objects.update_or_create",
                side_effect=ProgrammingError("fallo sintético"),
            ),
            self.assertRaises(ProgrammingError),
        ):
            self.client.post(self.url, {"mode": "light"})

    def test_processor_no_escribe_y_error_sin_usuario_tiene_default(self):
        request = RequestFactory().get("/")
        self.assertEqual(
            preferencias_visuales(request),
            {"modo_visual": "light", "puede_cambiar_modo": False},
        )
        request.user = self.actor
        self.assertEqual(preferencias_visuales(request)["modo_visual"], "light")
        self.client.get(reverse("Home"))
        self.client.get(reverse("User"))
        self.assertFalse(UserVisualPreference.objects.exists())
        self.assertFalse(Profile.objects.filter(user_FK=self.actor).exists())
        with patch(
            "core.context_processors.UserVisualPreference.objects.filter",
            side_effect=OperationalError("sin conexión"),
        ):
            self.assertEqual(preferencias_visuales(request)["modo_visual"], "light")


class MigracionModoVisualTests(TransactionTestCase):
    def test_migracion_aditiva_y_reversion_preservan_usuario_y_perfil(self):
        actor = User.objects.create_user("migracion_sintetica")
        profile = Profile.objects.create(user_FK=actor)
        UserVisualPreference.objects.create(user=actor, mode="dark")
        try:
            MigrationExecutor(connection).migrate([("UsuarioApp", "0001_initial")])
            self.assertNotIn(
                UserVisualPreference._meta.db_table,
                connection.introspection.table_names(),
            )
            self.assertTrue(User.objects.filter(pk=actor.pk).exists())
            self.assertTrue(Profile.objects.filter(pk=profile.pk).exists())
        finally:
            MigrationExecutor(connection).migrate(
                [("UsuarioApp", "0002_user_visual_preference")]
            )
        self.assertEqual(UserVisualPreference.objects.count(), 0)
        self.assertTrue(User.objects.filter(pk=actor.pk).exists())
