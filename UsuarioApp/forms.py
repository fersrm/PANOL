from django import forms
from django.contrib.auth.forms import SetPasswordForm, UserCreationForm
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.views.decorators.debug import sensitive_variables

from .models import Position, Profile, UserVisualPreference
from .permissions import cargos_asignables


class VisualModeForm(forms.Form):
    mode = forms.ChoiceField(choices=UserVisualPreference.Mode.choices)


class AdministrativePasswordForm(SetPasswordForm):
    admin_password = forms.CharField(
        label="Tu contraseña de administrador",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    field_order = ["admin_password", "new_password1", "new_password2"]

    def __init__(self, user, *args, actor, **kwargs):
        self.actor = actor
        super().__init__(user, *args, **kwargs)
        self.fields["new_password1"].label = "Nueva contraseña del usuario"
        self.fields["new_password2"].label = "Repite la nueva contraseña"
        self.fields[
            "new_password2"
        ].help_text = "Escribe la misma contraseña para confirmar."
        for name in ("new_password1", "new_password2"):
            self.fields[name].required = True

    @sensitive_variables()
    def clean_admin_password(self):
        password = self.cleaned_data["admin_password"]
        if not self.actor.check_password(password):
            raise forms.ValidationError(
                "Tu contraseña de administrador no es correcta."
            )
        return password


class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "first_name",
            "last_name",
        ]


class UserCreateForm(UserCreationForm, UserUpdateForm):
    password1 = forms.PasswordInput()
    password2 = forms.PasswordInput()

    class Meta:
        model = User
        fields = UserUpdateForm.Meta.fields + ["password1", "password2"]

    def clean_password1(self):
        password1 = self.cleaned_data.get("password1")
        validate_password(password1)
        return password1

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")

        if password1 and password2 and password1 != password2:
            raise ValidationError("Passwords do not match")

        return cleaned_data


class ProfileUpdateForm(forms.ModelForm):
    image = forms.ImageField(
        label="Imagen",
        widget=forms.FileInput(
            attrs={
                "class": "file-input",
                "id": "id_image",
                "accept": "image/*",
                "tabindex": "-1",
            }
        ),
        required=False,
    )

    class Meta:
        model = Profile
        fields = ["image"]

    def clean_image(self):
        image = self.cleaned_data.get("image")
        # La imagen inicial puede ser el avatar predeterminado, sin archivo en media.
        if isinstance(image, UploadedFile) and image.size > 5 * 1024 * 1024:
            raise forms.ValidationError(
                "El tamaño del archivo de imagen no debe exceder los 5 MB."
            )
        return image


class ProfileCreateForm(ProfileUpdateForm):
    position_FK = forms.ModelChoiceField(
        label="Cargo",
        queryset=Position.objects.none(),
        widget=forms.Select(
            attrs={
                "class": "bg-surface text-ink border border-line rounded-lg py-2 px-4 block w-full mb-3"
            }
        ),
    )

    class Meta(ProfileUpdateForm.Meta):
        fields = ProfileUpdateForm.Meta.fields + ["position_FK"]

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["position_FK"].queryset = Position.objects.filter(
            permission_code__in=cargos_asignables(actor)
        )
