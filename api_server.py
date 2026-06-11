from fastapi import FastAPI, File, UploadFile, Form, Header, HTTPException, Body, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn, mysql.connector, os, time, json, logging, secrets, base64, re
from collections import defaultdict
from datetime import datetime
import cv2
import numpy as np
from runtime_env import configure_windows_dll_paths
from face_verifier import DEFAULT_FACE_THRESHOLD, MAX_FACE_THRESHOLD, MIN_FACE_THRESHOLD, build_reference_signature, compare_with_signature
from auth_security import hash_password, verify_and_upgrade_password

configure_windows_dll_paths()
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)
DB_HOST = os.getenv("S_MONITOR_DB_HOST", "127.0.0.1")
DB_USER = os.getenv("S_MONITOR_DB_USER", "root")
DB_PASSWORD = os.getenv("S_MONITOR_DB_PASSWORD", "12345")
DB_NAME = os.getenv("S_MONITOR_DB_NAME", "exam_monitor_db")

app = FastAPI(title="S-MONITOR Central Server")

# --- KHỞI TẠO ---
for d in ["server_evidence", "server_evidence/clips", "server_database", "server_submissions"]: 
    os.makedirs(d, exist_ok=True)
app.mount("/evidence_images", StaticFiles(directory="server_evidence"), name="images")
EVIDENCE_CLIPS_DIR = os.path.join("server_evidence", "clips")

# Fix #10, #15: Rate-limiting & validation state
_password_recovery_attempts = defaultdict(lambda: [])  # List of timestamps per account
PASSWORD_RECOVERY_MAX_ATTEMPTS = 3  # Max 3 requests per hour
PASSWORD_RECOVERY_WINDOW_SECONDS = 3600  # 1 hour window

def validate_msv(msv: str) -> bool:
    """Fix #15: Validate MSV format to prevent injection attacks"""
    if not msv:
        return False
    return bool(re.match(r'^[A-Z0-9]{1,20}$', str(msv)))

def _check_password_recovery_rate_limit(account_key: str) -> bool:
    """Fix #10: Check if password recovery request is rate-limited"""
    now = time.time()
    attempts = _password_recovery_attempts.get(account_key, [])
    # Clean old attempts outside the window
    attempts = [t for t in attempts if (now - t) < PASSWORD_RECOVERY_WINDOW_SECONDS]
    _password_recovery_attempts[account_key] = attempts
    
    if len(attempts) >= PASSWORD_RECOVERY_MAX_ATTEMPTS:
        return False  # Rate-limited
    # Record new attempt
    attempts.append(now)
    return True

def get_db_connection():
    return mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME)


def _safe_json_loads(raw_value):
    if raw_value is None:
        return {}
    if isinstance(raw_value, dict):
        return raw_value
    try:
        text = str(raw_value).strip()
        if not text:
            return {}
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _persist_preview_review_to_violation(violation_id: Optional[int], action: str, note: str, proctor_id: str):
    if violation_id is None:
        return {"updated": False, "violation_id": None, "reason": "missing_violation_id"}
    try:
        violation_id_int = int(violation_id)
    except (TypeError, ValueError):
        return {"updated": False, "violation_id": None, "reason": "invalid_violation_id"}

    db = get_db_connection()
    try:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE violations
            SET review_status = %s,
                review_note = %s,
                reviewed_by = %s,
                reviewed_at = %s
            WHERE violation_id = %s
            """,
            (
                str(action or "").strip().lower(),
                str(note or "").strip(),
                str(proctor_id or ""),
                datetime.now(),
                violation_id_int,
            ),
        )
        db.commit()
        return {
            "updated": bool(cur.rowcount > 0),
            "violation_id": violation_id_int,
            "reason": "updated" if cur.rowcount > 0 else "violation_not_found",
        }
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to persist preview review for violation_id=%s", violation_id)
        return {
            "updated": False,
            "violation_id": violation_id_int,
            "reason": "db_error",
            "error": str(exc),
        }
    finally:
        db.close()


def get_face_threshold(cur, default=DEFAULT_FACE_THRESHOLD):
    try:
        cur.execute("SELECT setting_value FROM configs WHERE setting_key = 'ai_face_threshold' LIMIT 1")
        row = cur.fetchone()
        if not row:
            return default
        value = row["setting_value"] if isinstance(row, dict) else row[0]
        threshold = float(value)
        if threshold < MIN_FACE_THRESHOLD or threshold > MAX_FACE_THRESHOLD:
            return default
        return threshold
    except Exception:
        logger.exception("Failed to load ai_face_threshold; using default %.2f", default)
        return default


def get_max_warnings(cur, default=5):
    try:
        cur.execute("SELECT setting_value FROM configs WHERE setting_key = 'max_warnings' LIMIT 1")
        row = cur.fetchone()
        if not row:
            return default
        value = row["setting_value"] if isinstance(row, dict) else row[0]
        return max(1, min(int(float(value)), 20))
    except Exception:
        logger.exception("Failed to load max_warnings; using default %s", default)
        return default


def get_student_reference_face_paths(cur, msv: str) -> List[str]:
    paths: List[str] = []
    try:
        cur.execute(
            """
            SELECT face_image
            FROM student_face_images
            WHERE msv = %s AND face_image IS NOT NULL AND TRIM(face_image) <> ''
            ORDER BY is_primary DESC, ref_id ASC
            """,
            (msv,),
        )
        rows = cur.fetchall() or []
        for row in rows:
            face_image = row["face_image"] if isinstance(row, dict) else row[0]
            if face_image:
                paths.append(str(face_image))
    except mysql.connector.Error:
        logger.debug("student_face_images not available yet; falling back to students.face_image", exc_info=True)

    try:
        cur.execute("SELECT face_image FROM students WHERE msv = %s", (msv,))
        row = cur.fetchone()
    except Exception:
        logger.exception("Failed to load legacy face image for %s", msv)
        row = None

    legacy_path = None
    if row:
        legacy_path = row["face_image"] if isinstance(row, dict) else row[0]
    if legacy_path and str(legacy_path) not in paths:
        paths.append(str(legacy_path))
    return paths


def get_student_reference_face_rows(cur, msv: str) -> List[Dict[str, Any]]:
    rows_out: List[Dict[str, Any]] = []
    seen = set()
    try:
        cur.execute(
            """
            SELECT ref_id, face_image, is_primary
            FROM student_face_images
            WHERE msv = %s AND face_image IS NOT NULL AND TRIM(face_image) <> ''
            ORDER BY is_primary DESC, ref_id ASC
            """,
            (msv,),
        )
        rows = cur.fetchall() or []
        for row in rows:
            face_image = str(row["face_image"] if isinstance(row, dict) else row[1])
            if not face_image or face_image in seen:
                continue
            seen.add(face_image)
            rows_out.append(
                {
                    "ref_id": int(row["ref_id"] if isinstance(row, dict) else row[0]),
                    "face_image": face_image,
                    "is_primary": bool(row["is_primary"] if isinstance(row, dict) else row[2]),
                }
            )
    except mysql.connector.Error:
        logger.debug("student_face_images metadata unavailable; falling back to students.face_image", exc_info=True)

    try:
        cur.execute("SELECT face_image FROM students WHERE msv = %s", (msv,))
        row = cur.fetchone()
    except Exception:
        logger.exception("Failed to load legacy face row for %s", msv)
        row = None

    legacy_path = None
    if row:
        legacy_path = row["face_image"] if isinstance(row, dict) else row[0]
    if legacy_path and str(legacy_path) not in seen:
        rows_out.append({"ref_id": 0, "face_image": str(legacy_path), "is_primary": not rows_out})
    return rows_out


def merge_session_lock_state(session, classroom_state):
    state = dict(classroom_state)
    # Hybrid policy: vượt ngưỡng cảnh báo chỉ yêu cầu giám thị duyệt, không tự khóa phiên thi.
    if session.get("warning_locked"):
        state["broadcast"] = session.get("warning_message", "Đã vượt ngưỡng cảnh báo; cần giám thị xem xét thủ công.")
    if session.get("warning_review_required"):
        state["review_required"] = True
    if session.get("require_snapshot"):
        state["require_snapshot"] = True
        state["snapshot_reason"] = session.get("snapshot_reason") or "Giám thị yêu cầu ảnh snapshot mới nhất."
    return state

active_classrooms = {}
exam_sessions: Dict[str, Dict[str, Any]] = {}
admin_sessions: Dict[str, Dict[str, Any]] = {}
proctor_sessions: Dict[str, Dict[str, Any]] = {}
live_monitor_snapshots: Dict[str, Dict[str, Any]] = {}
question_key_cache: Dict[int, Dict[str, Any]] = {}
_yolo_result_cache: Dict[str, Dict[str, Any]] = {}
SESSION_TTL_SECONDS = 8 * 60 * 60
STAFF_SESSION_TTL_SECONDS = 8 * 60 * 60
QUESTION_CACHE_TTL_SECONDS = 5 * 60
MONITOR_STALE_SECONDS = 20
YOLO_RESULT_CACHE_TTL_SECONDS = 5


def cleanup_expired_sessions():
    now = time.time()
    expired_tokens = [token for token, session in exam_sessions.items() if session.get("expires_at", 0) <= now]
    for token in expired_tokens:
        exam_sessions.pop(token, None)
        live_monitor_snapshots.pop(token, None)

    expired_admin_tokens = [token for token, session in admin_sessions.items() if session.get("expires_at", 0) <= now]
    for token in expired_admin_tokens:
        admin_sessions.pop(token, None)

    expired_proctor_tokens = [token for token, session in proctor_sessions.items() if session.get("expires_at", 0) <= now]
    for token in expired_proctor_tokens:
        proctor_sessions.pop(token, None)


def require_admin_session(x_admin_token: Optional[str]):
    cleanup_expired_sessions()
    if not x_admin_token:
        raise HTTPException(status_code=401, detail="Thiếu phiên quản trị hợp lệ")
    session = admin_sessions.get(x_admin_token)
    if not session:
        raise HTTPException(status_code=401, detail="Phiên quản trị không hợp lệ hoặc đã hết hạn")
    return session


def require_proctor_session(x_proctor_token: Optional[str], proctor_id: Optional[str] = None):
    cleanup_expired_sessions()
    if not x_proctor_token:
        raise HTTPException(status_code=401, detail="Thiếu phiên giám thị hợp lệ")
    session = proctor_sessions.get(x_proctor_token)
    if not session:
        raise HTTPException(status_code=401, detail="Phiên giám thị không hợp lệ hoặc đã hết hạn")
    if proctor_id is not None and str(session.get("proctor_id") or "") != str(proctor_id):
        raise HTTPException(status_code=403, detail="Phiên giám thị không khớp với tài nguyên yêu cầu")
    return session


def _get_role_mapping(role: str):
    normalized = str(role or "").strip().lower()
    mapping = {
        "student": ("students", "msv"),
        "proctor": ("proctors", "proctor_id"),
        "admin": ("admins", "admin_id"),
    }
    if normalized not in mapping:
        raise HTTPException(status_code=400, detail="Vai trò khôi phục mật khẩu không hợp lệ")
    return normalized, mapping[normalized][0], mapping[normalized][1]


def _write_audit_log(actor: str, action: str):
    db = get_db_connection()
    try:
        cur = db.cursor()
        cur.execute("INSERT INTO audit_logs (actor, action) VALUES (%s, %s)", (str(actor or "system"), str(action or "")))
        db.commit()
    except Exception:
        logger.exception("Failed to write audit log: actor=%s action=%s", actor, action)
    finally:
        db.close()


def _set_classroom_status(class_id: int, target_status: str, actor_role: str, actor_id: str, actor_name: str, allowed_proctor_id: Optional[str] = None):
    normalized_status = str(target_status or "").strip().lower()
    if normalized_status not in {"active", "closed"}:
        raise HTTPException(status_code=400, detail="Trạng thái lớp không hợp lệ")

    db = get_db_connection()
    try:
        cur = db.cursor(dictionary=True)
        cur.execute(
            """
            SELECT c.class_id, c.class_name, c.status, et.proctor_id
            FROM classes c
            LEFT JOIN exam_templates et ON c.template_id = et.template_id
            WHERE c.class_id = %s
            LIMIT 1
            """,
            (class_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Không tìm thấy lớp thi")

        owner_proctor_id = str(row.get("proctor_id") or "")
        if allowed_proctor_id is not None and owner_proctor_id != str(allowed_proctor_id):
            raise HTTPException(status_code=403, detail="Bạn không có quyền thay đổi trạng thái lớp thi này")

        current_status = str(row.get("status") or "active").strip().lower()
        if current_status == normalized_status:
            action_text = "mở lại" if normalized_status == "active" else "khóa"
            return {
                "status": "success",
                "message": f"Lớp {class_id} đã ở trạng thái {action_text} tương ứng.",
                "class_id": int(row["class_id"]),
                "class_name": row.get("class_name") or "",
                "class_status": current_status,
            }

        cur.execute("UPDATE classes SET status = %s WHERE class_id = %s", (normalized_status, class_id))
        db.commit()
    finally:
        db.close()

    broadcast_text = "Hệ thống đang giám sát..."
    if normalized_status == "closed":
        broadcast_text = f"Lớp thi đã bị khóa thủ công bởi {actor_role} {actor_id}."
    active_classrooms[class_id] = {"broadcast": broadcast_text, "locked": normalized_status != "active"}

    actor_label = f"{actor_role}:{actor_id}"
    if actor_name:
        actor_label = f"{actor_label} ({actor_name})"
    action_label = "mở lại phòng" if normalized_status == "active" else "khóa phòng"
    _write_audit_log(actor_label, f"{action_label} lớp thi ID={class_id} lúc {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    return {
        "status": "success",
        "message": f"Đã {action_label} lớp {class_id}.",
        "class_id": int(class_id),
        "class_name": row.get("class_name") or "",
        "class_status": normalized_status,
    }


def get_class_question_key(class_id: int):
    cached = question_key_cache.get(class_id)
    now = time.time()
    if cached and (now - cached.get("loaded_at", 0)) <= QUESTION_CACHE_TTL_SECONDS:
        return cached["items"]

    db = get_db_connection()
    try:
        cur = db.cursor(dictionary=True)
        cur.execute(
            "SELECT q.q_id AS question_id, q.correct_option, q.points FROM question_bank q JOIN classes c ON q.template_id = c.template_id WHERE c.class_id = %s",
            (class_id,),
        )
        rows = cur.fetchall()
    finally:
        db.close()

    items = {
        str(row["question_id"]): {
            "correct_option": str(row["correct_option"] or "").upper(),
            "points": float(row.get("points") or 0.0),
        }
        for row in rows
    }
    question_key_cache[class_id] = {"loaded_at": now, "items": items}
    return items


def score_live_answers(class_id: int, answers: Any):
    answer_key = get_class_question_key(class_id)
    if not isinstance(answers, dict):
        answers = {}

    normalized_answers = {
        str(question_id): str(choice or "").strip().upper()
        for question_id, choice in answers.items()
        if str(choice or "").strip()
    }
    answered_count = 0
    correct_count = 0
    wrong_count = 0
    current_score = 0.0

    for question_id, choice in normalized_answers.items():
        question = answer_key.get(question_id)
        if not question:
            continue
        answered_count += 1
        if choice == question["correct_option"]:
            correct_count += 1
            current_score += float(question["points"])
        else:
            wrong_count += 1

    question_total = len(answer_key)
    return {
        "question_total": question_total,
        "answered_count": answered_count,
        "correct_count": correct_count,
        "wrong_count": wrong_count,
        "unanswered_count": max(0, question_total - answered_count),
        "current_score": round(current_score, 2),
    }


def _normalize_monitor_scores(payload: Dict[str, Any], key: str):
    raw = payload.get(key, {})
    if not isinstance(raw, dict):
        return {}
    result = {}
    for name, value in raw.items():
        try:
            result[str(name)] = float(value)
        except (TypeError, ValueError):
            continue
    return result


def _json_dumps_safe(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        logger.exception("Could not serialize JSON payload for YOLO-World task")
        return "{}"


def enqueue_yolo_world_task(
    session: Dict[str, Any],
    violation_id: int,
    evidence_path: str,
    trigger_type: str,
    extra_meta: Optional[Dict[str, Any]] = None,
):
    db = get_db_connection()
    try:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO yolo_world_tasks (
                session_token, msv, exam_id, violation_id, evidence_path,
                trigger_type, source, prompt_profile, input_meta_json,
                status, priority, attempt_count, max_attempts
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'violation_upload', 'default_exam', %s, 'pending', %s, 0, 3)
            """,
            (
                session.get("session_token"),
                session.get("msv"),
                session.get("class_id"),
                violation_id,
                evidence_path,
                trigger_type,
                _json_dumps_safe(extra_meta or {}),
                1,
            ),
        )
        db.commit()
        return int(cur.lastrowid)
    except Exception:
        logger.exception("Failed to enqueue YOLO-World task for violation_id=%s", violation_id)
        try:
            db.rollback()
        except Exception:
            pass
        return None
    finally:
        db.close()


