# app.py
import os
import sqlite3
from datetime import datetime, timedelta

from functools import wraps
from flask import Flask, request, jsonify, render_template, Response

app = Flask(__name__)

# Basic認証用のチェック関数
def check_auth(username: str, password: str) -> bool:
    """環境変数に設定したユーザー名・パスワードと比較"""
    admin_user = os.environ.get("ADMIN_USER", "admin")
    admin_pass = os.environ.get("ADMIN_PASS", "changeme")
    return username == admin_user and password == admin_pass

def authenticate():
    """401 を返してブラウザにBasic認証ダイアログを出させる"""
    return Response(
        "Authentication required", 401,
        {"WWW-Authenticate": 'Basic realm="HerePing Admin"'}
    )

def requires_auth(f):
    """/admin/dashboard 用のデコレーター"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# 管理用の簡易シークレット（本番では環境変数で上書き推奨）
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "dev-secret")

# v1で許可するステータス
ALLOWED_STATUS = {"awake", "free", "cantSleep", "working"}

# --- DB 周り ----------------------------------------------------

# pings_v2.db をこのファイルと同じディレクトリに作る
DB_PATH = os.path.join(os.path.dirname(__file__), "pings_v2.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    # 以前 grid_id で作っていた人は、pings_v2.db を一度消してからこれを実行すると楽です
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT,
            status TEXT,
            region_code TEXT,
            city_name TEXT,
            area_code TEXT,
            lat REAL,
            lng REAL,
            message TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


init_db()

# --- ヘルスチェック ---------------------------------------------


@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat() + "Z"})


# --- 緯度経度 → area_code（ざっくり5〜10km） --------------------


def compute_area_code(lat, lng, region_code: str) -> str:
    """
    5〜10km くらいのざっくりグリッドIDを作る簡易版。
    lat/lng を 0.1度単位で丸めて "35.6,139.7" みたいな文字列にする。
    位置OFFのときは region_code ベースのダミーIDにする。
    """
    if lat is None or lng is None:
        # 位置情報OFF＋手動エリア選択時は region_code ベースで雑にまとめる
        return f"{region_code}_center"

    # 0.1度単位で丸める（floor でも round でもOK。今回は round）
    lat_round = round(lat * 10) / 10.0
    lng_round = round(lng * 10) / 10.0
    return f"{lat_round:.1f},{lng_round:.1f}"

REGION_CENTER = {
    "hokkaido_tohoku": {"lat": 39.7, "lng": 141.0, "label": "北海道・東北"},
    "kanto":           {"lat": 35.7, "lng": 139.7, "label": "関東"},
    "chubu":           {"lat": 36.2, "lng": 137.9, "label": "中部"},
    "kansai":          {"lat": 34.7, "lng": 135.5, "label": "関西"},
    "chugoku_shikoku": {"lat": 34.3, "lng": 133.0, "label": "中国・四国"},
    "kyushu_okinawa":  {"lat": 32.0, "lng": 130.7, "label": "九州・沖縄"},
    # World はパリ近辺とか、どこか確実に陸の場所にしておく
    "world_other":     {"lat": 48.85, "lng": 2.35, "label": "World"},
}

# --- Ping 登録 API ----------------------------------------------

@app.route("/api/pings", methods=["POST"])
def create_ping():
    data = request.get_json() or {}

    status = data.get("status")
    region_code = data.get("region_code") or "unknown"
    city_name = data.get("city_name")
    lat = data.get("lat")
    lng = data.get("lng")
    message = data.get("message")
    device_id = data.get("device_id") or "unknown-device"

    # ざっくりバリデーション
    if status not in ALLOWED_STATUS:
        return jsonify({"error": "invalid status"}), 400

    # まず float にする
    try:
        raw_lat = float(lat) if lat is not None else None
        raw_lng = float(lng) if lng is not None else None
    except (TypeError, ValueError):
        raw_lat = None
        raw_lng = None

    # ★ 小数第2位で丸める（約 1km グリッド）
    def round_coord(v, digits=2):
        return round(v, digits) if v is not None else None

    lat_val = round_coord(raw_lat, 2)
    lng_val = round_coord(raw_lng, 2)

    # 範囲チェック & (0,0) 無効化
    if lat_val is not None and lng_val is not None:
        if not (-85 <= lat_val <= 85 and -180 <= lng_val <= 180):
            lat_val = None
            lng_val = None
        elif lat_val == 0 and lng_val == 0:
            lat_val = None
            lng_val = None

    # area_code の計算も丸めた値を使う
    area_code = compute_area_code(lat_val, lng_val, region_code)

    now_iso = datetime.utcnow().isoformat()

    conn = get_db()
    cur = conn.cursor()

    # ★ ここがポイント：
    # 同じ device_id の最新レコードがあれば UPDATE、
    # なければ INSERT にする
    cur.execute(
        """
        SELECT id
        FROM pings
        WHERE device_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (device_id,),
    )
    row = cur.fetchone()

    if row is not None:
        # 既存レコードを上書き
        ping_id = row["id"]
        cur.execute(
            """
            UPDATE pings
            SET status = ?,
                region_code = ?,
                city_name = ?,
                area_code = ?,
                lat = ?,
                lng = ?,
                message = ?,
                created_at = ?
            WHERE id = ?
            """,
            (
                status,
                region_code,
                city_name,
                area_code,
                lat_val,
                lng_val,
                message,
                now_iso,
                ping_id,
            ),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "mode": "updated"}), 200
    else:
        # 初めての device_id → 新規作成
        cur.execute(
            """
            INSERT INTO pings (
                device_id, status, region_code, city_name,
                area_code, lat, lng, message, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                status,
                region_code,
                city_name,
                area_code,
                lat_val,
                lng_val,
                message,
                now_iso,
            ),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "mode": "inserted"}), 201

@app.route("/api/admin/ping_stats")
def admin_ping_stats():
    """簡易的な統計: エリア別・市別・グリッド別の人数"""
    cutoff = datetime.utcnow() - timedelta(hours=1)
    cutoff_iso = cutoff.isoformat()

    conn = get_db()
    cur = conn.cursor()

    # エリアごとの人数（直近1時間）
    cur.execute(
        """
        SELECT region_code, COUNT(*)
        FROM pings
        WHERE created_at >= ?
        GROUP BY region_code
        """,
        (cutoff_iso,),
    )
    region_rows = cur.fetchall()

    # 市ごとの人数（全期間）
    cur.execute(
        """
        SELECT city_name, COUNT(*)
        FROM pings
        GROUP BY city_name
        """
    )
    city_rows = cur.fetchall()

    # 直近1時間の「生の lat / lng ごと」に一旦集計（NULL は除外）
    cur.execute(
        """
        SELECT lat, lng, COUNT(*)
        FROM pings
        WHERE created_at >= ?
          AND lat IS NOT NULL
          AND lng IS NOT NULL
        GROUP BY lat, lng
        """,
        (cutoff_iso,),
    )
    raw_grid_rows = cur.fetchall()

    conn.close()

    # ★ 世界共通の「粗いグリッド」（例: 0.2度 ≒ 20〜22km）に丸め直す
    CELL_DEG = 0.2  # ここを 0.25 とかに変えればさらに粗くできる
    grid_map = {}   # {(cell_lat, cell_lng): count}

    for lat, lng, c in raw_grid_rows:
        # 0.2度単位で丸めて代表点を作る
        cell_lat = round(lat / CELL_DEG) * CELL_DEG
        cell_lng = round(lng / CELL_DEG) * CELL_DEG
        key = (cell_lat, cell_lng)
        grid_map[key] = grid_map.get(key, 0) + c

    grid_stats = [
        {"lat": lat, "lng": lng, "count": count}
        for (lat, lng), count in grid_map.items()
    ]

    return jsonify(
        {
            "region_stats": [
                {"region_code": r, "count": c} for (r, c) in region_rows
            ],
            "city_stats": [
                {"city_name": name, "count": c} for (name, c) in city_rows
            ],
            "grid_stats": grid_stats,
        }
    )

@app.route("/api/pings/grid_status")
def pings_grid_status():
    """
    直近30分の「グリッドごとのステータス内訳」を返す。
    フロントのマップ用（ピンをタップしたときに 👀/🌀/🌙/💻 を出す）。
    """
    cutoff = datetime.utcnow() - timedelta(minutes=30)
    cutoff_iso = cutoff.isoformat()

    conn = get_db()
    cur = conn.cursor()

    # lat/lng, status ごとに集計
    cur.execute(
        """
        SELECT lat, lng, status, COUNT(*)
        FROM pings
        WHERE created_at >= ?
          AND lat IS NOT NULL
          AND lng IS NOT NULL
        GROUP BY lat, lng, status
        """,
        (cutoff_iso,),
    )
    rows = cur.fetchall()
    conn.close()

    # {(lat,lng): {"awake": x, "free": y, ...}} にまとめる
    grid_map = {}
    for lat, lng, status, c in rows:
        key = (float(lat), float(lng))
        if key not in grid_map:
            grid_map[key] = {"awake": 0, "free": 0, "cantSleep": 0, "working": 0}
        if status in grid_map[key]:
            grid_map[key][status] += int(c)

    result = []
    for (lat, lng), counts in grid_map.items():
        result.append(
            {
                "lat": lat,
                "lng": lng,
                "counts": counts,
            }
        )

    return jsonify(result)

@app.route("/api/admin/cleanup_old_pings")
def cleanup_old_pings():
    """
    古い Ping をまとめて削除する簡易API。
    デフォルトは「1日より前」を削除。
    /api/admin/cleanup_old_pings?token=...&days=3 みたいに指定も可能。
    """
    # まずは簡単な“鍵”チェック
    token = request.args.get("token")
    if token != ADMIN_SECRET:
        return jsonify({"error": "unauthorized"}), 401

    # 何日前より前を消すか（デフォルト1日）
    days_str = request.args.get("days", "1")
    try:
        days = int(days_str)
        if days < 0:
            days = 1
    except ValueError:
        days = 1

    cutoff = datetime.utcnow() - timedelta(days=days)
    cutoff_iso = cutoff.isoformat()

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM pings
        WHERE created_at < ?
        """,
        (cutoff_iso,),
    )
    deleted_rows = cur.rowcount
    conn.commit()
    conn.close()

    return jsonify(
        {
            "ok": True,
            "deleted": deleted_rows,
            "cutoff_iso": cutoff_iso,
            "days": days,
        }
    )

