import os
import random
import string
import secrets
from datetime import datetime, timezone, timedelta

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")

# 마지막 활동 후 이 시간(시간 단위)이 지난 방은 /api/cleanup 호출 시 자동 삭제됩니다.
# 예) ROOM_INACTIVE_HOURS=48 → 48시간 미사용 방 삭제, 168 → 7일
ROOM_INACTIVE_HOURS = int(os.environ.get("ROOM_INACTIVE_HOURS", "24"))

CODE_ALPHABET = "".join(c for c in string.ascii_uppercase if c not in "OI") + "23456789"
VALID_MODES = {"draw", "couple"}


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def sb_headers():
    return {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def sb_url(path):
    return f"{SUPABASE_URL}/rest/v1/{path}"


def gen_code(n=5):
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(n))


def get_room(room_id=None, code=None):
    params = {
        "select": "id,code,title,mode,status,current_round,host_token,"
                  "gender_split,number_roles,created_at,last_activity_at"
    }
    if room_id:
        params["id"] = f"eq.{room_id}"
    else:
        params["code"] = f"eq.{code}"
    resp = requests.get(sb_url("rooms"), headers=sb_headers(), params=params, timeout=10)
    rows = resp.json()
    return rows[0] if rows else None


def touch_room(room_id):
    """방에 실제 활동(참가/뽑기/역할저장)이 있을 때마다 마지막 활동시간을 갱신.
    실패해도 원래 요청은 막지 않음."""
    try:
        requests.patch(
            sb_url("rooms"),
            headers=sb_headers(),
            params={"id": f"eq.{room_id}"},
            json={"last_activity_at": utc_now_iso()},
            timeout=10,
        )
    except Exception as e:
        print(f"[방 활동시간 갱신 실패] {room_id} / {e}")


@app.route("/api/room_info", methods=["GET"])
def room_info():
    code = (request.args.get("code") or "").strip().upper()
    if not code:
        return jsonify({"error": "코드를 입력해주세요."}), 400
    room = get_room(code=code)
    if not room:
        return jsonify({"error": "존재하지 않는 방 코드예요."}), 404
    return jsonify({
        "title": room["title"], "mode": room["mode"], "status": room["status"],
        "gender_split": room["gender_split"],
    })


@app.route("/api/create_room", methods=["POST"])
def create_room():
    data = request.get_json(force=True, silent=True) or {}
    title = (data.get("title") or "").strip()[:60]
    mode = data.get("mode") if data.get("mode") in VALID_MODES else "draw"
    gender_split = bool(data.get("gender_split")) and mode == "draw"
    if not title:
        title = "커플매칭" if mode == "couple" else "제비뽑기"
    host_token = secrets.token_urlsafe(16)

    for _ in range(5):
        code = gen_code()
        resp = requests.post(
            sb_url("rooms"),
            headers=sb_headers(),
            json={
                "code": code, "title": title, "mode": mode,
                "gender_split": gender_split, "host_token": host_token,
            },
            timeout=10,
        )
        if resp.status_code == 201:
            room = resp.json()[0]
            return jsonify({
                "room_id": room["id"], "code": code, "host_token": host_token,
                "mode": mode, "gender_split": gender_split,
            })
        if resp.status_code != 409:
            return jsonify({"error": resp.text}), 500
    return jsonify({"error": "방 코드를 생성하지 못했어요. 다시 시도해주세요."}), 500


