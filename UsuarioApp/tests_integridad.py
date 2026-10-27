"""Regresiones de los fallos reproducidos AUD-004/005, con media ficticia."""

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import Client, override_settings
from django.urls import reverse
from PIL import Image

from .models import Position, Profile
from .tests import BaseUsuariosTestCase


def photo():
    buffer = BytesIO()
    Image.new("RGB", (600, 400), "green").save(buffer, format="PNG")
    return SimpleUploadedFile(
        "ficticia.png", buffer.getvalue(), content_type="image/png"
    )


class IntegridadRegistroTests(BaseUsuariosTestCase):
    def test_username_de_longitud_maxima_admite_foto_sin_alta_parcial(self):
        from django.contrib.auth.models import User

        actor = self.create_actor("nombre_largo", position=self.admin_position)
        self.client.force_login(actor)
        username = "u" * 150
        payload = {
            **self.valid_payload("limite"),
            "username": username,
            "image": photo(),
        }
        result = self.client.post(reverse("Register"), payload)
        self.assertEqual(result.status_code, 302)
        created = User.objects.get(username=username)
        self.assertLessEqual(len(created.profile.image.name), 100)
        with (
            created.profile.image.storage.open(created.profile.image.name) as file,
            Image.open(file) as image,
        ):
            self.assertEqual(image.size, (300, 300))

    def test_post_sin_csrf_no_crea_usuario(self):
        from django.contrib.auth.models import User

        actor = self.create_actor("csrf_registro", position=self.admin_position)
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(actor)
        before = (User.objects.count(), Profile.objects.count())
        result = browser.post(reverse("Register"), self.valid_payload("sin_csrf"))
        self.assertEqual(result.status_code, 403)
        self.assertEqual((User.objects.count(), Profile.objects.count()), before)

    def test_matriz_cargos_ids_arbitrarios_formulario_y_post(self):
        other = Position.objects.create(
            user_position="Desconocido", permission_code="OTHER"
        )
        roles = [
            self.excluded_position,
            self.admin_position,
            self.manager_position,
            self.restricted_position,
            other,
        ]
        actors = [
            (
                self.create_actor("normal_admin", position=self.admin_position),
                {"MANAGER", "RESTRICTED"},
            ),
            (
                self.create_actor("normal_manager", position=self.manager_position),
                {"RESTRICTED"},
            ),
            (
                self.create_actor(
                    "root_admin", position=self.admin_position, superuser=True
                ),
                {"ADMIN", "MANAGER", "RESTRICTED"},
            ),
            (
                self.create_actor(
                    "root_manager", position=self.manager_position, superuser=True
                ),
                {"RESTRICTED"},
            ),
            (
                self.create_actor("root_sin_cargo", superuser=True, with_profile=False),
                set(),
            ),
            (
                self.create_actor(
                    "root_restricted", superuser=True, position=self.restricted_position
                ),
                set(),
            ),
            (self.create_actor("desconocido", position=other), set()),
        ]
        for index, (actor, allowed) in enumerate(actors):
            self.client.force_login(actor)
            page = self.client.get(reverse("Register"))
            if allowed:
                options = page.context["profile_form"].fields["position_FK"].queryset
                self.assertEqual(
                    set(options.values_list("permission_code", flat=True)), allowed
                )
            else:
                self.assertEqual(page.status_code, 302)
            for role in roles:
                with self.subTest(
                    actor=actor.username, cargo=role.permission_code, pk=role.pk
                ):
                    username = f"matriz_{index}_{role.pk}"
                    result = self.client.post(
                        reverse("Register"), self.valid_payload(username, role)
                    )
                    from django.contrib.auth.models import User

                    created = User.objects.filter(
                        username="usuario_" + username
                    ).first()
                    self.assertEqual(
                        created is not None, role.permission_code in allowed
                    )
                    if created:
                        self.assertEqual(created.profile.position_FK, role)
                        self.assertFalse(created.is_superuser)
                        self.assertFalse(created.is_staff)
                    elif allowed:
                        self.assertEqual(result.status_code, 200)
                        self.assertIn(
                            "position_FK", result.context["profile_form"].errors
                        )
            self.client.logout()

    def test_cargo_del_operador_se_revalida_despues_de_get(self):
        actor = self.create_actor("revocado", position=self.admin_position)
        self.client.force_login(actor)
        self.assertEqual(self.client.get(reverse("Register")).status_code, 200)
        Profile.objects.filter(user_FK=actor).update(
            position_FK=self.restricted_position
        )
        result = self.client.post(reverse("Register"), self.valid_payload("revocado"))
        self.assertEqual(result.status_code, 302)
        from django.contrib.auth.models import User

        self.assertFalse(User.objects.filter(username="usuario_revocado").exists())

    def test_fallo_perfil_antes_y_despues_de_escribir_revierte_alta_y_foto(self):
        from django.contrib.auth.models import User

        actor = self.create_actor("creador_atomico", position=self.admin_position)
        self.client.force_login(actor)
        original = Profile.save
        for after in (False, True):

            def fail(instance, *args, after=after, **kwargs):
                if after:
                    original(instance, *args, **kwargs)
                raise IntegrityError("Fallo ficticio")

            before = set(Path(settings.MEDIA_ROOT).rglob("*"))
            before_files = {p for p in before if p.is_file()}
            payload = {**self.valid_payload(f"fallo_{after}"), "image": photo()}
            with self.subTest(after=after), patch.object(Profile, "save", fail):
                result = self.client.post(reverse("Register"), payload)
            self.assertEqual(result.status_code, 500)
            self.assertNotContains(result, "Usuario creado con éxito.", status_code=500)
            self.assertNotContains(
                self.client.get(reverse("User")), "Usuario creado con éxito."
            )
            self.assertFalse(User.objects.filter(username=payload["username"]).exists())
            self.assertEqual(
                {p for p in Path(settings.MEDIA_ROOT).rglob("*") if p.is_file()},
                before_files,
            )

    def test_fallo_decodificacion_no_escribe_cuenta_ni_archivo(self):
        from django.contrib.auth.models import User

        actor = self.create_actor("decodificacion", position=self.admin_position)
        self.client.force_login(actor)
        payload = {**self.valid_payload("decode"), "image": photo()}
        with patch(
            "UsuarioApp.models.prepare_profile_image",
            side_effect=OSError("Fallo ficticio"),
        ):
            result = self.client.post(reverse("Register"), payload)
        self.assertEqual(result.status_code, 500)
        self.assertFalse(User.objects.filter(username=payload["username"]).exists())

    def test_error_inesperado_se_propaga_pero_revierte_el_alta(self):
        from django.contrib.auth.models import User

        actor = self.create_actor("error_inesperado", position=self.admin_position)
        self.client.force_login(actor)
        original = Profile.save

        def fail(instance, *args, **kwargs):
            original(instance, *args, **kwargs)
            raise RuntimeError("Error ficticio de programación")

        payload = {**self.valid_payload("unexpected"), "image": photo()}
        before = {p for p in Path(settings.MEDIA_ROOT).rglob("*") if p.is_file()}
        with patch.object(Profile, "save", fail), self.assertRaises(RuntimeError):
            self.client.post(reverse("Register"), payload)
        self.assertFalse(User.objects.filter(username=payload["username"]).exists())
        self.assertEqual(
            {p for p in Path(settings.MEDIA_ROOT).rglob("*") if p.is_file()}, before
        )


