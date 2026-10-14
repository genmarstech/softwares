"""
Phase 4C — product image upload validation: real content (not just a
renamed file), size, and dimensions. Authorization is covered elsewhere
(test_authorization_roles.py — only owner/manager can upload at all); this
file is specifically about what happens to the bytes once someone who's
allowed to upload actually does.
"""

import io

import pytest
from PIL import Image


def _png_bytes(width=20, height=20):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color="blue").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.read()


@pytest.mark.django_db
def test_a_genuine_small_image_is_accepted(business_owner_client, product_with_stock):
    product, _ = product_with_stock()
    upload = io.BytesIO(_png_bytes())
    upload.name = "product.png"

    response = business_owner_client.post(
        "/api/genmars-store/product-images/",
        {"product": product.id, "image": upload, "sort_order": 0},
        format="multipart",
    )
    assert response.status_code == 201


@pytest.mark.django_db
def test_a_renamed_non_image_file_is_rejected(business_owner_client, product_with_stock):
    """Pillow has to actually open it — a .txt file wearing a .png name
    doesn't pass just because of its extension or declared content-type."""
    product, _ = product_with_stock()
    upload = io.BytesIO(b"this is not an image, just text pretending to be one")
    upload.name = "fake.png"

    response = business_owner_client.post(
        "/api/genmars-store/product-images/",
        {"product": product.id, "image": upload, "sort_order": 0},
        format="multipart",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_an_oversized_file_is_rejected(business_owner_client, product_with_stock, settings):
    settings.PRODUCT_IMAGE_MAX_BYTES = 10  # smaller than any real PNG, cheap to trigger
    product, _ = product_with_stock()
    upload = io.BytesIO(_png_bytes())
    upload.name = "product.png"

    response = business_owner_client.post(
        "/api/genmars-store/product-images/",
        {"product": product.id, "image": upload, "sort_order": 0},
        format="multipart",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_an_oversized_canvas_is_rejected(business_owner_client, product_with_stock, settings):
    settings.PRODUCT_IMAGE_MAX_DIMENSION_PX = 10  # the 20x20 test image exceeds this
    product, _ = product_with_stock()
    upload = io.BytesIO(_png_bytes(width=20, height=20))
    upload.name = "product.png"

    response = business_owner_client.post(
        "/api/genmars-store/product-images/",
        {"product": product.id, "image": upload, "sort_order": 0},
        format="multipart",
    )
    assert response.status_code == 400
