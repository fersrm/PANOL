"""Política de recuperación administrativa del pañol, sin datos reales."""

from allauth.account.forms import default_token_generator
from allauth.account.utils import user_pk_to_url_str
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import resolve, reverse

from UsuarioApp.models import Position, Profile
from UsuarioApp.views import PasswordRecoveryHelpView


class AccesoSinCorreoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "acceso_sintetico", "acceso@example.test", "Acceso-sintetico-2026!"
        )
        self.paths = [
            reverse("account_reset_password"),
            reverse("account_reset_password_done"),
            reverse("account_reset_password_from_key_done"),
            reverse(
                "account_reset_password_from_key",
                kwargs={
                    "uidb36": user_pk_to_url_str(self.user),
                    "key": default_token_generator.make_token(self.user),
                },
            ),
        ]

    def test_rutas_anteriores_incluso_token_valido_solo_muestran_ayuda(self):
        for path in self.paths:
            with self.subTest(path=path):
                self.assertIs(resolve(path).func.view_class, PasswordRecoveryHelpView)
                response = self.client.get(path)
                self.assertContains(response, "comunícate con el administrador")
                self.assertNotContains(response, "<form")
                self.assertNotContains(response, 'name="email"')
        self.assertEqual(len(mail.outbox), 0)

    def test_post_con_correo_o_token_no_envia_ni_modifica_password(self):
        before = self.user.password
        for path in self.paths:
            with self.subTest(path=path):
                response = self.client.post(
                    path,
                    {
                        "email": self.user.email,
                        "password1": "Reemplazo-sintetico-2026!",
                        "password2": "Reemplazo-sintetico-2026!",
                    },
                )
                self.assertEqual(response.status_code, 405)
        self.user.refresh_from_db()
        self.assertEqual(self.user.password, before)
        self.assertEqual(len(mail.outbox), 0)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_ayuda_publica_igual_para_cargos_inactivos_y_superusuario(self):
        for cargo in (None, "ADMIN", "MANAGER", "RESTRICTED"):
            with self.subTest(cargo=cargo):
                Profile.objects.filter(user_FK=self.user).delete()
                if cargo:
                    position = Position.objects.create(
                        user_position=cargo, permission_code=cargo
                    )
                    Profile.objects.create(user_FK=self.user, position_FK=position)
                self.client.force_login(self.user)
                self.assertContains(
                    self.client.get(self.paths[0]), "comunícate con el administrador"
                )
        self.user.is_superuser = True
        self.user.is_active = False
        self.user.save(update_fields=["is_superuser", "is_active"])
        self.assertContains(
            self.client.get(self.paths[0]), "comunícate con el administrador"
        )

    def test_menu_oculta_correo_pero_conserva_acciones_y_endpoint_existente(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("Home"))
        self.assertNotContains(response, f'href="{reverse("account_email")}"')
        for name in ("Profile", "mfa_index"):
            self.assertContains(response, f'href="{reverse(name)}"')
        self.assertContains(response, f'action="{reverse("account_logout")}"')
        self.assertEqual(self.client.get(reverse("account_email")).status_code, 200)

    def test_ayuda_del_login_no_es_submit_ni_formulario_de_correo(self):
        response = self.client.get(reverse("account_login"))
        self.assertContains(response, '<details class="auth-recovery">')
        self.assertContains(response, "¿Olvidaste tu contraseña?")
        self.assertContains(response, "comunícate con el administrador")
        self.assertNotContains(response, f'href="{self.paths[0]}"')
        html = response.content.decode()
        self.assertLess(
            html.index("</form>"), html.index('<details class="auth-recovery">')
        )
