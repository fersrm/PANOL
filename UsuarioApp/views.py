from allauth.account.models import EmailAddress
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.generic import ListView, View

from utils.customer_img import discard_new_profile_image

from .forms import (
    AdministrativePasswordForm,
    ProfileCreateForm,
    ProfileUpdateForm,
    UserCreateForm,
    UserUpdateForm,
    VisualModeForm,
)
from .models import Profile, UserVisualPreference
from .permissions import (
    AccountManagementMixin,
    UserRegistrationMixin,
    puede_gestionar_cuenta,
)


class VisualModeUpdateView(LoginRequiredMixin, View):
    http_method_names = ["post", "options"]

    def handle_no_permission(self):
        # El retorno después del login debe admitir GET.
        return redirect_to_login(reverse("Home"), self.get_login_url())

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.is_active:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        form = VisualModeForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                "400.html",
                {
                    "visual_mode_error": (
                        "Elige modo claro u oscuro desde el botón de Inicio. "
                        "Tu configuración no cambió."
                    )
                },
                status=400,
            )
        try:
            UserVisualPreference.objects.update_or_create(
                user=request.user,
                defaults={"mode": form.cleaned_data["mode"]},
            )
        except (IntegrityError, OperationalError):
            # update_or_create revierte su transacción antes de renderizar.
            return render(
                request,
                "500.html",
                {
                    "visual_mode_error": (
                        "No pudimos guardar el modo. Tu configuración no cambió. "
                        "Vuelve a Inicio e inténtalo otra vez."
                    )
                },
                status=500,
            )
        return redirect("Home")


class UserListView(LoginRequiredMixin, ListView):
    model = User
    template_name = "pages/UsuarioApp/usuarios_lista.html"
    context_object_name = "users"
    paginate_by = 9

    def get_queryset(self):
        show_inactive = self.request.GET.get("inactive") == "1"

        queryset = (
            User.objects.filter(is_active=not show_inactive)
            .select_related("profile__position_FK")
            .order_by("-id")
        )

        search_query = self.request.GET.get("search")

        if search_query:
            queryset = queryset.filter(
                Q(username__icontains=search_query)
                | Q(first_name__icontains=search_query)
                | Q(last_name__icontains=search_query)
            )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        verification_users = []
        for user in context["users"]:
            user.can_activate = puede_gestionar_cuenta(
                self.request.user, user, "activate"
            )
            user.can_deactivate = puede_gestionar_cuenta(
                self.request.user, user, "deactivate"
            )
            user.can_reset_password = puede_gestionar_cuenta(
                self.request.user, user, "reset"
            )
            verification = EmailAddress.objects.filter(
                user=user, verified=True
            ).exists()
            verification_users.append((user, verification))

        context["verification_users"] = verification_users
        context["placeholder"] = "Buscar por usuario, nombre o apellido"
        context["search_query"] = self.request.GET.get("search", "")
        context["show_inactive"] = self.request.GET.get("inactive") == "1"

        return context


class UserDeactivateView(AccountManagementMixin, View):
    account_action = "deactivate"
    active = False

    def post(self, request, pk, *args, **kwargs):
        try:
            with transaction.atomic():
                user = get_object_or_404(User.objects.select_for_update(), pk=pk)
                if not puede_gestionar_cuenta(
                    self.account_actor, user, self.account_action
                ):
                    raise PermissionDenied
                user.is_active = self.active
                user.save(update_fields=["is_active"])
        except (IntegrityError, OperationalError):
            return render(
                request,
                "500.html",
                {
                    "account_management_error": (
                        "No pudimos cambiar el estado de la cuenta. Sus datos no cambiaron. "
                        "Vuelve al listado e inténtalo nuevamente."
                    )
                },
                status=500,
            )
        messages.success(
            request,
            "Usuario activado correctamente."
            if self.active
            else "Usuario desactivado correctamente.",
        )
        return redirect("User")


class UserActivateView(UserDeactivateView):
    account_action = "activate"
    active = True


