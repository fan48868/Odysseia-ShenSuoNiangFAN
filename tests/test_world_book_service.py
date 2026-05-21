import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

try:
    import src.chat.features.world_book.services.world_book_service as world_book_service_module
except ModuleNotFoundError as exc:
    if exc.name == "sqlalchemy":
        world_book_service_module = None
    else:
        raise


class _FakeScalarResult:
    def __init__(self, profile):
        self._profile = profile

    def scalars(self):
        return self

    def first(self):
        return self._profile


class _FakeBegin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, profiles=None):
        self._profiles = list(profiles or [])
        self.added = []
        self.flush_called = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def begin(self):
        return _FakeBegin()

    async def execute(self, stmt):
        profile = self._profiles.pop(0) if self._profiles else None
        return _FakeScalarResult(profile)

    def add(self, profile):
        if getattr(profile, "id", None) is None:
            profile.id = 777
        self.added.append(profile)

    async def flush(self):
        self.flush_called = True


class _FakeSessionFactory:
    def __init__(self, sessions):
        self._sessions = list(sessions)

    def __call__(self):
        if not self._sessions:
            raise AssertionError("No fake session left")
        return self._sessions.pop(0)


@pytest.mark.asyncio
async def test_get_profile_by_discord_id_reads_async_session(
    monkeypatch: pytest.MonkeyPatch,
):
    if world_book_service_module is None:
        pytest.skip("sqlalchemy is not installed in the current environment.")

    service = world_book_service_module.WorldBookService(
        world_book_service_module.gemini_service
    )
    profile = SimpleNamespace(
        id=10,
        discord_id="123",
        title="Tester",
        personal_summary="记忆",
        source_metadata={"name": "Tester", "preferences": "茶"},
    )

    monkeypatch.setattr(
        world_book_service_module,
        "AsyncSessionLocal",
        _FakeSessionFactory([_FakeSession([profile])]),
    )

    result = await service.get_profile_by_discord_id(123, auto_create=False)

    assert result is not None
    assert result["discord_id"] == "123"
    assert result["title"] == "Tester"
    assert result["personal_summary"] == "记忆"
    assert result["name"] == "Tester"
    assert result["preferences"] == "茶"


@pytest.mark.asyncio
async def test_auto_create_minimal_member_profile_uses_async_session(
    monkeypatch: pytest.MonkeyPatch,
):
    if world_book_service_module is None:
        pytest.skip("sqlalchemy is not installed in the current environment.")

    service = world_book_service_module.WorldBookService(
        world_book_service_module.gemini_service
    )
    create_session = _FakeSession([None])
    process_member_mock = AsyncMock(return_value=True)

    monkeypatch.setattr(
        world_book_service_module,
        "AsyncSessionLocal",
        _FakeSessionFactory([create_session]),
    )
    monkeypatch.setattr(
        world_book_service_module.incremental_rag_service,
        "process_community_member",
        process_member_mock,
    )

    created = await service._auto_create_minimal_member_profile(
        discord_id=456,
        user_name="Auto User",
    )

    assert created is True
    assert create_session.flush_called is True
    assert len(create_session.added) == 1
    created_profile = create_session.added[0]
    assert created_profile.discord_id == "456"
    assert created_profile.title == "Auto User"
    assert created_profile.source_metadata["source"] == "auto_create"
    await asyncio.sleep(0)
    process_member_mock.assert_awaited_once_with("777")
