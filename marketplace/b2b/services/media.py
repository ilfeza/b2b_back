from __future__ import annotations

import asyncio
import io
import warnings
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from marketplace.b2b.models.entities import Media
from marketplace.shared.errors import invalid

FORMATS = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}
MAX_PIXELS = 20_000_000


def verify_image(body: bytes, declared_type: str):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(body)) as im:
                if im.format not in FORMATS:
                    invalid("Разрешены JPEG, PNG, WebP", "file")
                actual, extension = FORMATS[im.format]
                if declared_type != actual:
                    invalid("Content-Type не совпадает с форматом файла", "file")
                if im.width * im.height > MAX_PIXELS:
                    invalid("Слишком большое разрешение изображения", "file")
                im.verify()
            # Decode and re-encode to remove metadata, appended payloads and embedded scripts.
            with Image.open(io.BytesIO(body)) as im:
                im.load()
                clean = io.BytesIO()
                im.save(clean, format={"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}[extension])
                return clean.getvalue(), actual, extension
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        invalid("Поврежденное или опасное изображение", "file")


class MediaService:
    def __init__(self, db, state):
        self.db, self.state = db, state

    async def upload(self, store, file):
        body = await file.read(self.state.settings.max_upload_bytes + 1)
        if len(body) > self.state.settings.max_upload_bytes:
            invalid("Превышен размер файла", "file")
        clean, content_type, extension = await asyncio.to_thread(
            verify_image, body, file.content_type
        )
        if len(clean) > self.state.settings.max_upload_bytes:
            invalid("Превышен размер декодированного изображения", "file")
        key = f"{store.id}/{uuid4()}.{extension}"
        await self.state.storage.upload(key, clean, content_type)
        url = f"{self.state.settings.s3_public_url.rstrip('/')}/{key}"
        media = Media(
            owner_seller_id=store.id,
            storage_key=key,
            url=url,
            content_type=content_type,
            size=len(clean),
        )
        self.db.add(media)
        try:
            await self.db.flush()
        except Exception:
            await self.state.storage.delete(key)
            raise
        return {"id": str(media.id), "url": url, "contentType": content_type, "size": len(clean)}
