"""Preflight de solo lectura; nunca migra ni resuelve datos del propietario."""

from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.db.models.functions import Lower


class Command(BaseCommand):
    help = (
        "Detecta colisiones antes de normalizar correos con allauth; no modifica datos."
    )

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default")

    def handle(self, *args, **options):
        alias = options["database"]
        emails = EmailAddress.objects.using(alias)
        duplicates = (
            emails.annotate(normalizado=Lower("email"))
            .values("user_id", "normalizado")
            .annotate(total=Count("pk"))
            .filter(total__gt=1)
            .count()
        )
        verified = (
            emails.filter(verified=True)
            .annotate(normalizado=Lower("email"))
            .values("normalizado")
            .annotate(total=Count("pk"))
            .filter(total__gt=1)
            .count()
        )
        users = (
            get_user_model()
            .objects.using(alias)
            .exclude(email="")
            .annotate(normalizado=Lower("email"))
            .values("normalizado")
            .annotate(total=Count("pk"))
            .filter(total__gt=1)
            .count()
        )
        multiple_primary = (
            emails.filter(primary=True)
            .values("user_id")
            .annotate(total=Count("pk"))
            .filter(total__gt=1)
            .count()
        )
        authenticators = Authenticator.objects.using(alias)
        unsupported_mfa = authenticators.exclude(
            type__in=("totp", "recovery_codes")
        ).count()
        duplicated_mfa = (
            authenticators.values("user_id", "type")
            .annotate(total=Count("pk"))
            .filter(total__gt=1)
            .count()
        )
        # Solo cantidades: nunca direcciones, nombres de usuarios ni credenciales.
        self.stdout.write(
            f"Grupos duplicados por usuario/correo: {duplicates}; verificados: {verified}; "
            f"User.email: {users}; usuarios con varios principales: {multiple_primary}; "
            f"MFA no admitidos: {unsupported_mfa}; grupos MFA duplicados: {duplicated_mfa}."
        )
        if duplicates or verified or users or unsupported_mfa or duplicated_mfa:
            raise CommandError(
                "Revisar las colisiones o estados MFA con el propietario antes de migrar. "
                "No se modificaron datos ni se descartaron cuentas o correos."
            )
        self.stdout.write(
            self.style.SUCCESS(
                "Sin colisiones detectadas. No equivale a autorizar migraciones; verificar respaldo y recuperación."
            )
        )
