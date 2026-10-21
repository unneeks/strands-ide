"""A small Flask demo app for the Strands IDE.

Run:  python app.py   -> http://localhost:5000
"""
from __future__ import annotations

from flask import Flask, jsonify, request

app = Flask(__name__)

# In-memory store for the demo.
_ITEMS: list[dict] = [
    {"id": 1, "name": "welcome item"},
]


@app.get("/")
def index() -> dict:
    """Root: list available routes."""
    return jsonify(
        {
            "service": "strands-ide-demo",
            "routes": ["/", "/items", "/items/<id>", "/add/<a>/<b>", "/div/<a>/<b>"],
        }
    )


@app.get("/items")
def list_items() -> dict:
    """Return all items."""
    return jsonify({"items": _ITEMS})


@app.get("/items/<int:item_id>")
def get_item(item_id: int):
    item = next((it for it in _ITEMS if it["id"] == item_id), None)
    if item is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(item)


@app.post("/items")
def add_item():
    """Add an item. Body: {"name": "..."}"""
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    if not name:
        return jsonify({"error": "name is required"}), 400
    new_id = (max((it["id"] for it in _ITEMS), default=0) + 1)
    item = {"id": new_id, "name": name}
    _ITEMS.append(item)
    return jsonify(item), 201


@app.get("/add/<a>/<b>")
def add(a, b) -> dict:
    return jsonify({"a": float(a), "b": float(b), "sum": float(a) + float(b)})


@app.get("/div/<a>/<b>")
def divide(a, b):
    # NOTE: division by zero is not handled here — a good task for the agent.
    a, b = float(a), float(b)
    return jsonify({"a": a, "b": b, "quotient": a / b})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
