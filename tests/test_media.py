import boto3
import pytest
from moto import mock_aws
from sqlalchemy import func, select

from marketplace.b2b.integrations.storage import S3StorageClient
from marketplace.b2b.models.entities import Media
from marketplace.shared.errors import DomainError
from tests.conftest import onboard, png


@pytest.mark.parametrize(
    "body,mime",
    [
        (b"<svg><script>alert(1)</script></svg>", "image/svg+xml"),
        (b"not an image", "image/png"),
        (png(), "image/jpeg"),
        (png()[:25], "image/png"),
    ],
)
async def test_bad_upload(cluster, body, mime):
    await onboard(cluster.client)
    response = await cluster.client.post(
        "/api/uploads", files={"file": ("../../bad.png", body, mime)}
    )
    assert response.status_code == 400, response.text
    async with cluster.apps["b2b"].state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Media)) == 0
    assert not cluster.apps["b2b"].state.storage.objects


async def test_oversized_and_storage_failure(cluster):
    await onboard(cluster.client)
    settings = cluster.apps["b2b"].state.settings
    settings.max_upload_bytes = 10
    response = await cluster.client.post(
        "/api/uploads", files={"file": ("a.png", png(), "image/png")}
    )
    assert response.status_code == 400
    settings.max_upload_bytes = 5242880

    async def fail(*args):
        raise DomainError(503, "DEPENDENCY_UNAVAILABLE", "Storage unavailable")

    cluster.apps["b2b"].state.storage.upload = fail
    response = await cluster.client.post(
        "/api/uploads", files={"file": ("a.png", png(), "image/png")}
    )
    assert response.status_code == 503
    async with cluster.apps["b2b"].state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Media)) == 0


async def test_real_s3_adapter_roundtrip_and_error(cluster):
    settings = cluster.apps["b2b"].state.settings
    settings.s3_endpoint = "https://s3.amazonaws.com"
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=settings.s3_bucket)
        storage = S3StorageClient(settings)
        await storage.upload("safe/file.png", png(), "image/png")
        body = client.get_object(Bucket=settings.s3_bucket, Key="safe/file.png")
        assert body["ContentType"] == "image/png"
        assert body["Body"].read() == png()
        await storage.delete("safe/file.png")
        assert client.list_objects_v2(Bucket=settings.s3_bucket)["KeyCount"] == 0
        storage.bucket = "missing-bucket"
        with pytest.raises(DomainError) as error:
            await storage.upload("image.png", png(), "image/png")
        assert error.value.status == 503
        storage.client.close()


async def test_unsupported_raster_and_pixel_limit(cluster, monkeypatch):
    import io

    from PIL import Image

    await onboard(cluster.client)
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "GIF")
    response = await cluster.client.post(
        "/api/uploads", files={"file": ("a.gif", buf.getvalue(), "image/gif")}
    )
    assert response.status_code == 400
    monkeypatch.setattr("marketplace.b2b.services.media.MAX_PIXELS", 1)
    response = await cluster.client.post(
        "/api/uploads", files={"file": ("a.png", png(), "image/png")}
    )
    assert response.status_code == 400


async def test_metadata_failure_compensates_object_upload(cluster, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    await onboard(cluster.client)
    original = AsyncSession.flush

    async def flush(self, *args, **kwargs):
        if any(isinstance(item, Media) for item in self.new):
            raise RuntimeError("Metadata persistence failed")
        return await original(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "flush", flush)
        response = await cluster.client.post(
            "/api/uploads", files={"file": ("a.png", png(), "image/png")}
        )
    assert response.status_code == 500
    assert cluster.apps["b2b"].state.storage.objects == {}