@app.route("/api/join", methods=["POST"])
def join_room():
    data = request.get_json(force=True, silent=True) or {}
    code = (data.get("code") or "").strip().upper()
    name = (data.get("name") or "").strip()[:30]
    gender = data.get("gender")
    if not code or not name:
        return jsonify({"error": "코드와 이름을 입력해주세요."}), 400

    room = get_room(code=code)
    if not room:
        return jsonify({"error": "존재하지 않는 방 코드예요."}), 404
    if room["status"] != "waiting":
        return jsonify({"error": "이미 시작된 방이라 더 이상 참가할 수 없어요."}), 400

    payload = {"room_id": room["id"], "name": name}
    needs_gender = room["mode"] == "couple" or (room["mode"] == "draw" and room["gender_split"])
    if needs_gender:
        if gender not in ("M", "F"):
            return jsonify({"error": "성별을 선택해주세요."}), 400
        payload["gender"] = gender

    join_secret = secrets.token_urlsafe(24)
    payload["join_secret"] = join_secret

    resp = requests.post(sb_url("participants"), headers=sb_headers(), json=payload, timeout=10)
    if resp.status_code != 201:
        return jsonify({"error": resp.text}), 500
    participant = resp.json()[0]
    touch_room(room["id"])
    return jsonify({
        "participant_id": participant["id"], "room_id": room["id"],
        "mode": room["mode"], "join_secret": join_secret,
    })


@app.route("/api/my_result", methods=["GET"])
def my_result():
    participant_id = request.args.get("participant_id")
    join_secret = request.args.get("join_secret")
    if not participant_id or not join_secret:
        return jsonify({"error": "잘못된 요청이에요."}), 400

    resp = requests.get(
        sb_url("participants"),
        headers=sb_headers(),
        params={"id": f"eq.{participant_id}", "select": "id,room_id,name,join_secret"},
        timeout=10,
    )
    rows = resp.json()
    if not rows or rows[0]["join_secret"] != join_secret:
        return jsonify({"error": "권한이 없어요."}), 403
    participant = rows[0]

    room = get_room(room_id=participant["room_id"])
    if not room:
        return jsonify({"error": "존재하지 않는 방이에요."}), 404

    if room["current_round"] == 0:
        return jsonify({"waiting": True, "mode": room["mode"]})

    resp = requests.get(
        sb_url("draw_results"),
        headers=sb_headers(),
        params={
            "participant_id": f"eq.{participant_id}",
            "round_no": f"eq.{room['current_round']}",
            "select": "round_no,assigned_number,role_label,partner_id",
        },
        timeout=10,
    )
    rows = resp.json()
    if not rows:
        return jsonify({"waiting": True, "mode": room["mode"]})
    result = rows[0]

    partner_name = None
    if result.get("partner_id"):
        resp = requests.get(
            sb_url("participants"),
            headers=sb_headers(),
            params={"id": f"eq.{result['partner_id']}", "select": "name"},
            timeout=10,
        )
        prows = resp.json()
        partner_name = prows[0]["name"] if prows else None

    return jsonify({
        "waiting": False, "mode": room["mode"],
        "round_no": result["round_no"], "assigned_number": result["assigned_number"],
        "role_label": result.get("role_label"), "partner_name": partner_name,
    })


@app.route("/api/set_roles", methods=["POST"])
def set_roles():
    data = request.get_json(force=True, silent=True) or {}
    room_id = data.get("room_id")
    host_token = data.get("host_token")
    roles = data.get("roles")
    if not room_id or not host_token or not isinstance(roles, dict):
        return jsonify({"error": "잘못된 요청이에요."}), 400

    room = get_room(room_id=room_id)
    if not room or room["host_token"] != host_token:
        return jsonify({"error": "권한이 없어요."}), 403

    cleaned = {}
    for key, value in roles.items():
        try:
            num = int(key)
        except (TypeError, ValueError):
            continue
        text = (value or "").strip()[:40]
        if num > 0 and text:
            cleaned[str(num)] = text
        if len(cleaned) >= 300:
            break

    requests.patch(
        sb_url("rooms"),
        headers=sb_headers(),
        params={"id": f"eq.{room_id}"},
        json={"number_roles": cleaned},
        timeout=10,
    )
    touch_room(room_id)
    return jsonify({"ok": True, "roles": cleaned})


def fetch_participants(room_id):
    resp = requests.get(
        sb_url("participants"),
        headers=sb_headers(),
        params={"room_id": f"eq.{room_id}", "select": "id,name,gender", "order": "joined_at.asc"},
        timeout=10,
    )
    return resp.json()


