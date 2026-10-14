import pytest
from rest_framework.test import APIClient

from accounts.models import User
from businesses.models import Business, BusinessMembership
from businesses.services import create_business
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
    """
    A Genmars PLATFORM admin (`is_staff=True`) — nothing more. Deliberately
    has no BusinessMembership anywhere: per Decision 1 of the multi-tenant
    plan, that flag alone must never grant access to any one business's
    storefront or ops surface. Use `business_owner_client` for tests that
    need business-level access.
    """
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
def business(db) -> Business:
    """The one business Phase 1's migration seeds — exists in every test
    database by the time migrations have run, so this just looks it up
    rather than creating anything."""
    return Business.objects.get(slug="genmars-store")


@pytest.fixture
def second_business(db) -> Business:
    """A second, independent business — for every A/B isolation test.
    Deliberately NOT the migration-seeded one, so isolation tests exercise
    real multi-tenancy rather than two names for the same row."""
    return create_business(name="Acme Fashion", slug="acme-fashion")


@pytest.fixture
def business_owner(make_user, business):
    user = make_user(email="owner@example.com")
    BusinessMembership.objects.create(business=business, user=user, role=BusinessMembership.Role.OWNER)
    return user


@pytest.fixture
def business_owner_client(business_owner):
    # A dedicated APIClient, not the shared `api_client` fixture — a test
    # using both this and `second_business_owner_client` together would
    # otherwise force_authenticate() the same underlying client twice,
    # silently swapping which user it's actually authenticated as.
    client = APIClient()
    client.force_authenticate(user=business_owner)
    return client


@pytest.fixture
def second_business_owner(make_user, second_business):
    user = make_user(email="owner-b@example.com")
    BusinessMembership.objects.create(
        business=second_business, user=user, role=BusinessMembership.Role.OWNER
    )
    return user


@pytest.fixture
def second_business_owner_client(second_business_owner):
    client = APIClient()
    client.force_authenticate(user=second_business_owner)
    return client


@pytest.fixture
def business_manager(make_user, business):
    user = make_user(email="manager@example.com")
    BusinessMembership.objects.create(business=business, user=user, role=BusinessMembership.Role.MANAGER)
    return user


@pytest.fixture
def business_manager_client(business_manager):
    client = APIClient()
    client.force_authenticate(user=business_manager)
    return client


@pytest.fixture
def business_staff(make_user, business):
    user = make_user(email="shopfloor-staff@example.com")
    BusinessMembership.objects.create(business=business, user=user, role=BusinessMembership.Role.STAFF)
    return user


@pytest.fixture
def business_staff_client(business_staff):
    client = APIClient()
    client.force_authenticate(user=business_staff)
    return client


@pytest.fixture
def product_with_stock(business):
    def _make(price="500.00", quantity=10, sku="SKU-1", name="Test Product", status=Product.Status.PUBLISHED, business=business):
        product = Product.objects.create(
            name=name, sku=sku, price=price, status=status, business=business
        )
        record = InventoryRecord.objects.create(product=product, quantity=quantity, business=business)
        return product, record

    return _make


@pytest.fixture
def cart_with_item(business, customer, product_with_stock):
    product, record = product_with_stock()
    cart = Cart.objects.create(user=customer, business=business)
    CartItem.objects.create(cart=cart, product=product, quantity=2)
    return cart, product, record