class IntegridadPerfilTests(BaseUsuariosTestCase):
    def setUp(self):
        media = TemporaryDirectory(prefix="perfil-sintetico-")
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=Path(media.name))
        override.enable()
        self.addCleanup(override.disable)
        self.actor = self.create_actor(
            "perfil_atomico", position=self.restricted_position
        )
        profile = self.actor.profile
        profile.image = photo()
        with self.captureOnCommitCallbacks(execute=True):
            profile.save()
        self.old_name = profile.image.name
        self.storage = profile.image.storage
        self.old_bytes = self.storage.open(self.old_name).read()
        self.client.force_login(self.actor)

    def payload(self):
        return {
            "username": "perfil_modificado",
            "email": "modificado@example.test",
            "first_name": "Cambio",
            "last_name": "Ficticio",
            "image": photo(),
        }

    def test_fallo_despues_de_guardar_revierte_datos_y_preserva_foto(self):
        original = Profile.save

        def fail(instance, *args, **kwargs):
            original(instance, *args, **kwargs)
            raise IntegrityError("Fallo ficticio tras escribir")

        with (
            patch.object(Profile, "save", fail),
            self.captureOnCommitCallbacks(execute=True),
        ):
            result = self.client.post(reverse("Profile"), self.payload())
        self.assertEqual(result.status_code, 500)
        self.actor.refresh_from_db()
        self.assertEqual(self.actor.username, "perfil_atomico")
        self.assertEqual(self.actor.profile.image.name, self.old_name)
        self.assertEqual(self.storage.open(self.old_name).read(), self.old_bytes)
        files = {
            p.relative_to(settings.MEDIA_ROOT).as_posix()
            for p in Path(settings.MEDIA_ROOT).rglob("*")
            if p.is_file()
        }
        self.assertEqual(files, {self.old_name})

    def test_error_storage_despues_de_escribir_compensa_archivo(self):
        original = self.storage.save

        def fail(name, content, **kwargs):
            original(name, content, **kwargs)
            raise OSError("Fallo ficticio del almacenamiento")

        before = {p for p in Path(settings.MEDIA_ROOT).rglob("*") if p.is_file()}
        with patch.object(self.storage, "save", fail):
            result = self.client.post(reverse("Profile"), self.payload())
        self.assertEqual(result.status_code, 500)
        self.actor.refresh_from_db()
        self.assertEqual(self.actor.username, "perfil_atomico")
        self.assertEqual(self.actor.profile.image.name, self.old_name)
        self.assertEqual(
            {p for p in Path(settings.MEDIA_ROOT).rglob("*") if p.is_file()}, before
        )

    def test_foto_anterior_se_borra_solo_despues_de_commit_y_nueva_es_300(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            result = self.client.post(reverse("Profile"), self.payload())
        self.assertEqual(result.status_code, 302)
        self.actor.refresh_from_db()
        self.assertTrue(self.storage.exists(self.old_name))
        new_name = self.actor.profile.image.name
        self.assertNotEqual(new_name, self.old_name)
        with self.storage.open(new_name) as file, Image.open(file) as image:
            self.assertEqual(image.size, (300, 300))
        for callback in callbacks:
            callback()
        self.assertFalse(self.storage.exists(self.old_name))
        self.assertTrue(self.storage.exists(new_name))

    def test_guardado_sin_foto_no_reprocesa_archivo_existente(self):
        payload = self.payload()
        del payload["image"]
        with patch(
            "UsuarioApp.models.prepare_profile_image",
            side_effect=AssertionError("No reprocesar"),
        ):
            result = self.client.post(reverse("Profile"), payload)
        self.assertEqual(result.status_code, 302)
        self.actor.refresh_from_db()
        self.assertEqual(self.actor.username, payload["username"])
        self.assertEqual(self.actor.profile.image.name, self.old_name)
        self.assertEqual(self.storage.open(self.old_name).read(), self.old_bytes)

    def test_post_invalido_sin_perfil_no_crea_fila(self):
        actor = self.create_actor("sin_perfil_invalido", with_profile=False)
        self.client.force_login(actor)
        result = self.client.post(
            reverse("Profile"), {"username": "", "email": "invalido"}
        )
        self.assertEqual(result.status_code, 200)
        self.assertFalse(Profile.objects.filter(user_FK=actor).exists())

    def test_fallo_limpieza_tras_commit_conserva_perfil_valido_y_registra_error(self):
        with (
            patch.object(self.storage, "delete", side_effect=OSError("Fallo ficticio")),
            self.assertLogs("utils.customer_img", level="ERROR") as logs,
            self.captureOnCommitCallbacks(execute=True),
        ):
            result = self.client.post(reverse("Profile"), self.payload())
        self.assertEqual(result.status_code, 302)
        self.actor.refresh_from_db()
        self.assertEqual(self.actor.username, "perfil_modificado")
        self.assertNotEqual(self.actor.profile.image.name, self.old_name)
        self.assertTrue(self.storage.exists(self.actor.profile.image.name))
        self.assertTrue(self.storage.exists(self.old_name))
        self.assertNotIn(self.old_name, " ".join(logs.output))
