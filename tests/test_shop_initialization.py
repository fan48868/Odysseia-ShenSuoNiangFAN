from unittest.mock import AsyncMock
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
pytest.importorskip("sqlalchemy")

from src.chat.features.odysseia_coin.service.coin_service import CoinService
from src.chat.features.odysseia_coin.service.shop_service import ShopService


class _FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class _FakeSession:
    def __init__(self, existing_names=None):
        self.existing_names = existing_names or []
        self.added_items = []
        self.commit_called = False

    async def execute(self, _query):
        return _FakeScalarResult(self.existing_names)

    def add(self, item):
        self.added_items.append(item)

    async def commit(self):
        self.commit_called = True


class _FakeSessionFactory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_seed_default_shop_items_if_empty(monkeypatch: pytest.MonkeyPatch):
    from src.chat.features.odysseia_coin.service import coin_service as coin_service_module
    from src.chat.config import shop_config

    fake_session = _FakeSession()
    monkeypatch.setattr(
        coin_service_module,
        "AsyncSessionLocal",
        _FakeSessionFactory(fake_session),
    )
    monkeypatch.setattr(
        shop_config,
        "SHOP_ITEMS",
        [
            ("测试商品A", "描述A", 10, "分类A", "self", "effect_a"),
            ("测试商品B", "描述B", 20, "分类B", "ai", None),
        ],
    )
    monkeypatch.setattr(
        shop_config,
        "BRAIN_GIRL_EATING_IMAGES",
        {"测试商品A": ["https://example.com/a.png"]},
    )

    service = CoinService()
    seeded_count = await service.seed_default_shop_items_if_empty()

    assert seeded_count == 2
    assert fake_session.commit_called is True
    assert [item.name for item in fake_session.added_items] == ["测试商品A", "测试商品B"]
    assert fake_session.added_items[0].cg_url == ["https://example.com/a.png"]
    assert fake_session.added_items[1].cg_url is None


@pytest.mark.asyncio
async def test_seed_default_shop_items_if_empty_skips_existing(
    monkeypatch: pytest.MonkeyPatch,
):
    from src.chat.features.odysseia_coin.service import coin_service as coin_service_module

    fake_session = _FakeSession(existing_names=["已有商品"])
    monkeypatch.setattr(
        coin_service_module,
        "AsyncSessionLocal",
        _FakeSessionFactory(fake_session),
    )

    service = CoinService()
    seeded_count = await service.seed_default_shop_items_if_empty()

    assert seeded_count == 0
    assert fake_session.commit_called is False
    assert fake_session.added_items == []


@pytest.mark.asyncio
async def test_prepare_shop_data_seeds_items_when_empty(monkeypatch: pytest.MonkeyPatch):
    from src.chat.features.odysseia_coin.service import shop_service as shop_service_module

    service = ShopService()
    get_all_items = AsyncMock(
        side_effect=[
            [],
            [
                {
                    "item_id": 1,
                    "name": "测试商品",
                    "description": "测试描述",
                    "price": 10,
                    "category": "测试分类",
                    "target": "self",
                    "effect_id": None,
                    "cg_url": None,
                    "is_available": 1,
                }
            ],
        ]
    )

    monkeypatch.setattr(shop_service_module.coin_service, "get_balance", AsyncMock(return_value=99))
    monkeypatch.setattr(shop_service_module.coin_service, "get_all_items", get_all_items)
    monkeypatch.setattr(
        shop_service_module.coin_service,
        "seed_default_shop_items_if_empty",
        AsyncMock(return_value=1),
    )
    monkeypatch.setattr(
        shop_service_module.chat_db_manager,
        "get_user_profile",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(shop_service_module.event_service, "get_active_event", lambda: None)
    monkeypatch.setattr(ShopService, "_get_shop_announcement", lambda self: None)

    shop_data = await service.prepare_shop_data(123)

    assert shop_data.balance == 99
    assert len(shop_data.items) == 1
    assert shop_data.items[0]["name"] == "测试商品"
    assert get_all_items.await_count == 2