def get_latest_yolo_world_result(msv: str, exam_id: int):
    cache_key = f"{msv}:{exam_id}"
    now = time.time()
    cached = _yolo_result_cache.get(cache_key)
    if cached and (now - float(cached.get("loaded_at", 0.0))) <= YOLO_RESULT_CACHE_TTL_SECONDS:
        return cached.get("data")

    db = get_db_connection()
    try:
        cur = db.cursor(dictionary=True)
        cur.execute(
            """
            SELECT
                t.task_id,
                t.status AS task_status,
                t.trigger_type,
                t.violation_id,
                t.created_at AS task_created_at,
                t.updated_at AS task_updated_at,
                r.result_id,
                r.top_label,
                r.top_confidence,
                r.verdict,
                r.risk_delta,
                r.labels_json,
                r.boxes_json,
                r.output_meta_json,
                r.model_name,
                r.model_version,
                r.inference_ms,
                r.created_at AS result_created_at
            FROM yolo_world_tasks t
            LEFT JOIN yolo_world_results r ON r.task_id = t.task_id
            WHERE t.msv = %s AND t.exam_id = %s
            ORDER BY t.task_id DESC
            LIMIT 1
            """,
            (msv, exam_id),
        )
        row = cur.fetchone()
    except Exception:
        logger.exception("Failed to fetch YOLO-World result for %s/%s", msv, exam_id)
        row = None
    finally:
        db.close()

    _yolo_result_cache[cache_key] = {"loaded_at": now, "data": row}
    return row


def get_latest_earpiece_yolo_world_result(msv: str, exam_id: int):
    """Lấy kết quả hậu kiểm gần nhất có trigger liên quan tai nghe."""
    db = get_db_connection()
    try:
        cur = db.cursor(dictionary=True)
        cur.execute(
            """
            SELECT
                t.task_id,
                t.status AS task_status,
                t.trigger_type,
                t.violation_id,
                t.created_at AS task_created_at,
                t.updated_at AS task_updated_at,
                r.result_id,
                r.top_label,
                r.top_confidence,
                r.verdict,
                r.risk_delta,
                r.labels_json,
                r.boxes_json,
                r.output_meta_json,
                r.model_name,
                r.model_version,
                r.inference_ms,
                r.created_at AS result_created_at
            FROM yolo_world_tasks t
            LEFT JOIN yolo_world_results r ON r.task_id = t.task_id
            WHERE t.msv = %s
              AND t.exam_id = %s
              AND (
                LOWER(COALESCE(t.trigger_type, '')) LIKE '%%earpiece%%'
                OR LOWER(COALESCE(t.trigger_type, '')) LIKE '%%tai nghe%%'
                OR LOWER(COALESCE(t.trigger_type, '')) LIKE '%%headset%%'
              )
            ORDER BY t.task_id DESC
            LIMIT 1
            """,
            (msv, exam_id),
        )
        return cur.fetchone()
    except Exception:
        logger.exception("Failed to fetch latest earpiece YOLO-World result for %s/%s", msv, exam_id)
        return None
    finally:
        db.close()


