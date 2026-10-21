"""Tests for the demo Flask app."""
from __future__ import annotations

import pytest

from app import app


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["service"] == "strands-ide-demo"
    assert "routes" in data


def test_list_items(client):
    resp = client.get("/items")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data["items"], list)
    assert len(data["items"]) >= 1


def test_add_and_get_item(client):
    resp = client.post("/items", json={"name": "created by test"})
    assert resp.status_code == 201
    created = resp.get_json()
    got = client.get(f"/items/{created['id']}")
    assert got.status_code == 200
    assert got.get_json()["name"] == "created by test"


def test_add_requires_name(client):
    resp = client.post("/items", json={})
    assert resp.status_code == 400


def test_addition(client):
    resp = client.get("/add/2/3")
    assert resp.status_code == 200
    assert resp.get_json()["sum"] == 5.0


def test_divide(client):
    resp = client.get("/div/10/2")
    assert resp.status_code == 200
    assert resp.get_json()["quotient"] == 5.0