def fetch_past_pairs(room_id):
    """이 방에서 지금까지 커플로 묶인 적 있는 (참가자, 참가자) 쌍의 집합을 반환."""
    resp = requests.get(
        sb_url("draw_results"),
        headers=sb_headers(),
        params={
            "room_id": f"eq.{room_id}",
            "select": "participant_id,partner_id",
            "partner_id": "not.is.null",
        },
        timeout=10,
    )
    pairs = set()
    for row in resp.json():
        a, b = row["participant_id"], row["partner_id"]
        pairs.add(frozenset((a, b)))
    return pairs


def match_couples(males, females, past_pairs, attempts=300):
    """남녀를 짝지으면서 과거 라운드에 이미 묶였던 쌍을 최대한 피한다.
    무작위 셔플을 여러 번 시도해 과거 이력과 충돌이 가장 적은 조합을 고른다."""
    best = None
    best_conflicts = None
    k = min(len(males), len(females))
    for _ in range(attempts):
        m = males[:]
        f = females[:]
        random.shuffle(m)
        random.shuffle(f)
        pairs = list(zip(m[:k], f[:k]))
        conflicts = sum(1 for a, b in pairs if frozenset((a, b)) in past_pairs)
        if conflicts == 0:
            return pairs, 0
        if best_conflicts is None or conflicts < best_conflicts:
            best, best_conflicts = pairs, conflicts
    return best or [], best_conflicts or 0


@app.route("/api/room_state", methods=["GET"])
def room_state():
    room_id = request.args.get("room_id")
    host_token = request.args.get("host_token")
    if not room_id or not host_token:
        return jsonify({"error": "잘못된 요청이에요."}), 400

    room = get_room(room_id=room_id)
    if not room or room["host_token"] != host_token:
        return jsonify({"error": "권한이 없어요."}), 403

    resp = requests.get(
        sb_url("participants"),
        headers=sb_headers(),
        params={"room_id": f"eq.{room_id}", "select": "id,name,gender,joined_at", "order": "joined_at.asc"},
        timeout=10,
    )
    participants = resp.json()

    results = []
    if room["current_round"] > 0:
        resp = requests.get(
            sb_url("draw_results"),
            headers=sb_headers(),
            params={
                "room_id": f"eq.{room_id}", "round_no": f"eq.{room['current_round']}",
                "select": "participant_id,assigned_number,role_label,partner_id",
            },
            timeout=10,
        )
        results = resp.json()

    return jsonify({
        "mode": room["mode"], "status": room["status"], "current_round": room["current_round"],
        "gender_split": room["gender_split"],
        "participants": participants,
        "results": results,
    })