# --- 直近30分のサマリー API --------------------------------------


@app.route("/api/pings/summary", methods=["GET"])
def ping_summary():
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

@app.route("/api/pings/map")
def pings_map():
    """地図に表示するポイント（エリアごと）"""
    cutoff = datetime.utcnow() - timedelta(minutes=30)
    cutoff_iso = cutoff.isoformat()

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT region_code, COUNT(*)
        FROM pings
        WHERE created_at >= ?
        GROUP BY region_code
        """,
        (cutoff_iso,),
    )
    rows = cur.fetchall()
    conn.close()

    result = []
    for region_code, count in rows:
        meta = REGION_CENTER.get(region_code)
        if not meta:
            continue
        result.append(
            {
                "lat": meta["lat"],
                "lng": meta["lng"],
                "count": int(count),
                "label": meta["label"],
            }
        )

    return jsonify(result)

@app.route("/api/pings/map_total")
def pings_map_total():
    """エリアごとの累計ピコン数（時間条件なし）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT region_code, COUNT(*)
        FROM pings
        GROUP BY region_code
        """
    )
    rows = cur.fetchall()
    conn.close()

    result = []
    for region_code, count in rows:
        meta = REGION_CENTER.get(region_code)
        if not meta:
            continue
        result.append(
            {
                "lat": meta["lat"],
                "lng": meta["lng"],
                "count": int(count),
                "label": meta["label"],
            }
        )

    return jsonify(result)

@app.route("/api/pings/map_points")
def pings_map_points():
    """
    マップ用: 1ピン = 1ユーザーの Ping 一覧を返す。
    直近24時間・lat/lng が入っているものだけ。
    """
    cutoff = datetime.utcnow() - timedelta(hours=24)
    cutoff_iso = cutoff.isoformat()

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, status, lat, lng, message, created_at
        FROM pings
        WHERE created_at >= ?
          AND lat IS NOT NULL
          AND lng IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 500
        """,
        (cutoff_iso,),
    )
    rows = cur.fetchall()
    conn.close()

    result = []
    for row in rows:
        result.append(
            {
                "id": row["id"],
                "status": row["status"],
                "lat": row["lat"],
                "lng": row["lng"],
                "hasMessage": bool(row["message"]),
                "createdAt": row["created_at"],
            }
        )

    return jsonify(result)

@app.route("/api/pings/summary_status")
def ping_summary_status():
    minutes_str = request.args.get("minutes", "30")
    try:
        minutes = int(minutes_str)
    except ValueError:
        minutes = 30

    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    cutoff_iso = cutoff.isoformat()

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT region_code, status, COUNT(*) AS count
        FROM pings
        WHERE created_at >= ?
        GROUP BY region_code, status
        """,
        (cutoff_iso,),
    )
    rows = cur.fetchall()
    conn.close()

    result = [
      {"region_code": r, "status": s, "count": c}
      for (r, s, c) in rows
    ]
    return jsonify(result)

@app.route("/admin/dashboard")
@requires_auth
def admin_dashboard():
    return render_template("admin_dashboard.html")

if __name__ == "__main__":
    # ローカルテスト用
    app.run(debug=True)
