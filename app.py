# app.py
from flask import Flask, request, jsonify
from datetime import datetime, timedelta
import sqlite3
import os

app = Flask(__name__)

# v2: グリッド対応した DB（古い pings.db とは別ファイルにする）
DB_PATH = os.path.join(os.path.dirname(__file__), "pings_v2.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT,
            region_code TEXT,
            city_name TEXT,
            grid_id TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


@app.route("/health")
def health():
    return jsonify(
        {"status": "ok", "time": datetime.utcnow().isoformat() + "Z"}
    )


# 緯度経度 → グリッドID（ざっくり5〜10km）
def make_grid_id(lat: float, lng: float, grid_size: float = 0.1) -> str:
    """
    grid_size=0.1度 ≒ 緯度方向で約11km
    ここはあとで調整してOK
    """
    lat_index = round(lat / grid_size)
    lng_index = round(lng / grid_size)
    return f"{lat_index}:{lng_index}"


@app.route("/api/pings", methods=["POST"])
def create_ping():
    data = request.get_json(force=True, silent=True) or {}

    status = data.get("status") or "unknown"
    region_code = data.get("region_code") or "unknown"
    city_name = data.get("city_name")
    lat = data.get("lat")
    lng = data.get("lng")

    grid_id = None
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        grid_id = make_grid_id(float(lat), float(lng))

    created_at = datetime.utcnow().isoformat()

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pings (status, region_code, city_name, grid_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (status, region_code, city_name, grid_id, created_at),
    )
    conn.commit()
    conn.close()

    return jsonify({"ok": True})


@app.route("/api/pings/summary")
def summary():
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=30)
    cutoff_iso = cutoff.isoformat()

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT region_code, COUNT(*) AS count
        FROM pings
        WHERE created_at >= ?
        GROUP BY region_code
        """,
        (cutoff_iso,),
    )
    rows = cur.fetchall()
    conn.close()

    result = [
        {"region_code": row["region_code"], "count": row["count"]}
        for row in rows
    ]
    return jsonify(result)


if __name__ == "__main__":
    # ローカルテスト用
    app.run(debug=True)