@app.route("/api/start_round", methods=["POST"])
def start_round():
    data = request.get_json(force=True, silent=True) or {}
    room_id = data.get("room_id")
    host_token = data.get("host_token")
    if not room_id or not host_token:
        return jsonify({"error": "잘못된 요청이에요."}), 400

    room = get_room(room_id=room_id)
    if not room or room["host_token"] != host_token:
        return jsonify({"error": "권한이 없어요."}), 403

    participants = fetch_participants(room_id)
    if not participants:
        return jsonify({"error": "참가자가 없어요."}), 400

    next_round = room["current_round"] + 1
    rows = []

    if room["mode"] == "draw":
        roles = room.get("number_roles") or {}
        unmatched_names = []

        if room["gender_split"]:
            males = [p["id"] for p in participants if p["gender"] == "M"]
            females = [p["id"] for p in participants if p["gender"] == "F"]
            others = [p["id"] for p in participants if p["gender"] not in ("M", "F")]
            random.shuffle(males)
            random.shuffle(females)
            # 남: 홀수(1,3,5,...) / 여: 짝수(2,4,6,...)
            assignments = [(pid, 2 * i + 1) for i, pid in enumerate(males)]
            assignments += [(pid, 2 * i + 2) for i, pid in enumerate(females)]
            for pid in others:
                rows.append({
                    "room_id": room_id, "round_no": next_round,
                    "participant_id": pid, "assigned_number": None,
                })
            by_id = {p["id"]: p["name"] for p in participants}
            unmatched_names = [by_id[pid] for pid in others]
        else:
            ids = [p["id"] for p in participants]
            numbers = list(range(1, len(ids) + 1))
            random.shuffle(numbers)
            assignments = list(zip(ids, numbers))

        for pid, num in assignments:
            rows.append({
                "room_id": room_id, "round_no": next_round,
                "participant_id": pid, "assigned_number": num,
                "role_label": roles.get(str(num)),
            })
    else:
        males = [p["id"] for p in participants if p["gender"] == "M"]
        females = [p["id"] for p in participants if p["gender"] == "F"]
        if not males or not females:
            return jsonify({"error": "남녀 참가자가 모두 있어야 매칭할 수 있어요."}), 400

        past_pairs = fetch_past_pairs(room_id)
        pairs, conflicts = match_couples(males, females, past_pairs)

        paired_ids = set()
        shuffled_pairs = pairs[:]
        random.shuffle(shuffled_pairs)
        for i, (a, b) in enumerate(shuffled_pairs, start=1):
            rows.append({
                "room_id": room_id, "round_no": next_round,
                "participant_id": a, "assigned_number": i, "partner_id": b,
            })
            rows.append({
                "room_id": room_id, "round_no": next_round,
                "participant_id": b, "assigned_number": i, "partner_id": a,
            })
            paired_ids.add(a)
            paired_ids.add(b)

        by_id = {p["id"]: p["name"] for p in participants}
        unmatched_ids = [p["id"] for p in participants if p["id"] not in paired_ids]
        unmatched_names = [by_id[pid] for pid in unmatched_ids]
        for pid in unmatched_ids:
            rows.append({
                "room_id": room_id, "round_no": next_round,
                "participant_id": pid, "assigned_number": None, "partner_id": None,
            })

    resp = requests.post(sb_url("draw_results"), headers=sb_headers(), json=rows, timeout=15)
    if resp.status_code != 201:
        return jsonify({"error": resp.text}), 500

    requests.patch(
        sb_url("rooms"),
        headers=sb_headers(),
        params={"id": f"eq.{room_id}"},
        json={"status": "active", "current_round": next_round},
        timeout=10,
    )
    touch_room(room_id)

    result = {"ok": True, "round": next_round, "count": len(participants)}
    if room["mode"] == "couple":
        result["couples"] = (len(rows) - len(unmatched_names)) // 2
        result["unmatched"] = unmatched_names
    elif room["gender_split"] and unmatched_names:
        result["unmatched"] = unmatched_names
    return jsonify(result)


@app.route("/api/cleanup", methods=["GET", "POST"])
def cleanup():
    """마지막 활동 후 ROOM_INACTIVE_HOURS 시간이 지난 방을 삭제.
    participants/draw_results는 rooms에 대한 ON DELETE CASCADE로 함께 삭제됨.
    Vercel Cron 같은 예약 작업이 주기적으로 호출하도록 설계되어 있고,
    아무나 실행할 수 없도록 CRON_SECRET 헤더 검증이 필요합니다."""
    if not CRON_SECRET:
        return jsonify({"error": "CRON_SECRET 환경변수가 설정되어 있지 않아요."}), 500
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {CRON_SECRET}":
        return jsonify({"error": "권한이 없어요."}), 403

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ROOM_INACTIVE_HOURS)).isoformat()
    resp = requests.delete(
        sb_url("rooms"),
        headers=sb_headers(),
        params={"last_activity_at": f"lt.{cutoff}"},
        timeout=20,
    )
    if resp.status_code not in (200, 204):
        return jsonify({"error": resp.text}), 500
    deleted = resp.json() if resp.text else []
    return jsonify({"ok": True, "deleted_count": len(deleted), "cutoff": cutoff})
