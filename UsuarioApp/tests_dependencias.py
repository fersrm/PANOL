"""Regresiones de integración de dependencias; datos y servicios sintéticos."""

import ast
from pathlib import Path

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse


def hashers_normales():
    # Leer el literal, sin importar settings.py ni cargar el .env del propietario.
    module = ast.parse(
        (Path(settings.BASE_DIR) / "core/settings.py").read_text(encoding="utf-8")
    )
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "PASSWORD_HASHERS"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("Configuración normal sin PASSWORD_HASHERS explícitos")


@override_settings(PASSWORD_HASHERS=hashers_normales())
class CompatibilidadHashersTests(TestCase):
    def test_hashes_heredados_se_verifican_y_actualizan_al_iniciar_sesion(self):
        secret = "Contraseña ficticia extensa y Unicode ñ Ω " * 3
        for algorithm in (
            "argon2",
            "pbkdf2_sha256",
            "pbkdf2_sha1",
            "bcrypt_sha256",
            "scrypt",
        ):
            with self.subTest(algorithm=algorithm):
                user = User.objects.create(
                    username="heredado_" + algorithm, email=algorithm + "@example.test"
                )
                user.password = make_password(secret, hasher=algorithm)
                user.save(update_fields=["password"])
                self.assertTrue(check_password(secret, user.password))
                self.assertFalse(check_password(secret + "error", user.password))
                response = self.client.post(
                    reverse("account_login"),
                    {"login": user.username, "password": secret},
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(str(user.pk), self.client.session["_auth_user_id"])
                user.refresh_from_db()
                self.assertTrue(user.password.startswith("argon2$"))
                self.assertTrue(user.check_password(secret))
                self.client.logout()


class CompatibilidadLoginTests(TestCase):
    def test_username_y_email_conservan_acceso_sin_enviar_correo(self):
        from django.core import mail

        user = User.objects.create_user(
            "login_compatible", "compatible@example.test", "Ficticia-login-2026!"
        )
        for login in (user.username, user.email):
            with self.subTest(login=login):
                response = self.client.post(
                    reverse("account_login"),
                    {"login": login, "password": "Ficticia-login-2026!"},
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(str(user.pk), self.client.session["_auth_user_id"])
                self.client.logout()
        self.assertEqual(len(mail.outbox), 0)

    def test_password_incorrecto_no_crea_sesion(self):
        user = User.objects.create_user(
            "login_invalido", "invalido@example.test", "Ficticia-login-2026!"
        )
        response = self.client.post(
            reverse("account_login"),
            {"login": user.email, "password": "Incorrecta-ficticia!"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertNotIn("_auth_user_id", self.client.session)


class MigracionesAllauthTests(TransactionTestCase):
    old_targets = [
        ("account", "0005_emailaddress_idx_upper_email"),
        ("mfa", "0002_authenticator_timestamps"),
    ]

    def setUp(self):
        executor = MigrationExecutor(connection)
        self.current_targets = executor.loader.graph.leaf_nodes()
        executor.migrate(self.old_targets)
        self.old_apps = executor.loader.project_state(self.old_targets).apps

    def tearDown(self):
        # Restaurar siempre el esquema antes del flush y de los siguientes tests.
        MigrationExecutor(connection).migrate(self.current_targets)
        super().tearDown()

    def seed(self):
        old_user = self.old_apps.get_model("auth", "User").objects.create(
            username="migracion",
            email="Preferida@Example.Test",
            password="!sin-password-utilizable",
        )
        email_model = self.old_apps.get_model("account", "EmailAddress")
        email_model.objects.create(
            user=old_user, email="Otra@Example.Test", primary=True
        )
        email_model.objects.create(
            user=old_user, email="Preferida@Example.Test", primary=True
        )
        return old_user, email_model

    def test_preflight_bloquea_tipo_mfa_desconocido_sin_alterar_factor(self):
        from io import StringIO

        from django.core.management import call_command
        from django.core.management.base import CommandError

        old_user, _ = self.seed()
        old_auth = self.old_apps.get_model("mfa", "Authenticator")
        factor = old_auth.objects.create(
            user=old_user, type="UNKNOWN", data={"fixture": True}
        )
        with self.assertRaises(CommandError):
            call_command("verificar_actualizacion_allauth", stdout=StringIO())
        factor.refresh_from_db()
        self.assertEqual(factor.data, {"fixture": True})
        self.assertEqual(factor.type, "UNKNOWN")

    def test_normalizacion_principal_constraints_y_mfa_heredado(self):
        import hashlib
        import hmac

        from allauth.account.models import EmailAddress
        from allauth.mfa.models import Authenticator

        old_user, _ = self.seed()
        old_auth = self.old_apps.get_model("mfa", "Authenticator")
        data = {"seed": "12" * 20, "used_mask": 0}
        old_auth.objects.create(user=old_user, type="recovery_codes", data=data)
        digest = hmac.new(data["seed"].encode(), digestmod=hashlib.sha1)
        digest.update(b"  0,")
        legacy_code = str(int.from_bytes(digest.digest()[:4], "big") % 10**8).zfill(8)
        MigrationExecutor(connection).migrate(self.current_targets)
        user = User.objects.get(pk=old_user.pk)
        self.assertEqual(user.email, "preferida@example.test")
        self.assertEqual(user.password, old_user.password)
        self.assertEqual(
            EmailAddress.objects.get(user=user, primary=True).email, user.email
        )
        self.assertEqual(EmailAddress.objects.filter(user=user).count(), 2)
        authenticator = Authenticator.objects.get(user=user, type="recovery_codes")
        self.assertEqual(authenticator.data, data)
        recovery = authenticator.wrap()
        self.assertTrue(recovery.validate_code(legacy_code))
        self.assertFalse(recovery.validate_code(legacy_code))
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmailAddress.objects.create(
                user=user, email="tercero@example.test", primary=True
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Authenticator.objects.create(user=user, type="recovery_codes", data=data)

    def test_preflight_bloquea_colision_sin_modificar_datos(self):
        from io import StringIO

        from django.core.management import call_command
        from django.core.management.base import CommandError

        old_user, email_model = self.seed()
        email_model.objects.create(user=old_user, email="preferida@example.test")
        before = list(
            email_model.objects.order_by("pk").values("pk", "email", "primary")
        )
        with self.assertRaises(CommandError):
            call_command("verificar_actualizacion_allauth", stdout=StringIO())
        self.assertEqual(
            list(email_model.objects.order_by("pk").values("pk", "email", "primary")),
            before,
        )
        self.assertEqual(User.objects.get(pk=old_user.pk).email, old_user.email)
        # Solo fixture sintética: quitar el registro agregado para restaurar esquema.
        email_model.objects.filter(
            user=old_user, email="preferida@example.test"
        ).delete()
