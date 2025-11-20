# app.py
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify
import uuid

app = Flask(__name__)

# -----------------------
#  ヘルスチェック用
# -----------------------
@app.get("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat() + "Z"})


# -----------------------
#  仮の in-memory ストア
# -----------------------
# 本番では DB に置き換えるが、まずはメモリで動作確認だけする
pings: list[dict] = []


def now_utc():
    return datetime.now(timezone.utc)


# ★ Ping を新規作成（アプリから呼ぶ想定）
@app.post("/api/pings")
def create_ping():
    data = request.get_json() or {}

    status = data.get("status")
    region_code = data.get("region_code")
    city_name = data.get("city_name")
    message = data.get("message")  # 課金ユーザーのみ有効にする想定
    device_id = data.get("device_id") or str(uuid.uuid4())

    if status not in ["awake", "free", "cantSleep", "working"]:
        return jsonify({"error": "invalid status"}), 400
    if not region_code:
        return jsonify({"error": "region_code is required"}), 400
    if not city_name:
        return jsonify({"error": "city_name is required"}), 400

    ping = {
        "id": str(uuid.uuid4()),
        "device_id": device_id,
        "status": status,
        "region_code": region_code,
        "city_name": city_name,
        "message": message or None,
        "created_at": now_utc().isoformat(),
    }

    pings.append(ping)
    return jsonify(ping), 201


# ★ 直近30分の「エリア別人数」を返す
@app.get("/api/pings/summary")
def pings_summary():
    cutoff = now_utc() - timedelta(minutes=30)

    recent = []
    for p in pings:
        try:
            created = datetime.fromisoformat(p["created_at"])
        except Exception:
            continue
        if created >= cutoff:
            recent.append(p)

    summary: dict[str, int] = {}
    for p in recent:
        region = p["region_code"]
        summary[region] = summary.get(region, 0) + 1

    result = [
        {"region_code": region, "count": count}
        for region, count in summary.items()
    ]

    return jsonify(result), 200


# ★ 特定エリアの一覧（市区町村＋ステータス・メッセージ）
@app.get("/api/pings")
def list_pings():
    region_code = request.args.get("region_code")
    city_name = request.args.get("city_name")  # 任意

    cutoff = now_utc() - timedelta(minutes=30)

    result = []
    for p in pings:
        try:
            created = datetime.fromisoformat(p["created_at"])
        except Exception:
            continue

        if created < cutoff:
            continue

        if region_code and p["region_code"] != region_code:
            continue
        if city_name and p["city_name"] != city_name:
            continue

        result.append(p)

    return jsonify(result), 200


# ローカル実行用エントリポイント
if __name__ == "__main__":
    app.run(debug=True)