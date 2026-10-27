import os
import uuid

from django.conf import settings

# Create your models here.
from django.contrib.auth.models import User
from django.db import models, transaction
from django.utils import timezone

from utils.customer_img import delete_profile_image, prepare_profile_image

from .choices import PERMISOS


class UserVisualPreference(models.Model):
    class Mode(models.TextChoices):
        LIGHT = "light", "Claro"
        DARK = "dark", "Oscuro"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="visual_preference",
    )
    mode = models.CharField(max_length=5, choices=Mode.choices, default=Mode.LIGHT)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(mode__in=["light", "dark"]),
                name="visual_preference_mode_valid",
            )
        ]


def profile_picture_path(instance, filename):
    random_filename = str(uuid.uuid4())
    extension = os.path.splitext(filename)[1]
    # Un username válido de 150 caracteres no cabe en ImageField(max_length=100).
    # El ID mantiene la ruta breve y estable cuando cambia el nombre de usuario.
    return f"users/{instance.user_FK_id}/{random_filename}{extension}"


# Create your models here.
class Position(models.Model):
    user_position = models.CharField(max_length=45, unique=True)
    permission_code = models.CharField(
        max_length=25, choices=PERMISOS, default="RESTRICTED"
    )

    class Meta:
        db_table = "position"

    def __str__(self):
        return f"{self.user_position}"


class Profile(models.Model):
    last_activity = models.DateTimeField(null=True, blank=True)
    image = models.ImageField(upload_to=profile_picture_path, default="profile.webp")
    user_FK = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="profile"
    )
    position_FK = models.ForeignKey(
        Position, on_delete=models.SET_NULL, null=True, blank=True
    )

    def save(self, *args, **kwargs):
        update_last_activity = kwargs.pop("update_last_activity", False)

        if update_last_activity:
            self.last_activity = timezone.now()
            kwargs["update_fields"] = ["last_activity"]

        fields = kwargs.get("update_fields")
        new_upload = (
            self.image
            and not self.image._committed
            and (fields is None or "image" in fields)
        )
        if not new_upload:
            return super().save(*args, **kwargs)

        previous_name = (
            type(self)
            .objects.filter(pk=self.pk)
            .values_list("image", flat=True)
            .first()
            if self.pk
            else self._meta.get_field("image").get_default()
        )
        prepared = prepare_profile_image(self.image.file)
        storage = self.image.storage
        candidate_name = self.image.field.generate_filename(self, self.image.name)
        # UUID de upload_to; conservar cualquier archivo existente si storage falla.
        existed = None
        new_name = None
        try:
            existed = storage.exists(candidate_name)
            new_name = storage.save(
                candidate_name, prepared, max_length=self.image.field.max_length
            )
            self.image = new_name
            with transaction.atomic():
                super().save(*args, **kwargs)
                if previous_name != new_name:
                    transaction.on_commit(
                        lambda: delete_profile_image(storage, previous_name),
                        robust=True,
                    )
        except Exception:
            # Compensar también si storage escribió y después lanzó un error.
            if new_name or existed is False:
                delete_profile_image(storage, new_name or candidate_name)
            self.image = previous_name
            raise
        finally:
            prepared.close()

    def update_last_activity(self):
        # Actualizar actividad no debe volver a abrir ni recortar la fotografía.
        self.last_activity = timezone.now()
        type(self).objects.filter(pk=self.pk).update(last_activity=self.last_activity)

    class Meta:
        verbose_name = "Perfil"
        verbose_name_plural = "Perfiles"
        ordering = ["-id"]

    def __str__(self):
        return self.user_FK.username
