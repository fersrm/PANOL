from datetime import timedelta

from django.contrib.auth.models import AnonymousUser, User
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from UsuarioApp.models import UserVisualPreference


class RegresionInicioTests(TestCase):
    def test_selector_inicial_y_modo_servidor_en_pantallas_compartidas(self):
        user = User.objects.create_user(
            username="selector_sintetico", password="Prueba-2026-Segura!"
        )
        self.client.force_login(user)
        initial = self.client.get(reverse("Home"))
        self.assertContains(initial, 'data-mode="light"')
        self.assertContains(initial, 'aria-label="Activar modo oscuro"')
        self.assertContains(initial, f'action="{reverse("VisualMode")}" method="post"')
        self.assertContains(initial, 'name="csrfmiddlewaretoken"')
        self.assertContains(initial, 'aria-describedby="visual-mode-state"')
        self.assertFalse(UserVisualPreference.objects.exists())
        UserVisualPreference.objects.create(user=user, mode="dark")
        dark = self.client.get(reverse("Home"))
        self.assertContains(dark, 'aria-label="Activar modo claro"')
        self.assertNotContains(dark, 'aria-label="Activar modo oscuro"')
        for route in ("User", "account_email", "account_change_password", "mfa_index"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertContains(response, 'data-mode="dark"')
                self.assertNotContains(response, 'class="visual-mode-form"')

    def test_errores_autenticados_aplican_preferencia(self):
        user = User.objects.create_user(username="errores_oscuros_sinteticos")
        UserVisualPreference.objects.create(user=user, mode="dark")
        request = RequestFactory().get("/inexistente/")
        request.user = user
        for code in (400, 401, 403, 404, 429, 500):
            with self.subTest(code=code):
                self.assertIn(
                    'data-mode="dark"',
                    render_to_string(f"{code}.html", request=request),
                )

    def test_inicio_muestra_grafico_real_del_panol(self):
        user = User.objects.create_user(
            username="inicio", password="Prueba-2026-Segura!", is_superuser=True
        )
        self.client.force_login(user)
        response = self.client.get(reverse("Home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Entregas y recepciones")
        self.assertNotContains(response, "Datos demostrativos")
        self.assertEqual(sum(response.context["panol_grafico"]["entregas"]), 0)
        self.assertContains(response, "grafico_home.js")

    def test_inicio_distingue_actividad_y_accesos_con_datos_sinteticos(self):
        user = User.objects.create_user(
            username="visor", password="Prueba-2026-Segura!"
        )
        self.client.force_login(user)
        now = timezone.now()
        for index in range(7):
            User.objects.create_user(
                username=f"reciente_{index}", last_login=now - timedelta(minutes=index)
            )
        User.objects.create_user(
            username="inactivo_excluido", is_active=False, last_login=now
        )
        response = self.client.get(reverse("Home"))
        self.assertEqual(len(response.context["usuarios_recientes"]), 5)
        self.assertNotContains(response, "inactivo_excluido")
        self.assertContains(response, "no confirma que el usuario siga conectado")
        self.assertNotContains(response, "Datos demostrativos")

    def test_errores_renderizan_para_anonimo_y_cuenta_sin_perfil(self):
        factory = RequestFactory()
        actor = User.objects.create_user(
            username="errores", password="Prueba-2026-Segura!"
        )
        for user in (AnonymousUser(), actor):
            for code in (400, 401, 403, 404, 429, 500):
                with self.subTest(code=code, authenticated=user.is_authenticated):
                    request = factory.get("/pagina-inexistente/")
                    request.user = user
                    html = render_to_string(f"{code}.html", request=request)
                    self.assertIn(f'class="error-code">{code}', html)
                    self.assertIn(
                        reverse("Home")
                        if user.is_authenticated
                        else reverse("account_login"),
                        html,
                    )
