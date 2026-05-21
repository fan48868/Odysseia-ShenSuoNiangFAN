import pytest
import sys
import os
from unittest.mock import AsyncMock, patch

# Add project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.chat.features.odysseia_coin.service.coin_service import coin_service
from src.chat.config.chat_config import COIN_CONFIG


@pytest.fixture(autouse=True)
def mock_db():
    with patch(
        "src.chat.utils.database.chat_db_manager._execute", new_callable=AsyncMock
    ) as mock_execute:
        yield mock_execute


@pytest.mark.asyncio
async def test_transfer_coins_success(mock_db):
    # Arrange
    sender_id = 1
    receiver_id = 2
    amount = 100
    tax = int(amount * COIN_CONFIG["TRANSFER_TAX_RATE"])
    total_deduction = amount + tax

    # Mock get_balance to return sufficient funds
    with patch.object(
        coin_service, "get_balance", AsyncMock(return_value=total_deduction)
    ):
        # Mock the transaction to return a new balance
        mock_db.return_value = 900 - total_deduction

        # Act
        success, message, new_balance = await coin_service.transfer_coins(
            sender_id, receiver_id, amount
        )

        # Assert
        assert success is True
        assert "转账成功" in message
        assert new_balance == 900 - total_deduction


@pytest.mark.asyncio
async def test_transfer_coins_insufficient_funds(mock_db):
    # Arrange
    sender_id = 1
    receiver_id = 2
    amount = 100

    # Mock get_balance to return insufficient funds
    with patch.object(coin_service, "get_balance", AsyncMock(return_value=50)):
        # Act
        success, message, new_balance = await coin_service.transfer_coins(
            sender_id, receiver_id, amount
        )

        # Assert
        assert success is False
        assert "余额不足" in message
        assert new_balance is None


@pytest.mark.asyncio
async def test_transfer_to_self(mock_db):
    # Act
    success, message, new_balance = await coin_service.transfer_coins(1, 1, 100)

    # Assert
    assert success is False
    assert "不能给自己转账" in message
    assert new_balance is None


@pytest.mark.asyncio
async def test_transfer_negative_amount(mock_db):
    # Act
    success, message, new_balance = await coin_service.transfer_coins(1, 2, -100)

    # Assert
    assert success is False
    assert "必须是正数" in message
    assert new_balance is None


@pytest.mark.asyncio
async def test_borrow_coins_success(mock_db):
    # Arrange
    user_id = 1
    amount = 500

    # Mock get_active_loan to return None (no active loan)
    with patch.object(coin_service, "get_active_loan", AsyncMock(return_value=None)):
        # Mock add_coins to simulate success
        with patch.object(coin_service, "add_coins", AsyncMock()):
            # Act
            success, message = await coin_service.borrow_coins(user_id, amount)

            # Assert
            assert success is True
            assert "成功借款" in message


@pytest.mark.asyncio
async def test_borrow_with_active_loan(mock_db):
    # Arrange
    user_id = 1
    amount = 500

    # Mock get_active_loan to return an active loan
    with patch.object(
        coin_service, "get_active_loan", AsyncMock(return_value={"amount": 200})
    ):
        # Act
        success, message = await coin_service.borrow_coins(user_id, amount)

        # Assert
        assert success is False
        assert "尚未还清" in message


@pytest.mark.asyncio
async def test_repay_loan_success(mock_db):
    # Arrange
    user_id = 1
    loan_amount = 200

    # Mock get_active_loan to return an active loan
    with patch.object(
        coin_service,
        "get_active_loan",
        AsyncMock(return_value={"loan_id": 1, "amount": loan_amount}),
    ):
        # Mock get_balance to return sufficient funds
        with patch.object(coin_service, "get_balance", AsyncMock(return_value=300)):
            # Mock remove_coins to simulate success
            with patch.object(
                coin_service, "remove_coins", AsyncMock(return_value=100)
            ):
                # Act
                success, message = await coin_service.repay_loan(user_id)

                # Assert
                assert success is True
                assert "成功偿还" in message


@pytest.mark.asyncio
async def test_repay_loan_no_active_loan(mock_db):
    # Arrange
    user_id = 1

    # Mock get_active_loan to return None
    with patch.object(coin_service, "get_active_loan", AsyncMock(return_value=None)):
        # Act
        success, message = await coin_service.repay_loan(user_id)

        # Assert
        assert success is False
        assert "没有需要偿还的贷款" in message


@pytest.mark.asyncio
async def test_repay_loan_insufficient_funds(mock_db):
    # Arrange
    user_id = 1
    loan_amount = 200

    # Mock get_active_loan to return an active loan
    with patch.object(
        coin_service,
        "get_active_loan",
        AsyncMock(return_value={"loan_id": 1, "amount": loan_amount}),
    ):
        # Mock get_balance to return insufficient funds
        with patch.object(coin_service, "get_balance", AsyncMock(return_value=100)):
            # Act
            success, message = await coin_service.repay_loan(user_id)

            # Assert
            assert success is False
            assert "余额不足" in message
