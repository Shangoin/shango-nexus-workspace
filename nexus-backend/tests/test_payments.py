"""tests/test_payments.py — Sprint 2 S2-05"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient


def _make_app():
    from fastapi import FastAPI
    from api.payments import router
    app = FastAPI()
    app.include_router(router, prefix="/api/payments")
    return app


def test_list_products_returns_all():
    client = TestClient(_make_app())
    resp = client.get("/api/payments/products")
    assert resp.status_code == 200
    products = resp.json()["products"]
    assert "aurora_pro" in products
    assert "nexus_pro" in products


def test_razorpay_create_order_returns_order_id():
    mock_order = {"id": "order_TEST123", "amount": 850000, "currency": "INR"}

    with patch("api.payments.razorpay") as mock_rz_module:
        mock_client = MagicMock()
        mock_client.order.create.return_value = mock_order
        mock_rz_module.Client.return_value = mock_client

        import os
        with patch.dict(os.environ, {"RAZORPAY_KEY_ID": "rzp_test_x", "RAZORPAY_KEY_SECRET": "secret"}):
            client = TestClient(_make_app())
            resp = client.post(
                "/api/payments/razorpay/create-order",
                json={"product_id": "aurora_pro", "user_email": "test@shango.in"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert "order_id" in data
    assert data["currency"] == "INR"
    assert data["amount"] == 8500


def test_razorpay_create_order_unknown_product():
    with patch.dict(__import__("os").environ, {"RAZORPAY_KEY_ID": "x", "RAZORPAY_KEY_SECRET": "x"}):
        client = TestClient(_make_app())
        resp = client.post(
            "/api/payments/razorpay/create-order",
            json={"product_id": "nonexistent", "user_email": "test@shango.in"},
        )
    assert resp.status_code == 404


def test_inr_prices_all_products_covered():
    from api.payments import INR_PRICES, PRODUCTS
    for product_id in PRODUCTS:
        assert product_id in INR_PRICES, f"Missing INR price for {product_id}"