@method_decorator(never_cache, name="dispatch")
@method_decorator(
    sensitive_post_parameters("admin_password", "new_password1", "new_password2"),
    name="dispatch",
)
class AdministrativePasswordView(AccountManagementMixin, View):
    account_action = "reset"
    template_name = "pages/UsuarioApp/restablecer_password.html"

    def target(self, pk, *, lock=False):
        queryset = User.objects.select_for_update() if lock else User.objects.all()
        user = get_object_or_404(queryset, pk=pk)
        if not puede_gestionar_cuenta(self.account_actor, user, self.account_action):
            raise PermissionDenied
        return user

    def get(self, request, pk):
        user = self.target(pk)
        form = AdministrativePasswordForm(user, actor=self.account_actor)
        return render(request, self.template_name, {"form": form, "target_user": user})

    @sensitive_variables()
    def post(self, request, pk):
        try:
            with transaction.atomic():
                user = self.target(pk, lock=True)
                form = AdministrativePasswordForm(
                    user, request.POST, actor=self.account_actor
                )
                if form.is_valid():
                    user = form.save(commit=False)
                    user.save(update_fields=["password"])
                    saved = True
                else:
                    saved = False
        except (IntegrityError, OperationalError):
            return render(
                request,
                "500.html",
                {
                    "account_management_error": (
                        "No pudimos restablecer la contraseña. La cuenta conserva su contraseña "
                        "anterior. Vuelve al listado e inténtalo nuevamente."
                    )
                },
                status=500,
            )
        if saved:
            messages.success(
                request,
                (
                    "Contraseña restablecida. Las sesiones anteriores de esa cuenta "
                    "dejarán de ser válidas."
                ),
            )
            return redirect("User")
        return render(request, self.template_name, {"form": form, "target_user": user})


@method_decorator(sensitive_post_parameters("password1", "password2"), name="dispatch")
class UserCreateView(UserRegistrationMixin, View):
    template_name = "pages/UsuarioApp/registro_usuario.html"

    def get(self, request, *args, **kwargs):
        user_form = UserCreateForm()
        profile_form = ProfileCreateForm(actor=self.registration_actor)

        context = {"user_form": user_form, "profile_form": profile_form}

        return render(request, self.template_name, context)

    @sensitive_variables()
    def post(self, request, *args, **kwargs):
        user_form = UserCreateForm(request.POST)
        profile_form = ProfileCreateForm(
            request.POST, request.FILES, actor=self.registration_actor
        )

        if user_form.is_valid() and profile_form.is_valid():
            profile = profile_form.save(commit=False)
            try:
                with transaction.atomic():
                    user = user_form.save()
                    profile.user_FK = user
                    profile.save()
            except (IntegrityError, OperationalError, OSError):
                discard_new_profile_image(profile, "profile.webp")
                return render(
                    request,
                    "500.html",
                    {
                        "account_management_error": (
                            "No pudimos registrar el usuario. No se creó la cuenta. Inténtalo nuevamente."
                        )
                    },
                    status=500,
                )
            except Exception:
                discard_new_profile_image(profile, "profile.webp")
                raise
            messages.success(request, "Usuario creado con éxito.")
            return redirect("User")

        context = {"user_form": user_form, "profile_form": profile_form}

        return render(request, self.template_name, context)


class ProfileUpdateView(LoginRequiredMixin, View):
    template_name = "pages/UsuarioApp/perfil/perfil.html"

    def get(self, request, *args, **kwargs):
        user = request.user
        profile, _ = Profile.objects.get_or_create(user_FK=user)
        user_form = UserUpdateForm(instance=user)
        profile_form = ProfileUpdateForm(instance=profile)

        context = {"user_form": user_form, "profile_form": profile_form}

        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        user = request.user
        profile = Profile.objects.filter(user_FK=user).first() or Profile(user_FK=user)
        previous_image = profile.image.name
        user_form = UserUpdateForm(request.POST, instance=user)
        profile_form = ProfileUpdateForm(request.POST, request.FILES, instance=profile)

        if user_form.is_valid() and profile_form.is_valid():
            try:
                with transaction.atomic():
                    user_form.save()
                    profile_form.save()
            except (IntegrityError, OperationalError, OSError):
                discard_new_profile_image(profile, previous_image)
                return render(
                    request,
                    "500.html",
                    {
                        "account_management_error": (
                            "No pudimos actualizar el perfil. Sus datos y fotografía no cambiaron. Inténtalo nuevamente."
                        )
                    },
                    status=500,
                )
            except Exception:
                discard_new_profile_image(profile, previous_image)
                raise
            messages.success(request, "Perfil actualizado con éxito.")
            return redirect("Profile")

        context = {"user_form": user_form, "profile_form": profile_form}

        return render(request, self.template_name, context)
