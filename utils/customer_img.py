import logging
import os
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError

ImageFile.LOAD_TRUNCATED_IMAGES = True


def prepare_profile_image(upload, size=300):
    """Decodificar/recortar antes de escribir DB o almacenamiento."""
    upload.seek(0)
    with Image.open(upload) as source:
        source.load()
        image_format = source.format
        fitted = ImageOps.fit(source, (size, size), method=Image.Resampling.BILINEAR)
        try:
            output = BytesIO()
            fitted.save(output, format=image_format)
            return ContentFile(output.getvalue())
        finally:
            fitted.close()


def delete_profile_image(storage, name):
    """Compensación/borrado tras commit; nunca elimina el avatar compartido."""
    if name and name != "profile.webp":
        try:
            storage.delete(name)
        except OSError:
            # No incluir nombre de archivo, usuario ni datos de formulario.
            logging.getLogger(__name__).error(
                "No se pudo retirar una imagen de perfil del almacenamiento."
            )


def discard_new_profile_image(profile, previous_name):
    image = profile.image
    if image and image._committed and image.name != previous_name:
        delete_profile_image(image.storage, image.name)


def resize_image(img_path, img_size):
    try:
        with Image.open(img_path) as img:
            ancho, alto = img.size
            if alto != img_size or ancho != img_size:
                if ancho > alto:
                    nuevo_alto = img_size
                    nuevo_ancho = int((ancho / alto) * nuevo_alto)
                    img = img.resize(
                        (nuevo_ancho, nuevo_alto), Image.Resampling.BILINEAR
                    )
                elif alto > ancho:
                    nuevo_ancho = img_size
                    nuevo_alto = int((alto / ancho) * nuevo_ancho)
                    img = img.resize(
                        (nuevo_ancho, nuevo_alto), Image.Resampling.BILINEAR
                    )
                else:
                    img.thumbnail((img_size, img_size))
                img.save(img_path)
    except (FileExistsError, UnidentifiedImageError):
        print("Error al Redimensionar la imagen")


def crop_image(img_path, img_size):
    try:
        with Image.open(img_path) as img:
            ancho, alto = img.size
            if alto != img_size or ancho != img_size:
                lado = min(ancho, alto)
                left = (ancho - lado) / 2
                top = (alto - lado) / 2
                right = (ancho + lado) / 2
                bottom = (alto + lado) / 2
                img = img.crop((left, top, right, bottom))
                img.save(img_path)
    except (FileExistsError, UnidentifiedImageError):
        print("Erro al Recortar la imagen")


def handle_old_image(modelo, pk, image):
    default_image = "profile.webp"
    old_profile = modelo.objects.get(pk=pk)
    default_image_path = os.path.join(settings.MEDIA_ROOT, default_image)

    if (
        old_profile.image.path != image.path
        and old_profile.image.path != default_image_path
    ):
        default_storage.delete(old_profile.image.path)


def upload_to_s3(img, s3_path):
    print(f"Esta en el otro archivo {img} {s3_path}")