def get_confirmed_warning_count(cur, msv: str, exam_id: int) -> int:
    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM yolo_world_results r
        JOIN yolo_world_tasks t ON t.task_id = r.task_id
        WHERE t.msv = %s AND t.exam_id = %s AND r.verdict = 'confirm'
        """,
        (msv, exam_id),
    )
    row = cur.fetchone()
    if isinstance(row, dict):
        return int(row.get("count") or 0)
    if isinstance(row, (list, tuple)) and row:
        return int(row[0] or 0)
    return 0


def refresh_session_warning_state(session: Dict[str, Any]):
    msv = str(session.get("msv") or "")
    exam_id = int(session.get("class_id") or 0)
    if not msv or not exam_id:
        return

    db = get_db_connection()
    try:
        cur = db.cursor(dictionary=True)
        max_warnings = get_max_warnings(cur)
        confirmed_count = get_confirmed_warning_count(cur, msv, exam_id)
    except Exception:
        logger.exception("Failed to refresh warning state for %s/%s", msv, exam_id)
        return
    finally:
        db.close()

    session["max_warnings"] = int(max_warnings)
    session["session_warning_count"] = int(confirmed_count)
    review_required = int(confirmed_count) >= int(max_warnings)
    session["warning_review_required"] = bool(review_required)
    
    if review_required:
        session["warning_message"] = (
            f"Đã vượt ngưỡng cảnh báo đã xác nhận ({confirmed_count}/{max_warnings}). "
            "Cần giám thị xem xét và chủ động khóa phòng nếu cần."
        )
    else:
        session["warning_message"] = None


def _build_monitor_entry(session_token: str, include_preview: bool = False):
    session = exam_sessions.get(session_token)
    if not session:
        return None

    refresh_session_warning_state(session)
    classroom_state = get_classroom_state(int(session.get("class_id") or 0))

    snapshot = live_monitor_snapshots.get(session_token, {})
    updated_at_epoch = float(snapshot.get("updated_at_epoch", session.get("joined_at", 0.0)))
    now = time.time()
    historical_warning_count = int(session.get("historical_warning_count", 0))
    session_warning_count = int(session.get("session_warning_count", 0))
    total_warning_count = historical_warning_count + session_warning_count
    violation_scores = snapshot.get("violation_scores", {})
    violation_ratios = snapshot.get("violation_ratios", {})
    max_ratio = max(violation_ratios.values(), default=0.0)
    msv = str(session.get("msv") or "")
    class_id = int(session.get("class_id") or 0)
    yolo_world = get_latest_yolo_world_result(msv, class_id)
    yolo_world_earpiece = get_latest_earpiece_yolo_world_result(msv, class_id)
    yolo_world_meta = _safe_json_loads((yolo_world or {}).get("output_meta_json")) if yolo_world else {}
    yolo_world_earpiece_meta = _safe_json_loads((yolo_world_earpiece or {}).get("output_meta_json")) if yolo_world_earpiece else {}
    preview_review_status = str(snapshot.get("preview_review_status") or "pending").strip().lower()

    def _infer_manual_review_violation_type(trigger_type: Any, fallback: str = "hành vi nghi vấn") -> str:
        trigger_label = str(trigger_type or "").strip().lower()
        if any(token in trigger_label for token in ["earpiece", "tai nghe", "headset"]):
            return "tai nghe"
        if any(token in trigger_label for token in ["phone", "cell"]):
            return "điện thoại"
        if any(token in trigger_label for token in ["identity", "intruder", "nguoi la", "danh tinh"]):
            return "danh tính"
        return fallback

    manual_review_required = False
    manual_review_title = ""
    manual_review_reason = ""
    manual_review_violation_type = ""
    manual_review_source = ""
    if preview_review_status == "pending":
        if str((yolo_world_earpiece or {}).get("verdict") or "").strip().lower() == "review":
            manual_review_required = True
            manual_review_title = "Tầng 2 chưa chắc chắn"
            manual_review_reason = "Mô hình chuyên sâu tầng 2 chưa đủ chắc chắn để tự xác nhận hoặc bác bỏ vi phạm."
            manual_review_violation_type = "tai nghe"
            manual_review_source = "Tầng 2 chuyên sâu"
        elif str((yolo_world or {}).get("verdict") or "").strip().lower() == "review":
            manual_review_required = True
            manual_review_title = "Tầng 2 chưa chắc chắn"
            manual_review_reason = "YOLO-World trả về verdict review; cần giám thị đánh giá thủ công."
            manual_review_violation_type = _infer_manual_review_violation_type((yolo_world or {}).get("trigger_type"))
            manual_review_source = "YOLO-World"
        elif str(snapshot.get("identity_status") or "").strip() in {"Cần xác minh thêm", "Cảnh báo người lạ"} and int(snapshot.get("people_count", 0) or 0) <= 1:
            manual_review_required = True
            manual_review_title = "Chờ giám thị duyệt"
            manual_review_reason = "Danh tính trong lúc thi chưa đủ chắc chắn; cần đánh giá thủ công để tránh kết luận sai."
            manual_review_violation_type = "danh tính"
            manual_review_source = "Xác minh danh tính"

    entry = {
        "session_token": session_token,
        "msv": session.get("msv"),
        "full_name": session.get("full_name", ""),
        "class_id": session.get("class_id"),
        "class_name": session.get("class_name", ""),
        "class_locked": bool(classroom_state.get("locked", False)),
        "template_id": session.get("template_id"),
        "template_name": session.get("template_name", ""),
        "proctor_id": session.get("proctor_id"),
        "verified": bool(session.get("verified", False)),
        "submitted": bool(session.get("submitted", False)),
        "warning_locked": bool(session.get("warning_locked", False)),
        "warning_review_required": bool(session.get("warning_review_required", False)),
        "warning_message": session.get("warning_message"),
        "historical_warning_count": historical_warning_count,
        "session_warning_count": session_warning_count,
        "total_warning_count": total_warning_count,
        "warning_count": session_warning_count,
        "max_warnings": int(session.get("max_warnings", 5)),
        "status_text": snapshot.get("status_text") or "Chưa có dữ liệu giám sát.",
        "last_event": snapshot.get("last_event") or "Chưa có sự kiện nổi bật.",
        "identity_status": snapshot.get("identity_status") or "Chưa đối chiếu",
        "people_count": int(snapshot.get("people_count", 0) or 0),
        "phone_detected": bool(snapshot.get("phone_detected", False)),
        "away_detected": bool(snapshot.get("away_detected", False)),
        "pose_reliable": bool(snapshot.get("pose_reliable", False)),
        "head_pose_audit": bool(snapshot.get("head_pose_audit", False)),
        "head_pose_status": str(snapshot.get("head_pose_status") or "unavailable"),
        "pitch": round(float(snapshot.get("pitch", 0.0) or 0.0), 2),
        "yaw": round(float(snapshot.get("yaw", 0.0) or 0.0), 2),
        "violation_scores": violation_scores,
        "violation_ratios": violation_ratios,
        "risk_score": round(max_ratio * 100.0, 1),
        "risk_level": snapshot.get("risk_level") or ("high" if max_ratio >= 1.0 else "medium" if max_ratio >= 0.6 else "low"),
        "active": (now - updated_at_epoch) <= MONITOR_STALE_SECONDS and not session.get("submitted", False),
        "updated_at": snapshot.get("updated_at") or datetime.fromtimestamp(updated_at_epoch or now).strftime("%Y-%m-%d %H:%M:%S"),
        "seconds_since_update": round(max(0.0, now - updated_at_epoch), 1),
        "question_total": int(snapshot.get("question_total", 0) or 0),
        "answered_count": int(snapshot.get("answered_count", 0) or 0),
        "correct_count": int(snapshot.get("correct_count", 0) or 0),
        "wrong_count": int(snapshot.get("wrong_count", 0) or 0),
        "unanswered_count": int(snapshot.get("unanswered_count", 0) or 0),
        "current_score": float(snapshot.get("current_score", 0.0) or 0.0),
        "frame_available": bool(snapshot.get("preview_b64")),
        "snapshot_status": "pending" if session.get("require_snapshot", False) else snapshot.get("snapshot_status") or ("available" if snapshot.get("preview_b64") else "missing"),
        "snapshot_updated_at": snapshot.get("snapshot_updated_at") or snapshot.get("updated_at"),
        "snapshot_source": snapshot.get("snapshot_source") or "--",
        "snapshot_requested": bool(session.get("require_snapshot", False)),
        "snapshot_reason": session.get("snapshot_reason") or snapshot.get("snapshot_reason"),
        "snapshot_requested_at": datetime.fromtimestamp(float(session.get("snapshot_requested_at", 0.0))).strftime("%Y-%m-%d %H:%M:%S") if session.get("snapshot_requested_at") else None,
        "snapshot_requested_by": session.get("snapshot_requested_by"),
        "preview_review_status": str(snapshot.get("preview_review_status") or "pending"),
        "preview_review_note": str(snapshot.get("preview_review_note") or ""),
        "preview_reviewed_by": snapshot.get("preview_reviewed_by"),
        "preview_reviewed_at": snapshot.get("preview_reviewed_at"),
        "manual_review_required": bool(manual_review_required),
        "manual_review_title": manual_review_title,
        "manual_review_reason": manual_review_reason,
        "manual_review_violation_type": manual_review_violation_type,
        "manual_review_source": manual_review_source,
        "model_summary": snapshot.get("model_summary", {}),
        "yolo_world": {
            "task_id": yolo_world.get("task_id") if yolo_world else None,
            "violation_id": yolo_world.get("violation_id") if yolo_world else None,
            "status": yolo_world.get("task_status") if yolo_world else "idle",
            "trigger_type": yolo_world.get("trigger_type") if yolo_world else None,
            "top_label": yolo_world.get("top_label") if yolo_world else None,
            "top_confidence": float(yolo_world.get("top_confidence") or 0.0) if yolo_world else 0.0,
            "verdict": yolo_world.get("verdict") if yolo_world else None,
            "risk_delta": float(yolo_world.get("risk_delta") or 0.0) if yolo_world else 0.0,
            "model_name": yolo_world.get("model_name") if yolo_world else None,
            "model_version": yolo_world.get("model_version") if yolo_world else None,
            "inference_ms": yolo_world.get("inference_ms") if yolo_world else None,
            "updated_at": str((yolo_world or {}).get("result_created_at") or (yolo_world or {}).get("task_updated_at") or "--"),
            "thresholds": (yolo_world_meta or {}).get("thresholds") if yolo_world else None,
            "frame_verdict": (yolo_world_meta or {}).get("frame_verdict") if yolo_world else None,
            # auto_dismiss_eligible=True: YOLO-World found NO earpiece → violation
            # is likely a false positive; proctor can one-click dismiss via
            # POST /dismiss_yolo_fp/{violation_id} without manual frame review.
            "auto_dismiss_eligible": bool(
                yolo_world
                and yolo_world.get("verdict") == "review"
                and float(yolo_world.get("risk_delta") or 0.0) < -0.05
                and "earpiece" in str(yolo_world.get("trigger_type") or "").lower()
            ),
        },
        "earpiece_tier2": {
            "violation_id": (yolo_world_earpiece or {}).get("violation_id"),
            "trigger_type": (yolo_world_earpiece or {}).get("trigger_type"),
            "verdict": (yolo_world_earpiece or {}).get("verdict"),
            "top_label": (yolo_world_earpiece or {}).get("top_label"),
            "top_confidence": float((yolo_world_earpiece or {}).get("top_confidence") or 0.0),
            "risk_delta": float((yolo_world_earpiece or {}).get("risk_delta") or 0.0),
            "inference_ms": (yolo_world_earpiece or {}).get("inference_ms"),
            "updated_at": str((yolo_world_earpiece or {}).get("result_created_at") or (yolo_world_earpiece or {}).get("task_updated_at") or "--"),
            "status": (yolo_world_earpiece or {}).get("task_status") or "idle",
            "thresholds": (yolo_world_earpiece_meta or {}).get("thresholds"),
            "frame_verdict": (yolo_world_earpiece_meta or {}).get("frame_verdict"),
        },
    }
    if include_preview:
        entry["preview_b64"] = snapshot.get("preview_b64")
    return entry


def _collect_monitor_entries(predicate, include_preview: bool = False):
    cleanup_expired_sessions()
    entries = []
    for session_token, session in exam_sessions.items():
        if not predicate(session):
            continue
        entry = _build_monitor_entry(session_token, include_preview=include_preview)
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return entries


def get_classroom_state(class_id: int):
    state = active_classrooms.setdefault(class_id, {"broadcast": "Hệ thống đang giám sát...", "locked": False})
    db = get_db_connection()
    try:
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT status FROM classes WHERE class_id = %s", (class_id,))
        row = cur.fetchone()
    finally:
        db.close()

    if not row:
        return {"broadcast": "Lớp thi không tồn tại.", "locked": True}

    locked = row["status"] != "active"
    broadcast = state.get("broadcast") or "Hệ thống đang giám sát..."
    if locked:
        broadcast = "Phòng thi đã bị khóa hoặc đã kết thúc."
    return {"broadcast": broadcast, "locked": locked}


def require_exam_session(x_exam_token: Optional[str], class_id: Optional[int] = None, msv: Optional[str] = None, allow_locked: bool = False, require_verified: bool = False):
    cleanup_expired_sessions()
    if not x_exam_token:
        raise HTTPException(status_code=401, detail="Thiếu phiên thi hợp lệ")

    session = exam_sessions.get(x_exam_token)
    if not session:
        raise HTTPException(status_code=401, detail="Phiên thi không hợp lệ hoặc đã hết hạn")

    if class_id is not None and session["class_id"] != class_id:
        raise HTTPException(status_code=403, detail="Phiên thi không khớp với lớp yêu cầu")
    if msv is not None and session["msv"] != msv:
        raise HTTPException(status_code=403, detail="Không được phép truy cập tài nguyên của sinh viên khác")
    if require_verified and not session.get("verified", False):
        raise HTTPException(status_code=403, detail="Bạn cần hoàn tất xác minh danh tính trước khi vào phòng thi")

    classroom_state = merge_session_lock_state(session, get_classroom_state(session["class_id"]))
    if classroom_state["locked"] and not allow_locked:
        raise HTTPException(status_code=403, detail=classroom_state["broadcast"])

    return session, classroom_state

# ==========================================
# API DÀNH CHO SINH VIÊN
# ==========================================

@app.post("/api/student/login")
def student_login(msv: str = Form(...), password: str = Form(...)):
    try:
        db = get_db_connection(); cur = db.cursor(dictionary=True)
        cur.execute("SELECT full_name, password FROM students WHERE msv = %s", (msv,))
        res = cur.fetchone()
        if not res:
            db.close()
            return {"status": "error", "message": "Sai Mã sinh viên hoặc Mật khẩu!"}

        verified, upgraded = verify_and_upgrade_password(cur, "students", "msv", msv, password, res.get("password"))
        if not verified:
            db.close()
            return {"status": "error", "message": "Sai Mã sinh viên hoặc Mật khẩu!"}
        if upgraded:
            db.commit()
        db.close()
        return {"status": "success", "full_name": res["full_name"]}
        return {"status": "error", "message": "Sai Mã sinh viên hoặc Mật khẩu!"}
    except Exception as e:
        logger.exception("Student login failed for %s", msv)
        return {"status": "error", "message": str(e)}


@app.post("/api/admin/login")
def admin_login(admin_id: str = Form(...), password: str = Form(...)):
    try:
        cleanup_expired_sessions()
        db = get_db_connection(); cur = db.cursor(dictionary=True)
        cur.execute("SELECT admin_id, full_name, password FROM admins WHERE admin_id = %s", (admin_id,))
        row = cur.fetchone()
        if not row:
            db.close()
            return {"status": "error", "message": "Sai ID hoặc Mật khẩu Quản trị!"}

        verified, upgraded = verify_and_upgrade_password(cur, "admins", "admin_id", admin_id, password, row.get("password"))
        if not verified:
            db.close()
            return {"status": "error", "message": "Sai ID hoặc Mật khẩu Quản trị!"}
        if upgraded:
            db.commit()
        db.close()

        token = secrets.token_urlsafe(32)
        admin_sessions[token] = {
            "admin_id": row["admin_id"],
            "full_name": row["full_name"],
            "expires_at": time.time() + STAFF_SESSION_TTL_SECONDS,
        }
        return {"status": "success", "admin_id": row["admin_id"], "full_name": row["full_name"], "token": token}
    except Exception as exc:
        logger.exception("Admin login failed for %s", admin_id)
        return {"status": "error", "message": str(exc)}


@app.post("/api/proctor/login")
def proctor_login(proctor_id: str = Form(...), password: str = Form(...)):
    try:
        cleanup_expired_sessions()
        db = get_db_connection(); cur = db.cursor(dictionary=True)
        cur.execute("SELECT proctor_id, full_name, password FROM proctors WHERE proctor_id = %s", (proctor_id,))
        row = cur.fetchone()
        if not row:
            db.close()
            return {"status": "error", "message": "Sai ID hoặc Mật khẩu Giám thị!"}

        verified, upgraded = verify_and_upgrade_password(cur, "proctors", "proctor_id", proctor_id, password, row.get("password"))
        if not verified:
            db.close()
            return {"status": "error", "message": "Sai ID hoặc Mật khẩu Giám thị!"}
        if upgraded:
            db.commit()
        db.close()

        token = secrets.token_urlsafe(32)
        proctor_sessions[token] = {
            "proctor_id": row["proctor_id"],
            "full_name": row["full_name"],
            "expires_at": time.time() + STAFF_SESSION_TTL_SECONDS,
        }
        return {"status": "success", "proctor_id": row["proctor_id"], "full_name": row["full_name"], "token": token}
    except Exception as exc:
        logger.exception("Proctor login failed for %s", proctor_id)
        return {"status": "error", "message": str(exc)}


@app.post("/api/password-recovery/request")
def request_password_recovery(
    role: str = Form(...),
    account_id: str = Form(...),
    full_name: str = Form(...),
    note: str = Form(""),
):
    normalized_role, table_name, id_column = _get_role_mapping(role)
    account_id = str(account_id or "").strip()
    full_name = str(full_name or "").strip()
    note = str(note or "").strip()
    if not account_id or not full_name:
        raise HTTPException(status_code=400, detail="Thiếu mã tài khoản hoặc họ tên để gửi yêu cầu")

    # Fix #10: Implement rate-limiting on password recovery
    account_key = f"{normalized_role}:{account_id}"
    if not _check_password_recovery_rate_limit(account_key):
        logger.warning("Password recovery rate-limit exceeded for %s", account_key)
        return {"status": "error", "message": "Bạn đã vượt quá số lần yêu cầu trong 1 giờ. Vui lòng thử lại sau."}

    db = get_db_connection()
    try:
        cur = db.cursor(dictionary=True)
        cur.execute(
            f"SELECT {id_column}, full_name FROM {table_name} WHERE {id_column} = %s AND full_name = %s",
            (account_id, full_name),
        )
        row = cur.fetchone()
        if not row:
            return {"status": "error", "message": "Thông tin tài khoản không khớp với dữ liệu hệ thống."}

        cur.execute(
            """
            SELECT request_id FROM password_reset_requests
            WHERE account_role = %s AND account_id = %s AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (normalized_role, account_id),
        )
        existing = cur.fetchone()
        if existing:
            return {"status": "error", "message": "Đã có yêu cầu cấp lại mật khẩu đang chờ xử lý cho tài khoản này."}

        cur.execute(
            """
            INSERT INTO password_reset_requests (account_role, account_id, full_name, request_note)
            VALUES (%s, %s, %s, %s)
            """,
            (normalized_role, account_id, full_name, note or None),
        )
        db.commit()
        logger.info("Password recovery requested for %s/%s", normalized_role, account_id)
        return {"status": "success", "message": "Đã ghi nhận yêu cầu cấp lại mật khẩu. Quản trị viên sẽ cấp mật khẩu tạm sau khi kiểm tra."}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Password recovery request failed for %s/%s", normalized_role, account_id)
        return {"status": "error", "message": str(exc)}
    finally:
        db.close()


