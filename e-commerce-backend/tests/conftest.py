import pytest
from rest_framework.test import APIClient

from accounts.models import User
from cart.models import Cart, CartItem
from catalog.models import Product
from inventory.models import InventoryRecord


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def make_user(db):
    def _make(email="customer@example.com", password="a-strong-passw0rd!", **kwargs):
        return User.objects.create_user(email=email, password=password, **kwargs)

    return _make


@pytest.fixture
def customer(make_user):
    return make_user()


@pytest.fixture
def staff_user(make_user):
    return make_user(email="staff@example.com", is_staff=True)


@pytest.fixture
def auth_client(api_client, customer):
    api_client.force_authenticate(user=customer)
    return api_client


@pytest.fixture
def staff_client(api_client, staff_user):
    api_client.force_authenticate(user=staff_user)
    return api_client


@pytest.fixture
def product_with_stock(db):
    def _make(price="500.00", quantity=10, sku="SKU-1", name="Test Product"):
        product = Product.objects.create(
            name=name, sku=sku, price=price, status=Product.Status.PUBLISHED
        )
        record = InventoryRecord.objects.create(product=product, quantity=quantity)
        return product, record

    return _make


@pytest.fixture
def cart_with_item(db, customer, product_with_stock):
    product, record = product_with_stock()
    cart = Cart.objects.create(user=customer)
    CartItem.objects.create(cart=cart, product=product, quantity=2)
    return cart, product, record
