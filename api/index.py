import os
import random
import string
import secrets
from datetime import datetime, timezone, timedelta

import requests
from flask import Flask, jsonify, request


app = Flask(__name__)


# =========================================================
# 환경변수
# =========================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")


# =========================================================
# 기본 설정
# =========================================================

CODE_ALPHABET = "".join(
    c for c in string.ascii_uppercase
    if c not in "OI"
) + "23456789"

VALID_MODES = {"draw", "couple"}


# =========================================================
# 방 자동 삭제 설정
# =========================================================
#
# 기본값: 마지막 활동 후 24시간
#
# 예:
# ROOM_INACTIVE_HOURS=48
# → 48시간 미사용 방 자동 삭제
#
# ROOM_INACTIVE_HOURS=168
# → 7일 미사용 방 자동 삭제
#
# =========================================================

ROOM_INACTIVE_HOURS = int(
    os.environ.get("ROOM_INACTIVE_HOURS", "24")
)


# =========================================================
# Supabase 공통 함수
# =========================================================

def sb_headers():
    return {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def sb_url(path):
    return f"{SUPABASE_URL}/rest/v1/{path}"


# =========================================================
# 시간 관련
# =========================================================

def utc_now_iso():
    """
    현재 UTC 시간을 ISO 8601 문자열로 반환
    """
    return datetime.now(timezone.utc).isoformat()


# =========================================================
# 방 코드 생성
# =========================================================

def gen_code(n=5):
    return "".join(
        secrets.choice(CODE_ALPHABET)
        for _ in range(n)
    )


# =========================================================
# 방 조회
# =========================================================

def get_room(room_id=None, code=None):

    params = {
        "select": (
            "id,"
            "code,"
            "title,"
            "mode,"
            "status,"
            "current_round,"
            "host_token,"
            "gender_split,"
            "number_roles,"
            "created_at,"
            "last_activity_at"
        )
    }

    if room_id:
        params["id"] = f"eq.{room_id}"
    else:
        params["code"] = f"eq.{code}"

    resp = requests.get(
        sb_url("rooms"),
        headers=sb_headers(),
        params=params,
        timeout=10,
    )

    rows = resp.json()

    return rows[0] if rows else None


# =========================================================
# 방 활동시간 갱신
# =========================================================

def touch_room(room_id):
    """
    방의 마지막 활동 시간을 현재 시간으로 갱신
    """

    try:

        requests.patch(
            sb_url("rooms"),
            headers=sb_headers(),
            params={
                "id": f"eq.{room_id}"
            },
            json={
                "last_activity_at": utc_now_iso()
            },
            timeout=10,
        )

    except Exception as e:

        # 활동시간 갱신 실패가 본 요청까지 막지 않도록 함
        print(
            f"👉 [방 활동시간 갱신 실패] "
            f"{room_id} / {e}"
        )


# =========================================================
# 방 및 관련 데이터 삭제
# =========================================================