@app.get("/api/admin/password-recovery/requests")
def list_password_recovery_requests(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    status: Optional[str] = Query(default=None),
    keyword: str = Query(default=""),
):
    admin_session = require_admin_session(x_admin_token)
    keyword = str(keyword or "").strip()
    status = str(status or "").strip().lower()
    allowed_status = {"pending", "approved", "rejected"}
    if status and status not in allowed_status:
        raise HTTPException(status_code=400, detail="Trạng thái lọc không hợp lệ")

    db = get_db_connection()
    try:
        cur = db.cursor(dictionary=True)
        clauses = []
        params = []
        if status:
            clauses.append("status = %s")
            params.append(status)
        if keyword:
            clauses.append("(account_role LIKE %s OR account_id LIKE %s OR full_name LIKE %s OR request_note LIKE %s OR resolved_note LIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw, kw, kw])
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cur.execute(
            f"""
            SELECT request_id, account_role, account_id, full_name, request_note, status, resolved_note, approved_by, approved_at, created_at, updated_at
            FROM password_reset_requests
            {where_sql}
            ORDER BY created_at DESC
            LIMIT 300
            """,
            tuple(params),
        )
        rows = cur.fetchall()
        return {
            "status": "success",
            "data": rows,
            "requested_by": admin_session.get("admin_id"),
        }
    finally:
        db.close()


@app.post("/api/admin/password-recovery/requests/{request_id}/approve")
def approve_password_recovery_request(
    request_id: int,
    temp_password: str = Form(...),
    note: str = Form(""),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
):
    admin_session = require_admin_session(x_admin_token)
    temp_password = str(temp_password or "").strip()
    if len(temp_password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu tạm cần có ít nhất 6 ký tự")

    db = get_db_connection()
    try:
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT request_id, account_role, account_id, status FROM password_reset_requests WHERE request_id = %s", (request_id,))
        request_row = cur.fetchone()
        if not request_row:
            raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu cấp lại mật khẩu")
        if str(request_row.get("status") or "") != "pending":
            raise HTTPException(status_code=409, detail="Yêu cầu này đã được xử lý trước đó")

        _, table_name, id_column = _get_role_mapping(str(request_row.get("account_role") or ""))
        cur.execute(
            f"UPDATE {table_name} SET password = %s WHERE {id_column} = %s",
            (hash_password(temp_password), request_row["account_id"]),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Tài khoản cần cấp lại mật khẩu không còn tồn tại")

        resolved_note = str(note or "").strip() or "Đã cấp mật khẩu tạm mới"
        cur.execute(
            """
            UPDATE password_reset_requests
            SET status = 'approved', resolved_note = %s, approved_by = %s, approved_at = %s
            WHERE request_id = %s
            """,
            (resolved_note, admin_session.get("admin_id") or "admin", datetime.now(), request_id),
        )
        db.commit()
    finally:
        db.close()

    _write_audit_log(
        admin_session.get("full_name") or admin_session.get("admin_id") or "admin",
        f"Duyệt cấp lại mật khẩu cho {request_row['account_role']}:{request_row['account_id']} (request_id={request_id})",
    )
    return {
        "status": "success",
        "message": "Đã cấp mật khẩu tạm mới.",
        "request_id": request_id,
        "account_role": request_row["account_role"],
        "account_id": request_row["account_id"],
    }


@app.post("/api/admin/password-recovery/requests/{request_id}/reject")
def reject_password_recovery_request(
    request_id: int,
    note: str = Form(""),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
):
    admin_session = require_admin_session(x_admin_token)
    resolved_note = str(note or "").strip() or "Từ chối yêu cầu cấp lại mật khẩu"

    db = get_db_connection()
    try:
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT request_id, account_role, account_id, status FROM password_reset_requests WHERE request_id = %s", (request_id,))
        request_row = cur.fetchone()
        if not request_row:
            raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu cấp lại mật khẩu")
        if str(request_row.get("status") or "") != "pending":
            raise HTTPException(status_code=409, detail="Yêu cầu này đã được xử lý trước đó")

        cur.execute(
            """
            UPDATE password_reset_requests
            SET status = 'rejected', resolved_note = %s, approved_by = %s, approved_at = %s
            WHERE request_id = %s
            """,
            (resolved_note, admin_session.get("admin_id") or "admin", datetime.now(), request_id),
        )
        db.commit()
    finally:
        db.close()

    _write_audit_log(
        admin_session.get("full_name") or admin_session.get("admin_id") or "admin",
        f"Từ chối yêu cầu cấp lại mật khẩu cho {request_row['account_role']}:{request_row['account_id']} (request_id={request_id})",
    )
    return {
        "status": "success",
        "message": "Đã từ chối yêu cầu cấp lại mật khẩu.",
        "request_id": request_id,
        "account_role": request_row["account_role"],
        "account_id": request_row["account_id"],
    }

@app.post("/api/student/join_class")
def join_class(msv: str = Form(...), class_id: int = Form(...), class_password: str = Form(...)):
    """Kiểm tra quyền vào lớp, sau đó yêu cầu xác minh danh tính ở bước riêng."""
    try:
        cleanup_expired_sessions()
        db = get_db_connection(); cur = db.cursor(dictionary=True)
        cur.execute(
            """
            SELECT c.*, et.proctor_id, et.template_name
            FROM classes c
            LEFT JOIN exam_templates et ON c.template_id = et.template_id
            WHERE c.class_id = %s AND c.class_password = %s AND c.status = 'active'
            """,
            (class_id, class_password),
        )
        cls = cur.fetchone()
        if not cls:
            db.close(); return {"status": "error", "message": "Sai thông tin phòng thi!"}
        
        cur.execute("SELECT * FROM class_students WHERE class_id = %s AND msv = %s", (class_id, msv))
        enrollment = cur.fetchone(); db.close()
        
        if not enrollment: return {"status": "error", "message": "Bạn không có trong danh sách thi!"}
        
        db = get_db_connection()
        try:
            cur = db.cursor(dictionary=True)
            cur.execute("SELECT full_name FROM students WHERE msv = %s", (msv,))
            student = cur.fetchone() or {}
            cur.execute("SELECT COUNT(*) AS count FROM violations WHERE msv = %s AND exam_id = %s", (msv, class_id))
            warning_row = cur.fetchone() or {"count": 0}
            historical_warning_count = int(warning_row.get("count", 0))
            max_warnings = get_max_warnings(cur)
        finally:
            db.close()

        if class_id not in active_classrooms:
            active_classrooms[class_id] = {"broadcast": "Hệ thống đang giám sát...", "locked": False}

        session_token = secrets.token_urlsafe(32)
        exam_sessions[session_token] = {
            "msv": msv,
            "class_id": class_id,
            "submitted": False,
            "verified": False,
            "warning_locked": False,
            "warning_review_required": False,
            "warning_message": None,
            "historical_warning_count": historical_warning_count,
            "session_warning_count": 0,
            "max_warnings": int(max_warnings),
            "full_name": student.get("full_name", ""),
            "class_name": cls.get("class_name", ""),
            "template_id": cls.get("template_id"),
            "template_name": cls.get("template_name", ""),
            "proctor_id": cls.get("proctor_id"),
            "require_snapshot": False,
            "snapshot_reason": None,
            "snapshot_requested_at": 0.0,
            "snapshot_requested_by": None,
            "joined_at": time.time(),
            "expires_at": time.time() + SESSION_TTL_SECONDS,
        }

        return {"status": "success", "class_name": cls["class_name"], "session_token": session_token, "verification_required": True}
    except Exception as e:
        logger.exception("Join class failed for %s in class %s", msv, class_id)
        return {"status": "error", "message": str(e)}

@app.post("/api/student/verify_identity")
async def verify_identity(files: List[UploadFile] = File(...), x_exam_token: Optional[str] = Header(default=None, alias="X-Exam-Token")):
    try:
        session, _ = require_exam_session(x_exam_token)
        msv = session["msv"]

        db = get_db_connection(); cur = db.cursor(dictionary=True)
        face_threshold = get_face_threshold(cur)
        reference_paths = get_student_reference_face_paths(cur, msv)
        if not reference_paths:
            db.close()
            return {"status": "error", "message": "Không có ảnh gốc để xác minh"}

        reference_signatures = []
        for reference_name in reference_paths:
            ref_path = os.path.join("server_database", reference_name)
            if not os.path.exists(ref_path):
                continue
            ref_frame = cv2.imread(ref_path)
            reference_signature = build_reference_signature(ref_frame)
            if reference_signature is None:
                continue
            reference_signatures.append({"path": reference_name, "signature": reference_signature})

        if not reference_signatures:
            db.close()
            return {"status": "error", "message": "Ảnh gốc không hợp lệ"}

        best_confidence = 0.0
        frame_confidences = []
        strict_matches = 0
        near_match_margin = 0.03
        for file in files:
            img_bytes = await file.read()
            npimg = np.frombuffer(img_bytes, np.uint8)
            frame = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
            if frame is None:
                continue
            try:
                verified = False
                confidence = 0.0
                for reference_item in reference_signatures:
                    candidate_verified, candidate_confidence = compare_with_signature(
                        frame,
                        reference_item["signature"],
                        threshold=face_threshold,
                        assume_face_roi=True,
                    )
                    if not candidate_verified:
                        fallback_verified, fallback_confidence = compare_with_signature(
                            frame,
                            reference_item["signature"],
                            threshold=face_threshold,
                        )
                        if fallback_verified or float(fallback_confidence) > float(candidate_confidence):
                            candidate_verified, candidate_confidence = fallback_verified, fallback_confidence
                    if candidate_verified or float(candidate_confidence) > float(confidence):
                        verified, confidence = candidate_verified, candidate_confidence
                best_confidence = max(best_confidence, float(confidence))
                frame_confidences.append(float(confidence))
                if verified:
                    strict_matches += 1
                logger.info("Identity verification for %s: verified=%s confidence=%.3f threshold=%.3f", msv, verified, confidence, face_threshold)
            except Exception:
                logger.exception("Identity verification failed while processing frame for %s", msv)

        sorted_confidences = sorted(frame_confidences, reverse=True)
        near_match_count = sum(1 for value in frame_confidences if value >= max(0.0, float(face_threshold) - near_match_margin))
        top2_average = (sum(sorted_confidences[:2]) / 2.0) if len(sorted_confidences) >= 2 else (sorted_confidences[0] if sorted_confidences else 0.0)
        verified = False
        if strict_matches >= 1:
            verified = True
        elif len(sorted_confidences) >= 2 and near_match_count >= 2:
            # Conservative fallback: accept only when two frames consistently land very close to the threshold.
            verified = top2_average >= max(0.0, float(face_threshold) - 0.015) and float(sorted_confidences[0]) >= max(0.0, float(face_threshold) - 0.01)

        if verified:
            session["verified"] = True
            session["verified_at"] = time.time()
            db.close()
            return {
                "status": "success",
                "message": "Xác minh danh tính thành công",
                "confidence": round(float(best_confidence), 3),
                "threshold": round(face_threshold, 3),
                "strict_match_count": int(strict_matches),
                "near_match_count": int(near_match_count),
                "top2_average": round(float(top2_average), 3),
            }

        db.close()
        return {
            "status": "error",
            "message": "Xác minh khuôn mặt thất bại",
            "confidence": round(best_confidence, 3),
            "threshold": round(face_threshold, 3),
            "strict_match_count": int(strict_matches),
            "near_match_count": int(near_match_count),
            "top2_average": round(float(top2_average), 3),
        }
    except HTTPException as exc:
        raise exc
    except Exception as exc:
        logger.exception("Identity verification endpoint failed")
        return {"status": "error", "message": str(exc)}

@app.get("/api/student/quiz/{class_id}")
def get_quiz_content(class_id: int, x_exam_token: Optional[str] = Header(default=None, alias="X-Exam-Token")):
    """Lấy đúng bộ đề của Lớp (Fix lỗi nạp đề ảo)"""
    try:
        require_exam_session(x_exam_token, class_id=class_id, require_verified=True)
        db = get_db_connection(); cur = db.cursor(dictionary=True)
        cur.execute("SELECT template_id FROM classes WHERE class_id = %s", (class_id,))
        cls = cur.fetchone()
        if not cls or not cls['template_id']:
            db.close()
            raise HTTPException(status_code=404, detail="Lớp chưa có đề!")
        
        cur.execute("SELECT q_id AS question_id, question_text, option_a, option_b, option_c, option_d FROM question_bank WHERE template_id = %s", (cls['template_id'],))
        questions = cur.fetchall(); db.close()
        return {"status": "success", "data": questions}
    except HTTPException as exc:
        raise exc
    except Exception as e:
        logger.exception("Quiz fetch failed for class %s", class_id)
        return {"status": "error", "message": str(e)}

@app.post("/api/student/submit")
async def submit_exam(answers_json: str = Form(...), x_exam_token: Optional[str] = Header(default=None, alias="X-Exam-Token")):
    """Server tự chấm điểm dựa trên DB (Chống hack điểm)"""
    try:
        session, _ = require_exam_session(x_exam_token, require_verified=True)
        msv = session["msv"]
        exam_id = session["class_id"]
        student_answers = json.loads(answers_json)
        if not isinstance(student_answers, dict):
            raise HTTPException(status_code=400, detail="Định dạng đáp án không hợp lệ")

        total_score = 0.0
        db = get_db_connection(); cur = db.cursor(dictionary=True)
        cur.execute("SELECT score, submission_time FROM exam_results WHERE msv = %s AND exam_id = %s LIMIT 1", (msv, exam_id))
        existing_result = cur.fetchone()
        if existing_result:
            db.close()
            session["submitted"] = True
            return {
                "status": "success",
                "score": float(existing_result["score"]),
                "already_submitted": True,
                "message": "Bài thi này đã được nộp trước đó.",
            }

        cur.execute("SELECT q.q_id AS question_id, q.correct_option, q.points FROM question_bank q JOIN classes c ON q.template_id = c.template_id WHERE c.class_id = %s", (exam_id,))
        correct_data = cur.fetchall()
        for correct in correct_data:
            q_id = str(correct['question_id'])
            if q_id in student_answers and student_answers[q_id] == correct['correct_option']:
                total_score += float(correct['points'])
        cur.execute("INSERT INTO exam_results (msv, exam_id, score, answers_json, submission_time) VALUES (%s,%s,%s,%s,%s)", (msv, exam_id, total_score, answers_json, datetime.now()))
        db.commit(); db.close()
        session["submitted"] = True
        return {"status": "success", "score": total_score, "already_submitted": False}
    except HTTPException as exc:
        raise exc
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Định dạng JSON của đáp án không hợp lệ")
    except Exception as e:
        logger.exception("Submit exam failed")
        return {"status": "error", "message": str(e)}

@app.get("/api/get_face_refs/{msv}")
def get_face_references(msv: str, x_exam_token: Optional[str] = Header(default=None, alias="X-Exam-Token")):
    try:
        # Fix #9: Validate MSV format and verify it matches token
        if not validate_msv(msv):
            raise HTTPException(status_code=400, detail="MSV không hợp lệ")
        
        # Verify exam session and ensure requesting user's MSV matches
        require_exam_session(x_exam_token, msv=msv, require_verified=True)
        
        db = get_db_connection()
        cur = db.cursor(dictionary=True)
        rows = get_student_reference_face_rows(cur, msv)
        db.close()
        refs = [
            {
                "ref_id": int(row["ref_id"]),
                "face_image": row["face_image"],
                "is_primary": bool(row["is_primary"]),
            }
            for row in rows
        ]
        return {"status": "success", "references": refs}
    except HTTPException as exc:
        raise exc
    except Exception:
        logger.exception("Face reference metadata retrieval failed for %s", msv)
        raise HTTPException(status_code=500, detail="Lỗi truy xuất danh sách ảnh tham chiếu")


@app.get("/api/get_face/{msv}")
def get_face(msv: str, face_image: Optional[str] = Query(default=None), x_exam_token: Optional[str] = Header(default=None, alias="X-Exam-Token")):
    try:
        require_exam_session(x_exam_token, msv=msv, require_verified=True)
        db = get_db_connection(); cur = db.cursor(dictionary=True)
        reference_rows = get_student_reference_face_rows(cur, msv)
        db.close()
        if face_image:
            for row in reference_rows:
                if str(row["face_image"]) == str(face_image):
                    path = os.path.join("server_database", row["face_image"])
                    if os.path.exists(path):
                        return FileResponse(path)
                    break
            raise HTTPException(status_code=404, detail="Không tìm thấy ảnh tham chiếu được yêu cầu")

        for row in reference_rows:
            reference_name = row["face_image"]
            path = os.path.join("server_database", reference_name)
            if os.path.exists(path):
                return FileResponse(path)
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh tham chiếu")
    except HTTPException as exc:
        logger.warning("Face image access rejected for %s: %s", msv, exc.detail)
        raise exc
    except Exception:
        logger.exception("Face image retrieval failed for %s", msv)
        raise HTTPException(status_code=500, detail="Lỗi truy xuất ảnh tham chiếu")

@app.get("/api/student/sync/{exam_id}")
def sync_student_data(exam_id: int, x_exam_token: Optional[str] = Header(default=None, alias="X-Exam-Token")):
    try:
        session, classroom_state = require_exam_session(x_exam_token, class_id=exam_id, allow_locked=True)
        refresh_session_warning_state(session)
        response = dict(classroom_state)
        if session.get("require_snapshot"):
            response["require_snapshot"] = True
            response["snapshot_reason"] = session.get("snapshot_reason") or "Hệ thống yêu cầu ảnh snapshot mới nhất."
        else:
            response["require_snapshot"] = False
        return response
    except HTTPException as exc:
        raise exc


@app.get("/api/student/runtime_config")
def get_student_runtime_config(x_exam_token: Optional[str] = Header(default=None, alias="X-Exam-Token")):
    try:
        session, _ = require_exam_session(x_exam_token, allow_locked=True)
        refresh_session_warning_state(session)
        db = get_db_connection()
        try:
            cur = db.cursor(dictionary=True)
            face_threshold = get_face_threshold(cur)
            max_warnings = get_max_warnings(cur)
        finally:
            db.close()

        return {
            "status": "success",
            "face_threshold": round(float(face_threshold), 3),
            "max_warnings": int(max_warnings),
            "warnings_count": int(session.get("session_warning_count", 0)),
            "session_warning_count": int(session.get("session_warning_count", 0)),
            "historical_warning_count": int(session.get("historical_warning_count", 0)),
            "total_warning_count": int(session.get("historical_warning_count", 0)) + int(session.get("session_warning_count", 0)),
            "warning_locked": bool(session.get("warning_locked", False)),
            "warning_review_required": bool(session.get("warning_review_required", False)),
            "warning_message": session.get("warning_message"),
        }
    except HTTPException as exc:
        raise exc


@app.post("/api/student/monitor_snapshot")
def update_monitor_snapshot(
    payload: Dict[str, Any] = Body(...),
    x_exam_token: Optional[str] = Header(default=None, alias="X-Exam-Token"),
):
    try:
        session, _ = require_exam_session(x_exam_token, allow_locked=True, require_verified=True)
        answers = payload.get("answers", {})
        class_id = int(session.get("class_id") or 0)
        if class_id > 0:
            metrics = score_live_answers(class_id, answers)
        else:
            metrics = {
                "question_total": 0,
                "answered_count": 0,
                "correct_count": 0,
                "wrong_count": 0,
                "unanswered_count": 0,
                "current_score": 0.0,
            }

        violation_scores = _normalize_monitor_scores(payload, "violation_scores")
        violation_thresholds = _normalize_monitor_scores(payload, "violation_thresholds")
        violation_ratios = {}
        for name, score in violation_scores.items():
            threshold = max(float(violation_thresholds.get(name, 1.0) or 1.0), 0.1)
            violation_ratios[name] = min(1.4, score / threshold)

        previous_snapshot = live_monitor_snapshots.get(x_exam_token, {})
        preview_b64 = str(payload.get("preview_b64") or "").strip()
        if len(preview_b64) > 1_200_000:
            preview_b64 = ""
        elif preview_b64:
            try:
                base64.b64decode(preview_b64.encode("ascii"), validate=True)
            except Exception:
                preview_b64 = ""

        max_ratio = max(violation_ratios.values(), default=0.0)
        risk_level = "high" if max_ratio >= 1.0 else "medium" if max_ratio >= 0.6 else "low"
        live_monitor_snapshots[x_exam_token] = {
            "updated_at_epoch": time.time(),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "preview_b64": preview_b64 or previous_snapshot.get("preview_b64"),
            "snapshot_status": previous_snapshot.get("snapshot_status") or ("available" if preview_b64 or previous_snapshot.get("preview_b64") else "missing"),
            "snapshot_updated_at": previous_snapshot.get("snapshot_updated_at") or previous_snapshot.get("updated_at"),
            "snapshot_reason": previous_snapshot.get("snapshot_reason"),
            "snapshot_source": previous_snapshot.get("snapshot_source") or "--",
            "preview_review_status": previous_snapshot.get("preview_review_status") or "pending",
            "preview_review_note": previous_snapshot.get("preview_review_note") or "",
            "preview_reviewed_by": previous_snapshot.get("preview_reviewed_by"),
            "preview_reviewed_at": previous_snapshot.get("preview_reviewed_at"),
            "status_text": str(payload.get("status_text") or "AI đang theo dõi bình thường."),
            "last_event": str(payload.get("last_event") or payload.get("status_text") or "Không có sự kiện mới."),
            "identity_status": str(payload.get("identity_status") or "Chưa đối chiếu"),
            "people_count": int(payload.get("people_count", 0) or 0),
            "phone_detected": bool(payload.get("phone_detected", False)),
            "away_detected": bool(payload.get("away_detected", False)),
            "pose_reliable": bool(payload.get("pose_reliable", False)),
            "head_pose_audit": bool(payload.get("head_pose_audit", False)),
            "head_pose_status": str(payload.get("head_pose_status") or "unavailable"),
            "pitch": float(payload.get("pitch", 0.0) or 0.0),
            "yaw": float(payload.get("yaw", 0.0) or 0.0),
            "violation_scores": violation_scores,
            "violation_ratios": violation_ratios,
            "risk_level": risk_level,
            "model_summary": {
                "tracked_people": int(payload.get("people_count", 0) or 0),
                "identity_status": str(payload.get("identity_status") or "Chưa đối chiếu"),
                "phone_detected": bool(payload.get("phone_detected", False)),
                "away_detected": False,
                "pose_reliable": bool(payload.get("pose_reliable", False)),
                "head_pose_audit": bool(payload.get("head_pose_audit", False)),
                "head_pose_status": str(payload.get("head_pose_status") or "unavailable"),
            },
            **metrics,
        }
        return {"status": "success", "risk_level": risk_level}
    except HTTPException as exc:
        raise exc


@app.post("/api/student/upload_snapshot")
async def upload_monitor_snapshot(
    file: UploadFile = File(...),
    source: str = Form("manual"),
    reason: str = Form(""),
    x_exam_token: Optional[str] = Header(default=None, alias="X-Exam-Token"),
):
    try:
        session, _ = require_exam_session(x_exam_token, allow_locked=True, require_verified=True)
        image_bytes = await file.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="Không nhận được dữ liệu ảnh snapshot")

        encoded_image = base64.b64encode(image_bytes).decode("ascii")
        if len(encoded_image) > 1_200_000:
            raise HTTPException(status_code=413, detail="Ảnh snapshot quá lớn")

        current_snapshot = live_monitor_snapshots.get(x_exam_token, {})
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        current_snapshot.update(
            {
                "preview_b64": encoded_image,
                "snapshot_status": "available",
                "snapshot_updated_at": now_text,
                "snapshot_reason": reason or session.get("snapshot_reason") or source,
                "snapshot_source": source,
            }
        )
        live_monitor_snapshots[x_exam_token] = current_snapshot
        session["require_snapshot"] = False
        session["snapshot_reason"] = None
        session["snapshot_requested_at"] = 0.0
        session["snapshot_requested_by"] = None
        return {"status": "success", "snapshot_updated_at": now_text}
    except HTTPException as exc:
        raise exc


@app.post("/api/monitor/proctor/{proctor_id}/{session_token}/request_snapshot")
def request_proctor_snapshot(proctor_id: str, session_token: str, x_proctor_token: Optional[str] = Header(default=None, alias="X-Proctor-Token")):
    require_proctor_session(x_proctor_token, proctor_id=proctor_id)
    session = exam_sessions.get(session_token)
    if not session or str(session.get("proctor_id") or "") != str(proctor_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên giám sát phù hợp")

    session["require_snapshot"] = True
    session["snapshot_reason"] = "Giám thị yêu cầu ảnh snapshot mới nhất."
    session["snapshot_requested_at"] = time.time()
    session["snapshot_requested_by"] = str(proctor_id)
    current_snapshot = live_monitor_snapshots.get(session_token, {})
    current_snapshot["snapshot_status"] = "pending"
    current_snapshot["snapshot_reason"] = session["snapshot_reason"]
    live_monitor_snapshots[session_token] = current_snapshot
    return {"status": "success", "message": "Đã ghi nhận yêu cầu snapshot. Ảnh sẽ được gửi khi máy sinh viên đồng bộ phiên tiếp theo."}


@app.post("/api/monitor/proctor/{proctor_id}/{session_token}/review_preview")
def review_proctor_preview(
    proctor_id: str,
    session_token: str,
    action: str = Form(...),
    note: str = Form(""),
    violation_id: Optional[int] = Form(default=None),
    x_proctor_token: Optional[str] = Header(default=None, alias="X-Proctor-Token"),
):
    """Giám thị đánh giá ảnh preview hiện tại: confirm hoặc reject."""
    require_proctor_session(x_proctor_token, proctor_id=proctor_id)
    session = exam_sessions.get(session_token)
    if not session or str(session.get("proctor_id") or "") != str(proctor_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên giám sát phù hợp")

    action_normalized = str(action or "").strip().lower()
    if action_normalized not in {"confirm", "reject"}:
        raise HTTPException(status_code=422, detail="action must be either 'confirm' or 'reject'")

    current_snapshot = live_monitor_snapshots.get(session_token, {})
    if not current_snapshot or not current_snapshot.get("preview_b64"):
        raise HTTPException(status_code=409, detail="Phiên hiện chưa có ảnh preview để đánh giá")

    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    current_snapshot["preview_review_status"] = action_normalized
    current_snapshot["preview_review_note"] = str(note or "").strip()
    current_snapshot["preview_reviewed_by"] = str(proctor_id)
    current_snapshot["preview_reviewed_at"] = now_text
    live_monitor_snapshots[session_token] = current_snapshot
    review_db = _persist_preview_review_to_violation(
        violation_id=violation_id,
        action=action_normalized,
        note=str(note or "").strip(),
        proctor_id=str(proctor_id),
    )

    return {
        "status": "success",
        "message": "Đã lưu đánh giá preview.",
        "data": {
            "preview_review_status": action_normalized,
            "preview_review_note": current_snapshot.get("preview_review_note", ""),
            "preview_reviewed_by": str(proctor_id),
            "preview_reviewed_at": now_text,
            "violation_review_db": review_db,
        },
    }


@app.post("/api/monitor/proctor/{proctor_id}/classes/{class_id}/lock")
def lock_proctor_classroom(proctor_id: str, class_id: int, x_proctor_token: Optional[str] = Header(default=None, alias="X-Proctor-Token")):
    session = require_proctor_session(x_proctor_token, proctor_id=proctor_id)
    return _set_classroom_status(
        class_id=class_id,
        target_status="closed",
        actor_role="proctor",
        actor_id=str(session.get("proctor_id") or proctor_id),
        actor_name=str(session.get("full_name") or ""),
        allowed_proctor_id=str(session.get("proctor_id") or proctor_id),
    )


@app.post("/api/monitor/proctor/{proctor_id}/classes/{class_id}/unlock")
def unlock_proctor_classroom(proctor_id: str, class_id: int, x_proctor_token: Optional[str] = Header(default=None, alias="X-Proctor-Token")):
    session = require_proctor_session(x_proctor_token, proctor_id=proctor_id)
    return _set_classroom_status(
        class_id=class_id,
        target_status="active",
        actor_role="proctor",
        actor_id=str(session.get("proctor_id") or proctor_id),
        actor_name=str(session.get("full_name") or ""),
        allowed_proctor_id=str(session.get("proctor_id") or proctor_id),
    )


@app.get("/api/monitor/proctor/{proctor_id}")
def get_proctor_monitor_overview(proctor_id: str, x_proctor_token: Optional[str] = Header(default=None, alias="X-Proctor-Token")):
    require_proctor_session(x_proctor_token, proctor_id=proctor_id)
    entries = _collect_monitor_entries(lambda session: str(session.get("proctor_id") or "") == str(proctor_id))
    return {"status": "success", "data": entries}


@app.get("/api/monitor/proctor/{proctor_id}/{session_token}")
def get_proctor_monitor_detail(proctor_id: str, session_token: str, x_proctor_token: Optional[str] = Header(default=None, alias="X-Proctor-Token")):
    require_proctor_session(x_proctor_token, proctor_id=proctor_id)
    session = exam_sessions.get(session_token)
    if not session or str(session.get("proctor_id") or "") != str(proctor_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên giám sát phù hợp")
    entry = _build_monitor_entry(session_token, include_preview=True)
    if entry is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy dữ liệu giám sát")
    return {"status": "success", "data": entry}


@app.get("/api/monitor/admin")
def get_admin_monitor_overview(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin_session(x_admin_token)
    entries = _collect_monitor_entries(lambda session: True)
    return {"status": "success", "data": entries}


@app.get("/api/monitor/admin/{session_token}")
def get_admin_monitor_detail(session_token: str, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    require_admin_session(x_admin_token)
    entry = _build_monitor_entry(session_token, include_preview=False)
    if entry is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy dữ liệu giám sát")
    return {"status": "success", "data": entry}


@app.post("/api/monitor/admin/classes/{class_id}/lock")
def lock_admin_classroom(class_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    session = require_admin_session(x_admin_token)
    return _set_classroom_status(
        class_id=class_id,
        target_status="closed",
        actor_role="admin",
        actor_id=str(session.get("admin_id") or "admin"),
        actor_name=str(session.get("full_name") or ""),
    )


@app.post("/api/monitor/admin/classes/{class_id}/unlock")
def unlock_admin_classroom(class_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    session = require_admin_session(x_admin_token)
    return _set_classroom_status(
        class_id=class_id,
        target_status="active",
        actor_role="admin",
        actor_id=str(session.get("admin_id") or "admin"),
        actor_name=str(session.get("full_name") or ""),
    )

@app.post("/dismiss_yolo_fp/{violation_id}")
def dismiss_yolo_false_positive(violation_id: int, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")):
    """Dismiss an earpiece violation that YOLO-World classified as a false positive.

    Marks the latest YOLO-World result as reject so confirmed-warning counts
    are reduced by DB-backed aggregation.
    Only valid when the latest YOLO-World result for the corresponding task
    has verdict='review' and auto_dismiss_eligible=True.
    """
    require_admin_session(x_admin_token)
    db = get_db_connection()
    try:
        cur = db.cursor(dictionary=True)
        # Resolve the task and student from the violation
        cur.execute(
            """
            SELECT t.msv, t.exam_id, t.task_id, r.verdict, r.risk_delta, t.trigger_type
            FROM yolo_world_tasks t
            LEFT JOIN yolo_world_results r ON r.task_id = t.task_id
            WHERE t.violation_id = %s
            ORDER BY t.task_id DESC
            LIMIT 1
            """,
            (violation_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Không tìm thấy task YOLO-World cho vi phạm này")
        if row.get("verdict") != "review":
            raise HTTPException(status_code=409, detail="YOLO-World verdict không phải 'review'; không thể tự động hủy")
        trigger = str(row.get("trigger_type") or "").lower()
        if "earpiece" not in trigger and "tai nghe" not in trigger and "headset" not in trigger:
            raise HTTPException(status_code=409, detail="Chỉ hỗ trợ hủy tự động cho vi phạm loại earpiece")
        risk_delta = float(row.get("risk_delta") or 0.0)
        if risk_delta >= -0.05:
            raise HTTPException(status_code=409, detail="risk_delta không đủ âm; cần xác nhận thủ công")
        msv = str(row["msv"])
        exam_id = int(row["exam_id"])
        cur.execute(
            """
            UPDATE yolo_world_results
            SET verdict = 'reject', risk_delta = LEAST(risk_delta, -0.20)
            WHERE result_id = (
                SELECT result_id FROM (
                    SELECT r2.result_id
                    FROM yolo_world_results r2
                    JOIN yolo_world_tasks t2 ON t2.task_id = r2.task_id
                    WHERE t2.violation_id = %s
                    ORDER BY r2.result_id DESC
                    LIMIT 1
                ) z
            )
            """,
            (violation_id,),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Không tìm thấy kết quả YOLO-World để cập nhật")
        db.commit()
    except HTTPException:
        raise
    except Exception:
        logger.exception("dismiss_yolo_fp: DB lookup failed for violation_id=%s", violation_id)
        raise HTTPException(status_code=500, detail="Lỗi truy vấn cơ sở dữ liệu")
    finally:
        db.close()

    # Find the live session and decrement warning count
    matched_token = None
    for token, session in exam_sessions.items():
        if str(session.get("msv") or "") == msv and int(session.get("class_id") or 0) == exam_id:
            matched_token = token
            break
    if not matched_token:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên thi đang hoạt động cho sinh viên này")

    session = exam_sessions[matched_token]
    old_count = int(session.get("session_warning_count", 0))
    refresh_session_warning_state(session)
    new_count = int(session.get("session_warning_count", 0))
    # Invalidate YOLO result cache for this student
    cache_key = f"{msv}:{exam_id}"
    _yolo_result_cache.pop(cache_key, None)

    logger.info(
        "dismiss_yolo_fp: violation_id=%s msv=%s exam=%s warning_count %s→%s review_required=%s",
        violation_id, msv, exam_id, old_count, new_count, session.get("warning_review_required"),
    )
    return {
        "status": "dismissed",
        "violation_id": violation_id,
        "msv": msv,
        "session_warning_count": new_count,
        "warning_review_required": bool(session.get("warning_review_required", False)),
    }


@app.post("/upload_violation/")
async def upload_violation(error_type: str = Form(...), file: UploadFile = File(...), x_exam_token: Optional[str] = Header(default=None, alias="X-Exam-Token")):
    try:
        session, _ = require_exam_session(x_exam_token, allow_locked=True, require_verified=True)
        msv = session["msv"]
        exam_id = session["class_id"]
        fname = f"ERR_{msv}_{int(time.time())}.jpg"
        with open(os.path.join("server_evidence", fname), "wb") as f:
            f.write(await file.read())
        db = get_db_connection(); cur = db.cursor(dictionary=True)
        cur.execute("INSERT INTO violations (msv, exam_id, time_detected, error_type, evidence_path) VALUES (%s,%s,%s,%s,%s)", (msv, exam_id, datetime.now(), error_type, fname))
        violation_id = int(cur.lastrowid)
        max_warnings = get_max_warnings(cur)
        db.commit(); db.close()
        session["session_token"] = x_exam_token
        task_id = enqueue_yolo_world_task(
            session=session,
            violation_id=violation_id,
            evidence_path=fname,
            trigger_type=str(error_type),
            extra_meta={"error_type": str(error_type), "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        )
        refresh_session_warning_state(session)
        session["max_warnings"] = max_warnings
        session_warnings_count = int(session.get("session_warning_count", 0))
        historical_warning_count = int(session.get("historical_warning_count", 0))
        total_warnings_count = historical_warning_count + session_warnings_count
        review_required = bool(session.get("warning_review_required", False))
        return {
            "status": "success",
            "decision": "pending_proc_filter",
            "violation_id": violation_id,
            "warnings_count": session_warnings_count,
            "session_warning_count": session_warnings_count,
            "historical_warning_count": historical_warning_count,
            "total_warning_count": total_warnings_count,
            "max_warnings": max_warnings,
            "locked": bool(session.get("warning_locked", False)),
            "review_required": review_required,
            "message": session.get("warning_message") if review_required or session.get("warning_locked") else None,
            "yolo_world_task_id": task_id,
        }
    except HTTPException as exc:
        raise exc
    except Exception as exc:
        logger.exception("Violation upload failed")
        return {"status": "error", "message": str(exc)}


@app.post("/api/student/upload_violation_clip")
async def upload_violation_clip(
    violation_id: str = Form(...),
    event_started_at: str = Form(...),
    event_ended_at: str = Form(...),
    clip_file: UploadFile = File(...),
    thumbnail_file: Optional[UploadFile] = File(default=None),
    x_exam_token: Optional[str] = Header(default=None, alias="X-Exam-Token"),
):
    """Nhận clip sự kiện từ client và liên kết với violation_id đã có."""
    try:
        session, _ = require_exam_session(x_exam_token, allow_locked=True, require_verified=True)
        msv = session["msv"]

        try:
            vid_int = int(violation_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="violation_id must be an integer")

        try:
            started_ts = float(event_started_at)
            ended_ts = float(event_ended_at)
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="event timestamps must be numeric")

        duration_sec = round(max(0.0, ended_ts - started_ts), 2)
        started_dt = datetime.fromtimestamp(started_ts)
        ended_dt = datetime.fromtimestamp(ended_ts)

        # Xác minh violation_id thuộc về sinh viên này (security check)
        db = get_db_connection()
        try:
            cur = db.cursor(dictionary=True)
            cur.execute(
                "SELECT violation_id, msv FROM violations WHERE violation_id = %s",
                (vid_int,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="violation_id not found")
            if str(row["msv"]) != str(msv):
                raise HTTPException(status_code=403, detail="violation_id does not belong to this student")

            # Lưu clip
            ts_tag = int(time.time())
            clip_filename = f"clips/CLIP_{msv}_{ts_tag}.avi"
            clip_path_full = os.path.join("server_evidence", clip_filename)
            clip_bytes = await clip_file.read()
            if not clip_bytes:
                raise HTTPException(status_code=400, detail="Empty clip file")
            with open(clip_path_full, "wb") as fh:
                fh.write(clip_bytes)

            # Lưu thumbnail nếu có
            thumb_filename = None
            if thumbnail_file:
                thumb_bytes = await thumbnail_file.read()
                if thumb_bytes:
                    thumb_filename = f"THUMB_{msv}_{ts_tag}.jpg"
                    with open(os.path.join("server_evidence", thumb_filename), "wb") as fh:
                        fh.write(thumb_bytes)

            # Cập nhật bản ghi violation
            cur.execute(
                """
                UPDATE violations
                SET clip_path = %s,
                    thumbnail_path = %s,
                    event_started_at = %s,
                    event_ended_at = %s,
                    duration_seconds = %s
                WHERE violation_id = %s AND msv = %s
                """,
                (clip_filename, thumb_filename, started_dt, ended_dt, duration_sec, vid_int, msv),
            )
            db.commit()
        finally:
            db.close()

        logger.info("Event clip saved: %s (%.1fs) for violation_id=%s msv=%s", clip_filename, duration_sec, vid_int, msv)
        return {
            "status": "success",
            "clip_path": clip_filename,
            "thumbnail_path": thumb_filename,
            "duration_seconds": duration_sec,
        }
    except HTTPException as exc:
        raise exc
    except Exception as exc:
        logger.exception("Clip upload failed")
        return {"status": "error", "message": str(exc)}


@app.post("/api/monitor/student/earpiece_suspect")
async def submit_earpiece_suspect(
    tier1_conf: str = Form(...),
    file: UploadFile = File(...),
    x_exam_token: Optional[str] = Header(default=None, alias="X-Exam-Token"),
):
    """Nhận crop nghi vấn tai nghe từ Tầng 1 và đẩy vào pipeline quyết định Tầng 2 phía server."""
    session, _ = require_exam_session(x_exam_token, allow_locked=False, require_verified=True)
    msv = session["msv"]
    exam_id = session["class_id"]
    try:
        conf_val = float(tier1_conf)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="tier1_conf must be a float")
    fname = f"ERR_EARPIECE_{msv}_{int(time.time())}.jpg"
    save_path = os.path.join("server_evidence", fname)
    with open(save_path, "wb") as f:
        f.write(await file.read())
    db = get_db_connection()
    try:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO violations (msv, exam_id, time_detected, error_type, evidence_path) VALUES (%s,%s,%s,%s,%s)",
            (msv, exam_id, datetime.now(), "Su dung tai nghe", fname),
        )
        violation_id = int(cur.lastrowid)
        max_warnings = get_max_warnings(cur)
        db.commit()
    finally:
        db.close()

    session["session_token"] = x_exam_token
    task_id = enqueue_yolo_world_task(
        session=session,
        violation_id=violation_id,
        evidence_path=fname,
        trigger_type="Su dung tai nghe",
        extra_meta={
            "error_type": "Su dung tai nghe",
            "source": "tier1_earpiece_suspect",
            "tier1_conf": round(conf_val, 4),
            "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )
    refresh_session_warning_state(session)
    session["max_warnings"] = max_warnings
    session_warnings_count = int(session.get("session_warning_count", 0))
    historical_warning_count = int(session.get("historical_warning_count", 0))
    total_warnings_count = historical_warning_count + session_warnings_count
    review_required = bool(session.get("warning_review_required", False))

    logger.info("Earpiece suspect forwarded to server-tier2: msv=%s conf=%.2f task=%s", msv, conf_val, task_id)
    return {
        "status": "success",
        "decision": "pending_proc_filter",
        "violation_id": violation_id,
        "warnings_count": session_warnings_count,
        "session_warning_count": session_warnings_count,
        "historical_warning_count": historical_warning_count,
        "total_warning_count": total_warnings_count,
        "max_warnings": max_warnings,
        "locked": bool(session.get("warning_locked", False)),
        "review_required": review_required,
        "message": session.get("warning_message") if review_required or session.get("warning_locked") else None,
        "yolo_world_task_id": task_id,
    }


@app.get("/")
def health_check(): return {"status": "Online"}
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)