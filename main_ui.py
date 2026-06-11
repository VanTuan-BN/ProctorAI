import sys, time, requests, cv2, os, json, logging, threading, collections, tempfile, uuid
from multiprocessing import shared_memory
import numpy as np
from queue import Empty, Full, Queue as ThreadQueue
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
import core_ai as ai 
from face_verifier import extract_face_roi
from ui_branding import build_login_logo_label
from ui_theme import apply_theme, set_page_margins
from ui_components import AppToolbar, EmptyState, EnterpriseDialog
from password_recovery_dialog import PasswordRecoveryDialog

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

class ExamMonitorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("S-MONITOR: HỆ THỐNG THI TRỰC TUYẾN")
        self.setMinimumSize(1280, 800)
        apply_theme(self, role="student")
        
        self.api_url = "http://127.0.0.1:8000"
        self.msv, self.exam_id, self.full_name = None, None, ""
        self.class_name = ""
        self.session_token = None
        self.verify_ok = False
        self.ai_started = False
        self.ai_process = None
        self.camera_thread = None
        self.quiz_loaded = False
        self.room_locked = False
        self.face_threshold = 0.75
        self.max_warnings = 5
        self.enable_attention_monitor = False
        self.verify_camera = None
        self.verify_preview_frames = []
        self.selected_verify_frames = []
        self.face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        self.identity_dict = {}
        self.identity_votes = {}
        self.pending_identity_tracks = set()
        self.track_first_seen = {}
        self.last_track_seen = {}
        self.last_track_boxes = {}
        self.lost_identity_tracks = {}
        self.track_generation_counters = {}
        self.track_missing_since = {}
        self.current_frame_id = 0
        self.alert_max_frame_lag = 12
        self.alert_box_iou_threshold = 0.12
        self.alert_box_distance_threshold = 140.0
        self.track_generation_reset_seconds = 0.8
        self.question_widgets = {} # Lưu đáp án
        self.question_cards = {}
        self.question_filter_mode = "all"
        self.violation_last_sent = {}
        self.behavior_counters = {"multiple_people": 0, "away": 0, "phone": 0, "earpiece": 0}
        self.behavior_thresholds = {"multiple_people": 3, "away": 5, "phone": 3, "earpiece": 2}
        self.violation_scores = {"phone": 0.0, "multiple_people": 0.0, "away": 0.0, "intruder": 0.0, "earpiece": 0.0}
        self.violation_thresholds = {"phone": 1.6, "multiple_people": 1.8, "away": 2.1, "intruder": 1.5, "earpiece": 1.4}
        self.violation_decay = 0.72
        self.identity_probe_limit = 5
        self.identity_recent_window_size = 5
        self.identity_burst_frame_count = 5
        self.identity_vote_target = 2
        self.identity_intruder_vote_target = 3
        self.identity_uncertainty_margin = 0.06
        self.identity_near_match_margin = 0.03
        self.identity_top2_mean_margin = 0.015
        self.identity_top1_margin = 0.01
        self.identity_quality_min_face_area_ratio = 0.012
        self.identity_quality_max_abs_yaw = 26.0
        self.identity_quality_max_abs_pitch = 18.0
        self.identity_periodic_probe_seconds = 60.0
        self.identity_suspicious_reprobe_seconds = 20.0
        self.identity_suspicion_window_seconds = 90.0
        self.identity_face_presence_grace_seconds = 1.2
        self.primary_face_last_seen_at = 0.0
        self.identity_face_recently_visible = False
        self.identity_burst_remaining = {}
        self.track_ttl_seconds = 4.0
        self.handover_iou_threshold = 0.38
        self.handover_distance_threshold = 120.0
        self.min_person_area_ratio = 0.018
        self.min_secondary_person_ratio = 0.20
        self.min_phone_area_ratio = 0.0015
        self.max_phone_area_ratio = 0.07
        self.phone_min_aspect_ratio = 0.22
        self.phone_max_aspect_ratio = 4.4
        self.phone_min_confidence = 0.36
        self.phone_overlap_iou = 0.01
        self.phone_near_distance_ratio = 0.55
        self.phone_signal_threshold = 0.46
        self.phone_require_primary_person = False
        self.phone_earpiece_arbitration_margin = 0.08
        self.phone_min_streak_frames = 3
        self.phone_detection_streak = 0
        self.earpiece_head_height_ratio = 0.34
        self.earpiece_head_width_margin_ratio = 0.20
        self.earpiece_ear_band_ratio = 0.22
        self.earpiece_head_gate_iou = 0.02
        self.earpiece_head_gate_distance_ratio = 0.45
        self.multiple_people_stable_seconds = 0.50
        self.pose_ema_alpha = 0.35
        self.smoothed_pitch = 0.0
        self.smoothed_yaw = 0.0
        self.pose_reliable = False
        self.head_pose_audit = False
        self.identity_startup_grace_seconds = 3.0
        self.track_identity_delay_seconds = 0.8
        self.identity_grace_until = 0.0
        self.identity_last_probe_at = {}
        self.identity_suspicious_tracks = {}
        self.question_total = 0
        self.exam_started_at = None
        self.monitor_state = {}
        self.monitor_state_lock = threading.Lock()  # Fix #12: Thread-safe monitor_state
        self.monitoring_active = True  # Fix #13: Track monitoring status
        self.monitor_status_text = "Đang giám sát nền."
        self.monitor_last_event = "Không có sự kiện đáng chú ý."
        self.monitor_preview_frame = None
        self.latest_camera_frame = None
        self.last_monitor_frame_capture = 0.0
        self.last_monitor_publish_at = 0.0
        self.monitor_publish_interval = 3.0
        self.monitor_request_inflight = False
        self.snapshot_upload_cooldowns = {
            "identity": 12.0,
            "phone": 12.0,
            "multiple_people": 15.0,
            "high_risk": 20.0,
            "proctor_request": 2.0,
        }
        self.last_snapshot_uploaded_at = {}
        self.snapshot_request_pending = False
        self.local_queue_dir = os.path.join("server_submissions", "offline_queue")
        os.makedirs(self.local_queue_dir, exist_ok=True)
        self.network_online = True
        self.offline_notice_last_shown = 0.0
        self._init_temporal_policy_state()

        # --- Frame ring buffer cho event clip (Phương án 1) ---
        self.frame_ring_buffer: collections.deque = collections.deque()
        self.frame_ring_max_seconds: float = 8.0          # giữ 8 s pre-roll tối đa
        self.frame_ring_lock = threading.Lock()
        self.clip_upload_inflight: set = set()            # track violation_type đang upload clip

        self.shared_frame_shape = ai.CAMERA_FRAME_SHAPE
        self.shared_frame_slots = ai.SHARED_FRAME_SLOTS
        self.shared_frame_size = int(np.prod(self.shared_frame_shape))
        self.shared_frame_ids = ai.mp.Array('i', [-1] * self.shared_frame_slots, lock=False)
        self.shared_frame_shm = shared_memory.SharedMemory(create=True, size=self.shared_frame_slots * self.shared_frame_size)
        self.shared_frame_view = np.ndarray((self.shared_frame_slots, *self.shared_frame_shape), dtype=np.uint8, buffer=self.shared_frame_shm.buf)

        self.frame_buffer_q = ai.mp.Queue(maxsize=10)
        self.frame_q = ai.mp.Queue(maxsize=2); self.tracker_q = ai.mp.Queue(maxsize=2)
        self.crop_q = ThreadQueue(maxsize=5)
        self.alert_q = ThreadQueue()
        self.init_q = ThreadQueue()

        self.init_ui()
        self.timer = QTimer(); self.timer.timeout.connect(self.update_ui)
        self.sync_timer = QTimer(); self.sync_timer.timeout.connect(self.sync_with_server)
        self.verify_preview_timer = QTimer(); self.verify_preview_timer.timeout.connect(self.update_verify_preview)
        self.exam_clock_timer = QTimer(); self.exam_clock_timer.timeout.connect(self._update_exam_timer)
        self._hint_hide_timer = QTimer(self)
        self._hint_hide_timer.setSingleShot(True)
        self._hint_hide_timer.timeout.connect(self._hide_hint_banner)

        # --- Dual-stream display: raw camera rendered independently of AI pipeline ---
        self.display_q = ThreadQueue(maxsize=2)
        self._last_overlay_boxes = []  # [(box_coords_list, label, color), ...]
        self.display_timer = QTimer()
        self.display_timer.timeout.connect(self._update_display)

    def _init_temporal_policy_state(self):
        self.temporal_policies = {
            "phone": {"continuous_seconds": 1.5, "cumulative_seconds": 2.0, "window_seconds": 5.0},
            "multiple_people": {"continuous_seconds": 0.7, "cumulative_seconds": 1.1, "window_seconds": 3.0},
        }
        self.temporal_presence_windows = {name: [] for name in self.temporal_policies}
        self.temporal_presence_active_since = {name: None for name in self.temporal_policies}
        self.intruder_policy = {"mismatch_votes": 3, "window_seconds": 10.0}
        self.intruder_probe_timestamps = {}
        self.earpiece_tier1_policy = {"min_hits": 2, "window_seconds": 1.0, "forward_cooldown": 0.75}
        self.earpiece_tier1_timestamps = []
        self._last_earpiece_upload_ts = 0.0
        self.secondary_person_first_seen = {}

    def __del__(self):
        """Fix #11: Cleanup SharedMemory to prevent resource leak"""
        try:
            if hasattr(self, 'shared_frame_shm'):
                self.shared_frame_shm.close()
                self.shared_frame_shm.unlink()
        except Exception as e:
            logger.debug("SharedMemory cleanup error: %s", e)

    def _auth_headers(self):
        if not self.session_token:
            return {}
        return {"X-Exam-Token": self.session_token}

    def _load_runtime_config(self):
        if not self.session_token:
            return
        response = requests.get(f"{self.api_url}/api/student/runtime_config", headers=self._auth_headers(), timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "success":
            raise ValueError("Không tải được cấu hình runtime từ server")
        self.face_threshold = float(data.get("face_threshold", 0.75))
        self.max_warnings = int(data.get("max_warnings", 5))

    def _show_dialog(self, title, message, detail=""):
        dlg = EnterpriseDialog(title, message, detail=detail, role="student", parent=self)
        dlg.exec_()

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                self._clear_layout(child_layout)

    def _reset_exam_state(self):
        self.verify_ok = False
        self.quiz_loaded = False
        self.room_locked = False
        self.verify_preview_frames.clear()
        self.selected_verify_frames.clear()
        self.identity_dict.clear()
        self.identity_votes.clear()
        self.pending_identity_tracks.clear()
        self.track_first_seen.clear()
        self.last_track_seen.clear()
        self.last_track_boxes.clear()
        self.lost_identity_tracks.clear()
        self.track_generation_counters.clear()
        self.track_missing_since.clear()
        self.current_frame_id = 0
        self.identity_last_probe_at.clear()
        self.identity_suspicious_tracks.clear()
        self.question_widgets.clear()
        self.question_cards.clear()
        self.question_filter_mode = "all"
        self.violation_last_sent.clear()
        self.behavior_counters = {"multiple_people": 0, "away": 0, "phone": 0, "earpiece": 0}
        self.violation_scores = {"phone": 0.0, "multiple_people": 0.0, "away": 0.0, "intruder": 0.0, "earpiece": 0.0}
        self.secondary_person_first_seen.clear()
        self._init_temporal_policy_state()
        self.phone_detection_streak = 0
        with self.frame_ring_lock:
            self.frame_ring_buffer.clear()
        self.clip_upload_inflight.clear()
        self.smoothed_pitch = 0.0
        self.smoothed_yaw = 0.0
        self.pose_reliable = False
        self.head_pose_audit = False
        self.identity_grace_until = 0.0
        self.primary_face_last_seen_at = 0.0
        self.identity_face_recently_visible = False
        self.identity_burst_remaining.clear()
        self.question_total = 0
        self.exam_started_at = None
        self.monitor_state = {}
        self.monitor_status_text = "Đang giám sát nền."
        self.monitor_last_event = "Không có sự kiện đáng chú ý."
        self.monitoring_active = True
        self.monitor_preview_frame = None
        self.latest_camera_frame = None
        self.last_monitor_frame_capture = 0.0
        self.last_monitor_publish_at = 0.0
        self.monitor_request_inflight = False
        self.last_snapshot_uploaded_at.clear()
        self.snapshot_request_pending = False
        self.network_online = True
        self.offline_notice_last_shown = 0.0
        self.exam_clock_timer.stop()
        self.lbl_verify_status.setText("Xác minh danh tính trước khi vào phòng thi")
        self.lbl_verify_hint.setText("Đặt khuôn mặt ở giữa khung hình, đủ sáng và không đeo vật che mặt.")
        self.lbl_exam_status.setText("AI giám sát đang sẵn sàng.")
        self.lbl_broadcast.show()
        self.lbl_exam_status.show()
        self.btn_enter_exam.setEnabled(False)
        self._clear_layout(self.quiz_inner)
        if hasattr(self, "quiz_empty_state"):
            self.quiz_empty_state.show()
        if hasattr(self, "scroll"):
            self.scroll.hide()
        if hasattr(self, "lbl_verify_live_status"):
            self.lbl_verify_live_status.setText("Chưa mở camera xem trước.")
        if hasattr(self, "verify_view"):
            self.verify_view.clear()
            self.verify_view.setText("Đang chờ mở webcam...")
        if hasattr(self, "verify_selected_labels"):
            for label in self.verify_selected_labels:
                label.clear()
                label.setText("Chưa có ảnh")
        if hasattr(self, "lbl_ai_identity"):
            self.lbl_ai_identity.setText("Danh tính: Chưa bắt đầu")
            self.lbl_ai_camera.setText("Camera: Chưa vào phòng thi")
            self.lbl_ai_attention.setText("Tập trung: Không đánh giá tự động")
            self.lbl_ai_risk.setText("Mức rủi ro hiện tại: Thấp")
            for progress_bar in [self.pb_phone_risk, self.pb_people_risk, self.pb_attention_risk, self.pb_intruder_risk]:
                progress_bar.setValue(0)
        if hasattr(self, "lbl_exam_timer"):
            self.lbl_exam_timer.setText("00:00:00")
        if hasattr(self, "btn_filter_all"):
            self.btn_filter_all.setChecked(True)
        if hasattr(self, "btn_submit_exam"):
            self.btn_submit_exam.setEnabled(False)

    def _collect_selected_answers(self):
        answers = {}
        for question_id, button_group in self.question_widgets.items():
            checked_button = button_group.checkedButton()
            if checked_button is not None:
                answers[str(question_id)] = checked_button.val
        return answers

    def _set_question_filter_mode(self, mode):
        self.question_filter_mode = str(mode)
        self._apply_question_filter()

    def _apply_question_filter(self):
        for question_id, group_box in self.question_cards.items():
            button_group = self.question_widgets.get(question_id)
            checked = button_group.checkedButton() if button_group is not None else None
            if self.question_filter_mode == "answered":
                visible = checked is not None
            elif self.question_filter_mode == "unanswered":
                visible = checked is None
            else:
                visible = True
            group_box.setVisible(visible)

    def _summarize_identity_status(self, active_person_tracks, person_count):
        if person_count <= 0:
            return "Không thấy thí sinh"
        if person_count > 1:
            return "Phát hiện nhiều người"
        if time.time() < self.identity_grace_until:
            return "Cần xác minh thêm"
        statuses = [self.identity_dict.get(track_id) for track_id in active_person_tracks if track_id in self.identity_dict]
        if "KE DOT NHAP" in statuses:
            return "Cảnh báo người lạ"
        if "THI SINH" in statuses and self.identity_face_recently_visible:
            return "Đúng thí sinh"
        if "THI SINH" in statuses and not self.identity_face_recently_visible:
            return "Cần xác minh thêm"
        if any(track_id in self.identity_votes for track_id in active_person_tracks):
            return "Đang đối chiếu danh tính"
        if any(track_id in self.track_first_seen for track_id in active_person_tracks):
            return "Cần xác minh thêm"
        return "Đang tìm khuôn mặt"

    def _set_violation_ratio(self, name, ratio):
        threshold = max(float(self.violation_thresholds.get(name, 1.0)), 0.1)
        self.violation_scores[name] = max(0.0, float(ratio)) * threshold
        return self.violation_scores[name]

    def _update_temporal_presence(self, name, active, now=None):
        now = float(now if now is not None else time.time())
        policy = self.temporal_policies[name]
        active_since = self.temporal_presence_active_since.get(name)

        if active:
            if active_since is None:
                self.temporal_presence_active_since[name] = now
        elif active_since is not None:
            if now > active_since:
                self.temporal_presence_windows[name].append((active_since, now))
            self.temporal_presence_active_since[name] = None

        window_start = now - float(policy["window_seconds"])
        pruned_segments = []
        for start, end in self.temporal_presence_windows.get(name, []):
            if end <= window_start:
                continue
            pruned_segments.append((max(float(start), window_start), float(end)))
        self.temporal_presence_windows[name] = pruned_segments

        active_since = self.temporal_presence_active_since.get(name)
        if active_since is not None and active_since < window_start:
            active_since = window_start
            self.temporal_presence_active_since[name] = active_since

        continuous_seconds = max(0.0, now - active_since) if active_since is not None else 0.0
        cumulative_seconds = sum(max(0.0, end - start) for start, end in pruned_segments)
        if active_since is not None:
            cumulative_seconds += max(0.0, now - active_since)

        continuous_ratio = continuous_seconds / max(float(policy["continuous_seconds"]), 0.001)
        cumulative_ratio = cumulative_seconds / max(float(policy["cumulative_seconds"]), 0.001)
        ratio = max(continuous_ratio, cumulative_ratio)
        self._set_violation_ratio(name, ratio)
        return {
            "continuous_seconds": continuous_seconds,
            "cumulative_seconds": cumulative_seconds,
            "ratio": ratio,
            "ready": continuous_seconds >= float(policy["continuous_seconds"]) or cumulative_seconds >= float(policy["cumulative_seconds"]),
        }

    def _get_recent_intruder_probe_count(self, track_id, now=None):
        now = float(now if now is not None else time.time())
        track_id = int(track_id)
        window_seconds = float(self.intruder_policy["window_seconds"])
        recent = [ts for ts in self.intruder_probe_timestamps.get(track_id, []) if (now - float(ts)) <= window_seconds]
        self.intruder_probe_timestamps[track_id] = recent
        return len(recent)

    def _register_intruder_probe(self, track_id, now=None):
        now = float(now if now is not None else time.time())
        track_id = int(track_id)
        recent = [ts for ts in self.intruder_probe_timestamps.get(track_id, []) if (now - float(ts)) <= float(self.intruder_policy["window_seconds"])]
        recent.append(now)
        self.intruder_probe_timestamps[track_id] = recent
        return len(recent)

    def _update_earpiece_tier1_state(self, suspects, now=None):
        now = float(now if now is not None else time.time())
        policy = self.earpiece_tier1_policy
        if suspects:
            self.earpiece_tier1_timestamps.append(now)
        self.earpiece_tier1_timestamps = [
            ts for ts in self.earpiece_tier1_timestamps if (now - float(ts)) <= float(policy["window_seconds"])
        ]
        hit_count = len(self.earpiece_tier1_timestamps)
        ratio = hit_count / max(float(policy["min_hits"]), 1.0)
        self._set_violation_ratio("earpiece", ratio)
        ready = bool(suspects) and hit_count >= int(policy["min_hits"]) and (now - float(self._last_earpiece_upload_ts)) >= float(policy["forward_cooldown"])
        return {"hit_count": hit_count, "ratio": ratio, "ready": ready}

    def _compute_risk_level(self):
        max_ratio = 0.0
        for name, score in self.violation_scores.items():
            threshold = max(float(self.violation_thresholds.get(name, 1.0)), 0.1)
            max_ratio = max(max_ratio, float(score) / threshold)
        if max_ratio >= 1.0:
            return "Cao", max_ratio
        if max_ratio >= 0.6:
            return "Trung bình", max_ratio
        return "Thấp", max_ratio

    def _update_student_monitor_panel(self, identity_status, person_count, phone_detected, away_detected, earpiece_detected=False):
        # Student UI intentionally hides model outputs during the exam to avoid distraction.
        return

    def _update_monitor_state(self, frame, active_person_tracks, person_count, phone_detected, away_detected, earpiece_detected, pitch, yaw):
        identity_status = self._summarize_identity_status(active_person_tracks, person_count)
        self._update_student_monitor_panel(identity_status, person_count, phone_detected, away_detected, earpiece_detected)
        risk_text, _ = self._compute_risk_level()
        pose_status = "unavailable"
        if self.pose_reliable:
            pose_status = "looking-away" if self.head_pose_audit else "frontal"
        new_state = {
            "status_text": self.monitor_status_text,
            "last_event": self.monitor_last_event,
            "monitoring_active": bool(self.monitoring_active),
            "identity_status": identity_status,
            "people_count": int(person_count),
            "phone_detected": bool(phone_detected),
            "earpiece_detected": bool(earpiece_detected),
            "away_detected": False,
            "pitch": round(float(pitch), 2),
            "yaw": round(float(yaw), 2),
            "pose_reliable": bool(self.pose_reliable),
            "head_pose_audit": bool(self.head_pose_audit),
            "head_pose_status": pose_status,
            "violation_scores": {key: round(float(value), 3) for key, value in self.violation_scores.items()},
            "violation_thresholds": {key: round(float(value), 3) for key, value in self.violation_thresholds.items()},
            "answers": self._collect_selected_answers(),
            "question_total": int(self.question_total),
            "risk_level": risk_text,
        }
        # Keep shared state consistent for background publish thread.
        with self.monitor_state_lock:
            self.monitor_state = new_state
        now = time.time()
        if frame is not None:
            self.latest_camera_frame = frame
        if frame is not None and (now - self.last_monitor_frame_capture) >= 1.5:
            self.monitor_preview_frame = frame.copy()
            self.last_monitor_frame_capture = now

    def _build_monitor_payload(self):
        # Fix #12: Thread-safe read using lock
        with self.monitor_state_lock:
            if not self.monitor_state:
                return None
            return dict(self.monitor_state)

    def _encode_monitor_snapshot(self, frame):
        if frame is None:
            return None
        snapshot_frame = frame
        target_width = 960
        if snapshot_frame.shape[1] > target_width:
            scale = target_width / float(snapshot_frame.shape[1])
            target_height = max(1, int(snapshot_frame.shape[0] * scale))
            snapshot_frame = cv2.resize(snapshot_frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
        success, buffer = cv2.imencode('.jpg', snapshot_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
        if not success:
            return None
        return buffer.tobytes()

    def _snapshot_frame_for_upload(self):
        if self.latest_camera_frame is not None:
            return self.latest_camera_frame
        if self.monitor_preview_frame is not None:
            return self.monitor_preview_frame
        return None

    def _should_upload_snapshot(self, trigger_name, force=False):
        now = time.time()
        cooldown = float(self.snapshot_upload_cooldowns.get(trigger_name, 10.0))
        last_uploaded_at = float(self.last_snapshot_uploaded_at.get(trigger_name, 0.0))
        if not force and (now - last_uploaded_at) < cooldown:
            return False
        self.last_snapshot_uploaded_at[trigger_name] = now
        return True

    def _set_network_status(self, online: bool, reason: str = ""):
        self.network_online = bool(online)
        if self.network_online:
            if not self.monitoring_active:
                self.monitoring_active = True
            self._set_monitor_feedback("Đang giám sát nền.", "Kết nối máy chủ ổn định.")
            return

        self.monitoring_active = False
        now = time.time()
        if (now - self.offline_notice_last_shown) >= 6.0:
            self.offline_notice_last_shown = now
            fallback_msg = "Mạng gián đoạn: hệ thống chuyển sang lưu cục bộ và sẽ đồng bộ lại khi có mạng."
            if reason:
                fallback_msg = f"{fallback_msg} ({reason})"
            self._set_monitor_feedback("Chế độ dự phòng cục bộ", fallback_msg)

    def _queue_offline_job(self, job_type: str, payload: dict, binary_fields: dict = None):
        job_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex}"
        meta = {
            "id": job_id,
            "job_type": str(job_type),
            "created_at": float(time.time()),
            "payload": dict(payload or {}),
            "binary_fields": {},
        }
        binary_fields = binary_fields or {}
        for field_name, field_bytes in binary_fields.items():
            if not field_bytes:
                continue
            bin_name = f"{job_id}_{field_name}.bin"
            bin_path = os.path.join(self.local_queue_dir, bin_name)
            with open(bin_path, "wb") as f:
                f.write(field_bytes)
            meta["binary_fields"][str(field_name)] = bin_name

        meta_path = os.path.join(self.local_queue_dir, f"{job_id}.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)

    def _delete_offline_job(self, meta_path: str, meta: dict):
        for bin_name in (meta.get("binary_fields") or {}).values():
            try:
                os.remove(os.path.join(self.local_queue_dir, str(bin_name)))
            except Exception:
                logger.debug("Could not remove offline queue binary %s", bin_name, exc_info=True)
        try:
            os.remove(meta_path)
        except Exception:
            logger.debug("Could not remove offline queue meta %s", meta_path, exc_info=True)

    def _process_offline_job(self, meta: dict):
        job_type = str(meta.get("job_type") or "")
        payload = dict(meta.get("payload") or {})
        binaries = dict(meta.get("binary_fields") or {})

        if job_type == "snapshot_upload":
            image_name = binaries.get("image")
            if not image_name:
                return True
            image_path = os.path.join(self.local_queue_dir, str(image_name))
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            response = requests.post(
                f"{self.api_url}/api/student/upload_snapshot",
                headers=self._auth_headers(),
                data={
                    "source": str(payload.get("source") or "offline_snapshot"),
                    "reason": str(payload.get("reason") or "Offline queue replay"),
                },
                files={"file": ("snapshot.jpg", image_bytes, "image/jpeg")},
                timeout=10,
            )
            response.raise_for_status()
            body = response.json()
            return body.get("status") == "success"

        if job_type == "monitor_snapshot":
            response = requests.post(
                f"{self.api_url}/api/student/monitor_snapshot",
                headers=self._auth_headers(),
                json=payload,
                timeout=4,
            )
            response.raise_for_status()
            return True

        if job_type == "violation_upload":
            image_name = binaries.get("image")
            if not image_name:
                return True
            image_path = os.path.join(self.local_queue_dir, str(image_name))
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            response = requests.post(
                f"{self.api_url}/upload_violation/",
                headers=self._auth_headers(),
                data={"error_type": str(payload.get("violation_type") or "Vi phạm offline")},
                files={"file": ("violation.jpg", image_bytes, "image/jpeg")},
                timeout=10,
            )
            response.raise_for_status()
            body = response.json()
            return body.get("status") == "success"

        return True

    def _flush_offline_queue(self, max_jobs: int = 3):
        if not self.session_token:
            return
        try:
            entries = [name for name in os.listdir(self.local_queue_dir) if name.endswith(".json")]
            entries.sort()
        except Exception:
            logger.exception("Failed to enumerate offline queue")
            return

        processed = 0
        for file_name in entries:
            if processed >= int(max_jobs):
                break
            meta_path = os.path.join(self.local_queue_dir, file_name)
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                logger.exception("Invalid offline queue meta file: %s", meta_path)
                try:
                    os.remove(meta_path)
                except Exception:
                    pass
                continue

            try:
                ok = self._process_offline_job(meta)
                if ok:
                    self._delete_offline_job(meta_path, meta)
                    processed += 1
                    self._set_network_status(True)
                else:
                    self._set_network_status(False, "đồng bộ lại chưa thành công")
                    break
            except Exception:
                logger.exception("Failed to replay offline queue job")
                self._set_network_status(False, "lỗi khi đồng bộ hàng đợi cục bộ")
                break

    def _upload_snapshot_worker(self, image_bytes, source, reason):
        try:
            response = requests.post(
                f"{self.api_url}/api/student/upload_snapshot",
                headers=self._auth_headers(),
                data={"source": source, "reason": reason},
                files={"file": ("snapshot.jpg", image_bytes, "image/jpeg")},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "success":
                logger.warning("Snapshot upload rejected: %s", payload)
            else:
                self._set_network_status(True)
                self.snapshot_request_pending = False
                if not self.monitoring_active:
                    self.monitoring_active = True
                    self._set_monitor_feedback("Đang giám sát nền.", "Kênh snapshot đã phục hồi.")
        except Exception:
            # Fix #13: Track monitoring failures - update status instead of silent fail
            logger.exception("Failed to upload monitor snapshot for %s", source)
            try:
                self._queue_offline_job(
                    "snapshot_upload",
                    {"source": str(source), "reason": str(reason)},
                    binary_fields={"image": image_bytes},
                )
            except Exception:
                logger.exception("Failed to spool snapshot into offline queue")
            self._set_network_status(False, "không gửi được snapshot")
            logger.warning("Monitoring status marked as inactive due to upload failure")

    def _queue_snapshot_upload(self, trigger_name, frame=None, reason="", force=False):
        if not self.session_token or not self.verify_ok or self.room_locked:
            return
        if not self._should_upload_snapshot(trigger_name, force=force):
            return
        snapshot_frame = frame if frame is not None else self._snapshot_frame_for_upload()
        image_bytes = self._encode_monitor_snapshot(snapshot_frame)
        if not image_bytes:
            return
        threading.Thread(
            target=self._upload_snapshot_worker,
            args=(image_bytes, trigger_name, reason or trigger_name),
            daemon=True,
        ).start()

    def _publish_monitor_snapshot_worker(self, payload):
        try:
            response = requests.post(
                f"{self.api_url}/api/student/monitor_snapshot",
                headers=self._auth_headers(),
                json=payload,
                timeout=4,
            )
            response.raise_for_status()
            self._set_network_status(True)
        except Exception:
            logger.exception("Failed to publish monitor snapshot")
            try:
                self._queue_offline_job("monitor_snapshot", payload)
            except Exception:
                logger.exception("Failed to spool monitor payload into offline queue")
            self._set_network_status(False, "không gửi được dữ liệu giám sát")
        finally:
            self.monitor_request_inflight = False

    def _queue_monitor_snapshot(self):
        if not self.session_token or not self.verify_ok or self.room_locked or self.monitor_request_inflight:
            return
        now = time.time()
        if (now - self.last_monitor_publish_at) < self.monitor_publish_interval:
            return
        payload = self._build_monitor_payload()
        if not payload:
            return
        self.last_monitor_publish_at = now
        self.monitor_request_inflight = True
        threading.Thread(target=self._publish_monitor_snapshot_worker, args=(payload,), daemon=True).start()

    def _track_key(self, track_id, track_generation):
        return (int(track_id), int(track_generation))

    def _current_track_generation(self, track_id):
        return int(self.track_generation_counters.get(int(track_id), 0))

    def _discard_pending_track(self, track_id):
        track_id = int(track_id)
        self.pending_identity_tracks = {key for key in self.pending_identity_tracks if key[0] != track_id}

    def _clear_probe_history(self, track_id):
        track_id = int(track_id)
        self.identity_last_probe_at.pop(track_id, None)
        self.identity_suspicious_tracks.pop(track_id, None)
        self.intruder_probe_timestamps.pop(track_id, None)
        self.identity_burst_remaining.pop(track_id, None)

    def _mark_track_identity_suspicious(self, track_id):
        self.identity_suspicious_tracks[int(track_id)] = time.time()

    def _is_track_identity_suspicious(self, track_id):
        track_id = int(track_id)
        marked_at = self.identity_suspicious_tracks.get(track_id)
        if marked_at is None:
            return False
        if time.time() - marked_at > self.identity_suspicion_window_seconds:
            self.identity_suspicious_tracks.pop(track_id, None)
            return False
        return True

    def _set_monitor_feedback(self, status_text=None, last_event=None):
        if status_text is not None:
            self.monitor_status_text = str(status_text)
        if last_event is not None:
            self.monitor_last_event = str(last_event)

    def _show_soft_hint(self, level: int, message: str):
        """Hiển thị nhắc nhở nhẹ (level=1, nền vàng) hoặc nhắc nhở mạnh (level=2, nền cam).
        Tự động ẩn sau 4 giây nếu hành vi dừng lại."""
        if level == 2:
            style = (
                "background:#FFE0B2; color:#7B3F00; border:1px solid #FF9800;"
                "border-radius:6px; padding:6px 8px; font-size:12px; font-weight:bold;"
            )
        else:
            style = (
                "background:#FFF9C4; color:#4A4000; border:1px solid #F9A825;"
                "border-radius:6px; padding:6px 8px; font-size:12px;"
            )
        self.lbl_hint_banner.setStyleSheet(style)
        self.lbl_hint_banner.setText(message)
        self.lbl_hint_banner.show()
        self._hint_hide_timer.start(4000)

    def _hide_hint_banner(self):
        self.lbl_hint_banner.hide()
        self.lbl_hint_banner.setText("")

    def _queue_crop(self, crop, track_id, frame_id, track_generation, box):
        track_key = self._track_key(track_id, track_generation)
        if track_key in self.pending_identity_tracks:
            return False
        try:
            self.pending_identity_tracks.add(track_key)
            self.identity_last_probe_at[int(track_id)] = time.time()
            self.crop_q.put_nowait(
                {
                    "crop": crop,
                    "track_id": int(track_id),
                    "student_id": self.msv,
                    "frame_id": int(frame_id),
                    "track_generation": int(track_generation),
                    "box": tuple(int(v) for v in box),
                    "is_face_roi": True,
                }
            )
            return True
        except Full:
            self.pending_identity_tracks.discard(track_key)
            logger.debug("Crop queue is full; dropping frame for track %s", track_id)
            return False

    def _start_identity_burst(self, track_id, frame_count=None):
        track_id = int(track_id)
        burst_frames = int(frame_count or self.identity_burst_frame_count)
        self.identity_burst_remaining[track_id] = max(self.identity_burst_remaining.get(track_id, 0), burst_frames)

    def _consume_identity_burst(self, track_id):
        track_id = int(track_id)
        remaining = int(self.identity_burst_remaining.get(track_id, 0))
        if remaining <= 0:
            self.identity_burst_remaining.pop(track_id, None)
            return 0
        remaining -= 1
        if remaining <= 0:
            self.identity_burst_remaining.pop(track_id, None)
            return 0
        self.identity_burst_remaining[track_id] = remaining
        return remaining

    def _evaluate_identity_quality_gate(self, frame, person_box, person_count, pitch=None, yaw=None, allow_full_frame_fallback=False):
        if frame is None or person_box is None or int(person_count) != 1:
            return {"passed": False, "reason": "person_count", "face_data": None}
        if self.pose_reliable:
            probe_pitch = float(self.smoothed_pitch if pitch is None else pitch)
            probe_yaw = float(self.smoothed_yaw if yaw is None else yaw)
            if abs(probe_yaw) > float(self.identity_quality_max_abs_yaw) or abs(probe_pitch) > float(self.identity_quality_max_abs_pitch):
                return {"passed": False, "reason": "pose", "face_data": None}

        face_data = extract_face_roi(frame, person_box=person_box)
        if face_data is None and allow_full_frame_fallback:
            face_data = extract_face_roi(frame)
        if not face_data:
            return {"passed": False, "reason": "face_missing", "face_data": None}

        face_box = face_data.get("box") or ()
        if len(face_box) != 4:
            return {"passed": False, "reason": "face_box", "face_data": None}
        face_area = self._box_area(face_box)
        frame_area = float(frame.shape[0] * frame.shape[1])
        if face_area < (frame_area * float(self.identity_quality_min_face_area_ratio)):
            return {"passed": False, "reason": "face_small", "face_data": face_data}
        return {"passed": True, "reason": "ok", "face_data": face_data}

    def _extract_identity_crop(self, frame, box, allow_full_frame_fallback=False):
        face_data = extract_face_roi(frame, person_box=box)
        if face_data is None and allow_full_frame_fallback:
            face_data = extract_face_roi(frame)
        if not face_data:
            return None
        return face_data["face"]

    def _should_probe_track_identity(self, track_id):
        now = time.time()
        if now < self.identity_grace_until:
            return False
        first_seen = self.track_first_seen.get(int(track_id), now)
        return (now - first_seen) >= self.track_identity_delay_seconds

    def _should_probe_track_identity_periodically(self, track_id, suspicious=False):
        track_id = int(track_id)
        last_probe = self.identity_last_probe_at.get(track_id)
        if last_probe is None:
            return self._should_probe_track_identity(track_id)
        interval = self.identity_suspicious_reprobe_seconds if suspicious else self.identity_periodic_probe_seconds
        return (time.time() - last_probe) >= interval

    def _format_elapsed_time(self, elapsed_seconds):
        elapsed_seconds = max(0, int(elapsed_seconds))
        hours, remainder = divmod(elapsed_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _update_exam_timer(self):
        if not hasattr(self, "lbl_exam_timer"):
            return
        if self.exam_started_at is None:
            self.lbl_exam_timer.setText("00:00:00")
            return
        self.lbl_exam_timer.setText(self._format_elapsed_time(time.time() - self.exam_started_at))

    def _refresh_track_generations(self, active_track_ids):
        now = time.time()
        active_track_ids = {int(track_id) for track_id in active_track_ids}
        for track_id in active_track_ids:
            missing_since = self.track_missing_since.pop(track_id, None)
            if track_id not in self.track_generation_counters:
                self.track_generation_counters[track_id] = 1
                self.track_first_seen[track_id] = now
            elif missing_since is not None and (now - missing_since) > self.track_generation_reset_seconds:
                self.track_generation_counters[track_id] += 1
                self.track_first_seen[track_id] = now
                self.identity_dict.pop(track_id, None)
                self.identity_votes.pop(track_id, None)
                self._discard_pending_track(track_id)
                self._clear_probe_history(track_id)

        for track_id in list(self.track_generation_counters.keys()):
            if track_id in active_track_ids:
                continue
            self.track_missing_since.setdefault(track_id, now)

    def _is_identity_alert_current(self, result, tracked_lookup):
        track_id = int(result.get("id", -1))
        track_generation = int(result.get("track_generation", 0))
        if track_id < 0 or self._current_track_generation(track_id) != track_generation:
            return False

        source_frame_id = int(result.get("frame_id", 0))
        if self.current_frame_id and source_frame_id and (self.current_frame_id - source_frame_id) > self.alert_max_frame_lag:
            return False

        tracked_object = tracked_lookup.get(track_id)
        if not tracked_object:
            return False

        source_box = result.get("box")
        if not source_box:
            return True

        current_box = tracked_object.get("box")
        iou = self._compute_iou(current_box, source_box)
        distance = self._box_center_distance(current_box, source_box)
        return iou >= self.alert_box_iou_threshold or distance <= self.alert_box_distance_threshold

    def _update_behavior_counter(self, name, active):
        if active:
            self.behavior_counters[name] += 1
        else:
            self.behavior_counters[name] = 0
        return self.behavior_counters[name] >= self.behavior_thresholds[name]

    def _compute_iou(self, box_a, box_b):
        ax1, ay1, ax2, ay2 = [int(v) for v in box_a]
        bx1, by1, bx2, by2 = [int(v) for v in box_b]
        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)
        inter_w = max(0, inter_x2 - inter_x1)
        inter_h = max(0, inter_y2 - inter_y1)
        inter_area = inter_w * inter_h
        if inter_area <= 0:
            return 0.0

        area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
        area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
        union = area_a + area_b - inter_area
        return float(inter_area / union) if union > 0 else 0.0

    def _box_center_distance(self, box_a, box_b):
        ax1, ay1, ax2, ay2 = [int(v) for v in box_a]
        bx1, by1, bx2, by2 = [int(v) for v in box_b]
        center_a = ((ax1 + ax2) / 2.0, (ay1 + ay2) / 2.0)
        center_b = ((bx1 + bx2) / 2.0, (by1 + by2) / 2.0)
        return float(np.hypot(center_a[0] - center_b[0], center_a[1] - center_b[1]))

    def _box_area(self, box):
        x1, y1, x2, y2 = [int(v) for v in box]
        return float(max(0, x2 - x1) * max(0, y2 - y1))

    def _box_aspect_ratio(self, box):
        x1, y1, x2, y2 = [int(v) for v in box]
        width = float(max(1, x2 - x1))
        height = float(max(1, y2 - y1))
        return width / height

    def _get_valid_people_track_ids(self, tracked_lookup, primary_track_id, frame_shape):
        if not tracked_lookup:
            return []
        frame_area = float(frame_shape[0] * frame_shape[1])
        primary_box = tracked_lookup.get(primary_track_id, {}).get("box") if primary_track_id is not None else None
        primary_area = self._box_area(primary_box) if primary_box is not None else 0.0
        valid_track_ids = []
        for track_id, tracked_object in tracked_lookup.items():
            box = tracked_object.get("box")
            area = self._box_area(box)
            if area < frame_area * self.min_person_area_ratio:
                continue
            if primary_track_id is None or track_id == primary_track_id:
                valid_track_ids.append(track_id)
                continue
            if primary_area > 0 and area < primary_area * self.min_secondary_person_ratio:
                continue
            valid_track_ids.append(track_id)
        return valid_track_ids

    def _update_stable_secondary_people(self, valid_track_ids, primary_track_id, now=None):
        now = float(now if now is not None else time.time())
        secondary_track_ids = [int(track_id) for track_id in valid_track_ids if track_id != primary_track_id]
        current_secondary = set(secondary_track_ids)
        for track_id in list(self.secondary_person_first_seen.keys()):
            if track_id not in current_secondary:
                self.secondary_person_first_seen.pop(track_id, None)
        for track_id in secondary_track_ids:
            self.secondary_person_first_seen.setdefault(track_id, now)
        stable_secondary = [
            track_id
            for track_id in secondary_track_ids
            if (now - float(self.secondary_person_first_seen.get(track_id, now))) >= float(self.multiple_people_stable_seconds)
        ]
        return stable_secondary

    def _estimate_head_regions(self, primary_box, frame_shape):
        if primary_box is None:
            return None
        frame_h, frame_w = frame_shape[:2]
        x1, y1, x2, y2 = [int(v) for v in primary_box]
        width = max(1, x2 - x1)
        height = max(1, y2 - y1)

        head_x1 = max(0, int(x1 + (width * self.earpiece_head_width_margin_ratio)))
        head_x2 = min(frame_w, int(x2 - (width * self.earpiece_head_width_margin_ratio)))
        head_y1 = max(0, y1)
        head_y2 = min(frame_h, int(y1 + (height * self.earpiece_head_height_ratio)))
        if head_x2 <= head_x1 or head_y2 <= head_y1:
            return None

        head_box = (head_x1, head_y1, head_x2, head_y2)
        head_width = max(1, head_x2 - head_x1)
        head_height = max(1, head_y2 - head_y1)
        ear_band = max(6, int(head_width * self.earpiece_ear_band_ratio))
        ear_top = max(0, int(head_y1 + (head_height * 0.18)))
        ear_bottom = min(frame_h, int(head_y1 + (head_height * 0.85)))
        left_ear_box = (
            max(0, head_x1 - ear_band // 2),
            ear_top,
            min(frame_w, head_x1 + ear_band),
            ear_bottom,
        )
        right_ear_box = (
            max(0, head_x2 - ear_band),
            ear_top,
            min(frame_w, head_x2 + ear_band // 2),
            ear_bottom,
        )
        return {"head_box": head_box, "ear_boxes": [left_ear_box, right_ear_box]}

    def _filter_earpiece_suspects_by_head_region(self, suspects, primary_box, frame_shape):
        if not suspects:
            return [], {"raw_count": 0, "head_hits": 0}
        head_regions = self._estimate_head_regions(primary_box, frame_shape)
        if head_regions is None:
            return [], {"raw_count": len(suspects), "head_hits": 0}

        head_box = head_regions["head_box"]
        head_width = max(1, int(head_box[2] - head_box[0]))
        head_height = max(1, int(head_box[3] - head_box[1]))
        max_distance = max(16.0, float(np.hypot(head_width, head_height)) * self.earpiece_head_gate_distance_ratio)
        filtered = []
        for suspect in suspects:
            suspect_box = suspect.get("box") or []
            if len(suspect_box) != 4:
                continue
            candidate_box = tuple(int(v) for v in suspect_box)
            near_ear = False
            for ear_box in head_regions["ear_boxes"]:
                if self._compute_iou(candidate_box, ear_box) >= self.earpiece_head_gate_iou:
                    near_ear = True
                    break
                if self._box_center_distance(candidate_box, ear_box) <= max_distance:
                    near_ear = True
                    break
            if near_ear:
                filtered.append(suspect)
        return filtered, {"raw_count": len(suspects), "head_hits": len(filtered), "head_box": head_box}

    def _update_phone_streak(self, active):
        if active:
            self.phone_detection_streak = min(self.phone_detection_streak + 1, self.phone_min_streak_frames * 4)
        else:
            self.phone_detection_streak = max(0, self.phone_detection_streak - 1)
        return {
            "streak": int(self.phone_detection_streak),
            "confirmed": bool(self.phone_detection_streak >= int(self.phone_min_streak_frames)),
        }

    def _select_primary_person_track(self, tracked_lookup, frame_shape):
        if not tracked_lookup:
            return None
        frame_area = float(frame_shape[0] * frame_shape[1])
        best_track_id = None
        best_score = -1.0
        for track_id, tracked_object in tracked_lookup.items():
            box = tracked_object.get("box")
            area = self._box_area(box)
            if area < frame_area * self.min_person_area_ratio:
                continue
            status = self.identity_dict.get(track_id)
            status_bonus = 1.3 if status == "THI SINH" else 1.1 if status == "CAN XAC MINH" else 1.0
            score = area * status_bonus
            if score > best_score:
                best_score = score
                best_track_id = track_id
        return best_track_id

    def _count_valid_people(self, tracked_lookup, primary_track_id, frame_shape):
        return len(self._get_valid_people_track_ids(tracked_lookup, primary_track_id, frame_shape))

    def _compute_phone_signal(self, phone_boxes, primary_box, frame_shape):
        if not phone_boxes:
            return 0.0
        if primary_box is None:
            if self.phone_require_primary_person:
                return 0.0
            primary_box = (0, 0, frame_shape[1], frame_shape[0])
        frame_diag = float(np.hypot(frame_shape[1], frame_shape[0]))
        primary_diag = float(np.hypot(primary_box[2] - primary_box[0], primary_box[3] - primary_box[1]))
        max_distance = max(frame_diag * 0.18, primary_diag * self.phone_near_distance_ratio)
        frame_area = float(frame_shape[0] * frame_shape[1])
        best_signal = 0.0
        for phone_box, phone_confidence in phone_boxes:
            if float(phone_confidence) < self.phone_min_confidence:
                continue
            phone_area = self._box_area(phone_box)
            if phone_area < frame_area * self.min_phone_area_ratio:
                continue
            if phone_area > frame_area * self.max_phone_area_ratio:
                continue
            aspect_ratio = self._box_aspect_ratio(phone_box)
            if aspect_ratio < self.phone_min_aspect_ratio or aspect_ratio > self.phone_max_aspect_ratio:
                continue
            distance = self._box_center_distance(phone_box, primary_box)
            overlap = self._compute_iou(phone_box, primary_box)
            if distance > max_distance and overlap < self.phone_overlap_iou:
                continue
            proximity_score = max(0.0, 1.0 - (distance / max(max_distance, 1.0)))
            overlap_score = min(1.0, overlap * 10.0)
            size_score = min(1.0, phone_area / max(frame_area * 0.008, 1.0))
            signal = (float(phone_confidence) * 0.55) + (proximity_score * 0.30) + (max(overlap_score, size_score) * 0.15)
            best_signal = max(best_signal, signal)
        return min(1.0, best_signal)

    def _update_pose_smoothing(self, pitch, yaw, has_valid_face):
        if has_valid_face:
            self.smoothed_pitch = (self.smoothed_pitch * (1.0 - self.pose_ema_alpha)) + (float(pitch) * self.pose_ema_alpha)
            self.smoothed_yaw = (self.smoothed_yaw * (1.0 - self.pose_ema_alpha)) + (float(yaw) * self.pose_ema_alpha)
        else:
            self.smoothed_pitch *= (1.0 - self.pose_ema_alpha)
            self.smoothed_yaw *= (1.0 - self.pose_ema_alpha)
        return self.smoothed_pitch, self.smoothed_yaw

    def _attempt_track_handover(self, track_id, box):
        best_match = None
        best_score = 0.0
        now = time.time()
        for old_track_id, state in list(self.lost_identity_tracks.items()):
            if now - state["last_seen"] > self.track_ttl_seconds:
                self.lost_identity_tracks.pop(old_track_id, None)
                continue
            iou = self._compute_iou(state["box"], box)
            distance = self._box_center_distance(state["box"], box)
            score = iou - (distance / max(self.handover_distance_threshold * 4.0, 1.0))
            if iou >= self.handover_iou_threshold or distance <= self.handover_distance_threshold:
                if score > best_score:
                    best_score = score
                    best_match = old_track_id

        if best_match is None:
            return False

        state = self.lost_identity_tracks.pop(best_match)
        recovered_status = state.get("status")
        # Không chuyển trực tiếp trạng thái THI SINH sang track mới nếu chưa có bằng chứng khuôn mặt,
        # tránh trường hợp không thấy mặt vẫn giữ danh tính cũ.
        if recovered_status == "THI SINH":
            self.identity_dict.pop(track_id, None)
            self.identity_votes.pop(track_id, None)
            self.track_first_seen[track_id] = now
        else:
            if "status" in state and state.get("status") is not None:
                self.identity_dict[track_id] = state["status"]
            if "votes" in state:
                self.identity_votes[track_id] = dict(state["votes"])
        self._mark_track_identity_suspicious(track_id)
        self.last_track_seen[track_id] = now
        self.last_track_boxes[track_id] = tuple(int(v) for v in box)
        self.identity_dict.pop(best_match, None)
        self.identity_votes.pop(best_match, None)
        self.last_track_seen.pop(best_match, None)
        self.last_track_boxes.pop(best_match, None)
        self._discard_pending_track(best_match)
        self._clear_probe_history(best_match)
        logger.info("Handed over identity state from track %s to %s", best_match, track_id)
        return True

    def _score_violation_signal(self, name, signal):
        clamped_signal = max(0.0, min(float(signal), 1.0))
        self.violation_scores[name] = (self.violation_scores[name] * self.violation_decay) + clamped_signal
        return self.violation_scores[name]

    def _cool_down_violation_signal(self, name):
        self.violation_scores[name] *= self.violation_decay
        return self.violation_scores[name]

    def _should_commit_violation(self, name, signal):
        current_score = self._score_violation_signal(name, signal)
        return current_score >= self.violation_thresholds[name], current_score

    def _refresh_intruder_ratio(self, active_track_ids=None, now=None):
        now = float(now if now is not None else time.time())
        if active_track_ids is None:
            active_track_ids = set(self.intruder_probe_timestamps.keys())
        max_count = 0
        active_track_ids = {int(track_id) for track_id in active_track_ids}
        for track_id in list(self.intruder_probe_timestamps.keys()):
            count = self._get_recent_intruder_probe_count(track_id, now=now)
            if track_id in active_track_ids:
                max_count = max(max_count, count)
            elif count <= 0:
                self.intruder_probe_timestamps.pop(track_id, None)
        self._set_violation_ratio(
            "intruder",
            max_count / max(float(self.intruder_policy["mismatch_votes"]), 1.0),
        )
        return max_count

    def _cleanup_identity_state(self, active_track_ids):
        now = time.time()
        for track_id in active_track_ids:
            self.last_track_seen[track_id] = now

        missing_tracks = [track_id for track_id in list(self.last_track_seen.keys()) if track_id not in active_track_ids]
        for track_id in missing_tracks:
            if track_id in self.identity_dict or track_id in self.identity_votes:
                self.lost_identity_tracks[track_id] = {
                    "last_seen": now,
                    "box": self.last_track_boxes.get(track_id),
                    "status": self.identity_dict.get(track_id),
                    "votes": self.identity_votes.get(track_id, {}).copy() if track_id in self.identity_votes else {},
                }

        stale_ids = [
            track_id
            for track_id, last_seen in self.last_track_seen.items()
            if track_id not in active_track_ids and now - last_seen > self.track_ttl_seconds
        ]
        for track_id in stale_ids:
            self.last_track_seen.pop(track_id, None)
            self.last_track_boxes.pop(track_id, None)
            self.identity_dict.pop(track_id, None)
            self.identity_votes.pop(track_id, None)
            self.track_first_seen.pop(track_id, None)
            self._discard_pending_track(track_id)
            self._clear_probe_history(track_id)
            self.track_missing_since.pop(track_id, None)
        for track_id, state in list(self.lost_identity_tracks.items()):
            if now - state.get("last_seen", 0) > self.track_ttl_seconds:
                self.lost_identity_tracks.pop(track_id, None)

    def _record_identity_result(self, result):
        track_id = result["id"]
        track_generation = int(result.get("track_generation", 0))
        now = time.time()
        self.pending_identity_tracks.discard(self._track_key(track_id, track_generation))
        vote_state = self.identity_votes.setdefault(track_id, {
            "student": 0,
            "intruder": 0,
            "uncertain": 0,
            "samples": 0,
            "last_confidence": 0.0,
            "recent_confidences": [],
            "recent_strict_flags": [],
            "near_match_count": 0,
            "top2_average": 0.0,
            "strict_recent_count": 0,
        })
        vote_state["samples"] += 1
        confidence = float(result.get("confidence", 0.0))
        vote_state["last_confidence"] = confidence

        recent_confidences = list(vote_state.get("recent_confidences", []))
        recent_strict_flags = list(vote_state.get("recent_strict_flags", []))
        strict_match = (result["status"] == "THI SINH")
        recent_confidences.append(confidence)
        recent_strict_flags.append(bool(strict_match))
        window_size = int(max(2, self.identity_recent_window_size))
        if len(recent_confidences) > window_size:
            recent_confidences = recent_confidences[-window_size:]
            recent_strict_flags = recent_strict_flags[-window_size:]
        vote_state["recent_confidences"] = recent_confidences
        vote_state["recent_strict_flags"] = recent_strict_flags

        sorted_confidences = sorted(recent_confidences, reverse=True)
        near_cutoff = max(0.0, float(self.face_threshold) - float(self.identity_near_match_margin))
        near_match_count = sum(1 for value in recent_confidences if float(value) >= near_cutoff)
        top2_average = (
            (float(sorted_confidences[0]) + float(sorted_confidences[1])) / 2.0
            if len(sorted_confidences) >= 2
            else (float(sorted_confidences[0]) if sorted_confidences else 0.0)
        )
        strict_recent_count = sum(1 for flag in recent_strict_flags if flag)
        vote_state["near_match_count"] = int(near_match_count)
        vote_state["top2_average"] = float(top2_average)
        vote_state["strict_recent_count"] = int(strict_recent_count)

        aggregated_student_match = bool(strict_match)
        if not aggregated_student_match and len(sorted_confidences) >= 2 and near_match_count >= 2:
            aggregated_student_match = (
                float(top2_average) >= max(0.0, float(self.face_threshold) - float(self.identity_top2_mean_margin))
                and float(sorted_confidences[0]) >= max(0.0, float(self.face_threshold) - float(self.identity_top1_margin))
            )

        if aggregated_student_match:
            vote_state["student"] += 1
            self.intruder_probe_timestamps.pop(track_id, None)
        elif confidence <= max(0.0, self.face_threshold - self.identity_uncertainty_margin):
            vote_state["intruder"] = self._register_intruder_probe(track_id, now=now)
        else:
            vote_state["uncertain"] += 1

        intruder_probe_count = self._get_recent_intruder_probe_count(track_id, now=now)
        vote_state["intruder"] = intruder_probe_count
        self._set_violation_ratio(
            "intruder",
            intruder_probe_count / max(float(self.intruder_policy["mismatch_votes"]), 1.0),
        )

        mismatch_chain_ready = (
            intruder_probe_count >= int(self.intruder_policy["mismatch_votes"])
            and int(vote_state.get("samples", 0)) >= int(max(self.identity_burst_frame_count, self.identity_intruder_vote_target))
            and int(vote_state.get("strict_recent_count", 0)) == 0
            and int(vote_state.get("near_match_count", 0)) < 2
        )

        if vote_state["student"] >= self.identity_vote_target:
            self.identity_dict[track_id] = "THI SINH"
            self.identity_suspicious_tracks.pop(track_id, None)
            return "THI SINH"
        if mismatch_chain_ready:
            self.identity_dict[track_id] = "KE DOT NHAP"
            self._mark_track_identity_suspicious(track_id)
            return "KE DOT NHAP"
        if vote_state["samples"] >= self.identity_probe_limit:
            if vote_state["student"] >= self.identity_vote_target:
                final_status = "THI SINH"
            elif mismatch_chain_ready and intruder_probe_count > vote_state["student"]:
                final_status = "KE DOT NHAP"
            else:
                final_status = "CAN XAC MINH"
            self.identity_dict[track_id] = final_status
            if final_status == "THI SINH":
                self.identity_suspicious_tracks.pop(track_id, None)
            else:
                self._mark_track_identity_suspicious(track_id)
            return final_status
        return None

    def _get_track_display_status(self, track_id, fallback_label):
        if track_id in self.identity_dict:
            return self.identity_dict[track_id]
        vote_state = self.identity_votes.get(track_id)
        if vote_state and vote_state.get("samples", 0) > 0:
            if vote_state.get("uncertain", 0) > 0 and vote_state.get("student", 0) == 0 and vote_state.get("intruder", 0) == 0:
                return "Can xac minh"
            return "Dang doi chieu..."
        if int(track_id) in self.track_first_seen:
            return "Can xac minh"
        return fallback_label

    def _is_person_class(self, class_name):
        label = str(class_name).strip().lower()
        return any(token in label for token in ["person", "human", "student", "nguoi", "face"])

    # ------------------------------------------------------------------
    # Frame ring-buffer helpers (Phương án 1 – Event Clip Evidence)
    # ------------------------------------------------------------------

    def _push_to_ring_buffer(self, frame, ts: float):
        """Ghi frame vào vòng đệm, loại bỏ frame quá cũ."""
        if frame is None:
            return
        with self.frame_ring_lock:
            self.frame_ring_buffer.append((ts, frame.copy()))
            cutoff = ts - self.frame_ring_max_seconds
            while self.frame_ring_buffer and self.frame_ring_buffer[0][0] < cutoff:
                self.frame_ring_buffer.popleft()

    def _generate_event_clip(self, event_time: float, pre_seconds: float = 5.0, target_fps: int = 10):
        """
        Trích frame từ ring buffer trong [event_time - pre_seconds, event_time].
        Trả về (clip_bytes, thumbnail_bytes, event_started_at, event_ended_at) hoặc
        (None, None, None, None) nếu không đủ frame.
        clip_bytes là nội dung AVI dùng codec MJPG; thumbnail là JPEG.
        """
        with self.frame_ring_lock:
            frames_copy = list(self.frame_ring_buffer)

        cutoff_start = event_time - pre_seconds
        relevant = [(ts, f) for ts, f in frames_copy if ts >= cutoff_start]
        if len(relevant) < 3:
            logger.debug("Event clip: not enough frames (%d) in ring buffer", len(relevant))
            return None, None, None, None

        event_started_at = relevant[0][0]
        event_ended_at = relevant[-1][0]
        frames_only = [f for _, f in relevant]
        h, w = frames_only[0].shape[:2]

        try:
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".avi")
            os.close(tmp_fd)
            fourcc = cv2.VideoWriter_fourcc(*"MJPG")
            writer = cv2.VideoWriter(tmp_path, fourcc, float(target_fps), (w, h))
            for f in frames_only:
                writer.write(f)
            writer.release()
            with open(tmp_path, "rb") as fh:
                clip_bytes = fh.read()
        except Exception:
            logger.exception("Failed to encode event clip")
            return None, None, None, None
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        # Thumbnail = frame giữa clip
        mid_frame = frames_only[len(frames_only) // 2]
        ok, thumb_buf = cv2.imencode(".jpg", mid_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        thumbnail_bytes = thumb_buf.tobytes() if ok else None

        return clip_bytes, thumbnail_bytes, event_started_at, event_ended_at

    def _upload_clip_worker(
        self,
        violation_id: int,
        clip_bytes: bytes,
        thumbnail_bytes,
        event_started_at: float,
        event_ended_at: float,
        violation_type: str,
    ):
        """Upload clip và thumbnail lên server để liên kết với violation_id."""
        try:
            files = {"clip_file": ("event_clip.avi", clip_bytes, "video/x-msvideo")}
            if thumbnail_bytes:
                files["thumbnail_file"] = ("thumb.jpg", thumbnail_bytes, "image/jpeg")
            response = requests.post(
                f"{self.api_url}/api/student/upload_violation_clip",
                headers=self._auth_headers(),
                data={
                    "violation_id": str(violation_id),
                    "event_started_at": str(round(event_started_at, 3)),
                    "event_ended_at": str(round(event_ended_at, 3)),
                },
                files=files,
                timeout=30,
            )
            response.raise_for_status()
            logger.info("Event clip uploaded for violation_id=%s type=%s", violation_id, violation_type)
        except Exception:
            logger.exception("Failed to upload event clip for violation_id=%s", violation_id)
        finally:
            self.clip_upload_inflight.discard(violation_type)

    # ------------------------------------------------------------------

    def _should_send_violation(self, violation_type, cooldown_seconds):
        now = time.time()
        last_sent = self.violation_last_sent.get(violation_type, 0)
        if now - last_sent < cooldown_seconds:
            return False
        self.violation_last_sent[violation_type] = now
        return True

    def _upload_violation_worker(self, violation_type, image_bytes, event_time: float = 0.0):
        try:
            response = requests.post(
                f"{self.api_url}/upload_violation/",
                headers=self._auth_headers(),
                data={"error_type": violation_type},
                files={"file": ("violation.jpg", image_bytes, "image/jpeg")},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") != "success":
                logger.warning("Violation upload rejected: %s", data)
                return
            # Phương án 1: sau khi có violation_id, generate clip và upload
            violation_id = data.get("violation_id")
            if violation_id and violation_type not in self.clip_upload_inflight:
                self.clip_upload_inflight.add(violation_type)
                clip_ts = event_time if event_time > 0 else time.time()
                threading.Thread(
                    target=self._generate_and_upload_clip,
                    args=(int(violation_id), clip_ts, violation_type),
                    daemon=True,
                ).start()
            self._set_network_status(True)
        except Exception:
            logger.exception("Failed to upload violation %s", violation_type)
            try:
                self._queue_offline_job(
                    "violation_upload",
                    {
                        "violation_type": str(violation_type),
                        "event_time": float(event_time or time.time()),
                    },
                    binary_fields={"image": image_bytes},
                )
            except Exception:
                logger.exception("Failed to spool violation into offline queue")
            self._set_network_status(False, "không gửi được bằng chứng vi phạm")

    def _generate_and_upload_clip(self, violation_id: int, event_time: float, violation_type: str):
        """Sinh clip từ ring buffer và upload lên server (chạy trong daemon thread)."""
        clip_bytes, thumb_bytes, started_at, ended_at = self._generate_event_clip(event_time, pre_seconds=5.0)
        if clip_bytes is None:
            logger.debug("Event clip skipped (no frames) for violation_id=%s", violation_id)
            self.clip_upload_inflight.discard(violation_type)
            return
        self._upload_clip_worker(violation_id, clip_bytes, thumb_bytes, started_at, ended_at, violation_type)

    def _upload_earpiece_suspect_worker(self, crop_bytes: bytes, tier1_conf: float):
        """Gửi crop nghi vấn tai nghe lên server để giám thị chạy Tầng 2."""
        try:
            response = requests.post(
                f"{self.api_url}/api/monitor/student/earpiece_suspect",
                headers=self._auth_headers(),
                data={"tier1_conf": str(tier1_conf)},
                files={"file": ("suspect.jpg", crop_bytes, "image/jpeg")},
                timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
            violation_id = payload.get("violation_id")
            if violation_id and "Su dung tai nghe" not in self.clip_upload_inflight:
                self.clip_upload_inflight.add("Su dung tai nghe")
                threading.Thread(
                    target=self._generate_and_upload_clip,
                    args=(int(violation_id), time.time(), "Su dung tai nghe"),
                    daemon=True,
                ).start()
        except Exception:
            logger.exception("Failed to upload earpiece suspect crop")

    def _upload_earpiece_suspects(self, suspects: list, frame):
        """Trích crop nghi vấn tai nghe đã qua bộ lọc thời gian của Tầng 1 rồi upload."""
        self._last_earpiece_upload_ts = time.time()
        h, w = frame.shape[:2]
        for s in suspects[:1]:  # chỉ gửi 1 nghi vấn đại diện mỗi lần
            box = s.get("box", [])
            conf = float(s.get("conf_tier1", 0.0))
            if len(box) != 4:
                continue
            x1, y1, x2, y2 = (
                max(0, int(box[0])), max(0, int(box[1])),
                min(w, int(box[2])), min(h, int(box[3])),
            )
            if x2 <= x1 or y2 <= y1:
                continue
            # Padding nhỏ để bao quanh vùng tai
            pad = 20
            crop = frame[max(0, y1 - pad):min(h, y2 + pad), max(0, x1 - pad):min(w, x2 + pad)]
            ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if not ok:
                continue
            threading.Thread(
                target=self._upload_earpiece_suspect_worker,
                args=(buf.tobytes(), conf),
                daemon=True,
            ).start()

    def _record_violation(self, violation_type, frame, cooldown_seconds=10):
        if not self.session_token or frame is None or not self._should_send_violation(violation_type, cooldown_seconds):
            return
        success, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not success:
            logger.warning("Could not encode violation frame for %s", violation_type)
            return
        event_time = time.time()
        threading.Thread(
            target=self._upload_violation_worker,
            args=(violation_type, buffer.tobytes(), event_time),
            daemon=True,
        ).start()

    def _handle_room_locked(self, message):
        if self.room_locked:
            return
        self.room_locked = True
        self.btn_enter_exam.setEnabled(False)
        self.timer.stop()
        self.sync_timer.stop()
        self.display_timer.stop()
        self.exam_clock_timer.stop()
        logger.warning("Room locked for %s: %s", self.msv, message)
        self._show_dialog("Phòng thi đã khóa", message, "Phiên thi sẽ được kết thúc để đảm bảo trạng thái đồng bộ với máy chủ.")
        QApplication.quit()

    def _render_camera_frame(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888)
        # Use fast scaling for live preview to minimize perceived latency.
        pixmap = QPixmap.fromImage(image).scaled(max(self.exam_view.width(), 1), max(self.exam_view.height(), 1), Qt.KeepAspectRatio, Qt.FastTransformation)
        self.exam_view.setPixmap(pixmap)

    def _update_display(self):
        """Reads the latest raw camera frame and renders it clean (no bounding boxes).
        Runs on the display_timer (25 ms), decoupled from the AI processing pipeline.
        Boxes are only drawn on evidence clips stored in the ring buffer, not shown to the user."""
        frame = None
        try:
            while True:
                frame = self.display_q.get_nowait()
        except Empty:
            pass
        if frame is None:
            return
        self._render_camera_frame(frame)

    def _render_verify_frame(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(image).scaled(max(self.verify_view.width(), 1), max(self.verify_view.height(), 1), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.verify_view.setPixmap(pixmap)

    def _render_verify_selected_frames(self):
        for index, label in enumerate(self.verify_selected_labels):
            if index >= len(self.selected_verify_frames):
                label.clear()
                label.setText("Chưa có ảnh")
                continue

            frame = self.selected_verify_frames[index]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(image).scaled(150, 112, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            label.setPixmap(pixmap)

    def _reset_verify_capture_state(self):
        self.verify_preview_frames.clear()
        self.selected_verify_frames.clear()
        self._render_verify_selected_frames()

    def _collect_fresh_verify_frames(self, duration_seconds=2.0, target_samples=12):
        if self.verify_camera is None or not self.verify_camera.isOpened():
            return 0

        captured = 0
        deadline = time.time() + max(0.5, float(duration_seconds))
        while time.time() < deadline and captured < max(3, int(target_samples)):
            ret, frame = self.verify_camera.read()
            if not ret:
                QApplication.processEvents()
                time.sleep(0.03)
                continue

            self._render_verify_frame(frame)
            face_data = extract_face_roi(frame)
            if face_data:
                self.verify_preview_frames.append(
                    {
                        "score": float(face_data.get("focus_score", 0.0)),
                        "frame": frame.copy(),
                        "face": face_data["face"].copy(),
                        "captured_at": time.time(),
                    }
                )
                self.verify_preview_frames = sorted(self.verify_preview_frames, key=lambda item: item["score"], reverse=True)[:12]
                captured += 1
                self.lbl_verify_live_status.setText(f"Đang chụp lại bộ ảnh mới: {captured}/3+ khung hình hợp lệ.")
            else:
                self.lbl_verify_live_status.setText("Đang chụp lại bộ ảnh mới. Hãy giữ khuôn mặt ở giữa khung hình.")

            QApplication.processEvents()
            time.sleep(0.05)

        return len(self.verify_preview_frames)

    def _resolve_frame_from_payload(self, payload):
        if not isinstance(payload, dict):
            return payload[0] if payload else None
        slot_index = payload.get("shared_slot")
        if slot_index is None:
            return payload.get("frame")
        try:
            slot_index = int(slot_index)
        except (TypeError, ValueError):
            return None
        if slot_index < 0 or slot_index >= self.shared_frame_slots:
            return None
        expected_frame_id = int(payload.get("frame_id", -1))
        if int(self.shared_frame_ids[slot_index]) != expected_frame_id:
            return None
        frame = self.shared_frame_view[slot_index].copy()
        if int(self.shared_frame_ids[slot_index]) != expected_frame_id:
            return None
        return frame

    def start_verify_preview(self):
        if self.verify_camera is None:
            self.verify_camera = cv2.VideoCapture(0, cv2.CAP_MSMF)
            if self.verify_camera.isOpened():
                self.verify_camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                self.verify_camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                self.verify_camera.set(cv2.CAP_PROP_FPS, 20)

        if self.verify_camera is None or not self.verify_camera.isOpened():
            self.lbl_verify_live_status.setText("Không mở được webcam xem trước.")
            return

        self.lbl_verify_live_status.setText("Camera đang hoạt động. Đây là khung hình dùng cho bước xác minh.")
        if not self.verify_preview_timer.isActive():
            self.verify_preview_timer.start(50)

    def stop_verify_preview(self):
        self.verify_preview_timer.stop()
        if self.verify_camera is not None:
            self.verify_camera.release()
            self.verify_camera = None

    def update_verify_preview(self):
        if self.verify_camera is None or not self.verify_camera.isOpened():
            return

        ret, frame = self.verify_camera.read()
        if not ret:
            self.lbl_verify_live_status.setText("Không đọc được khung hình webcam.")
            return

        self._render_verify_frame(frame)

        face_data = extract_face_roi(frame)
        if face_data:
            self.verify_preview_frames.append(
                {
                    "score": float(face_data.get("focus_score", 0.0)),
                    "frame": frame.copy(),
                    "face": face_data["face"].copy(),
                    "captured_at": time.time(),
                }
            )
            self.verify_preview_frames = sorted(self.verify_preview_frames, key=lambda item: item["score"], reverse=True)[:12]
            self.lbl_verify_live_status.setText("Đã phát hiện khuôn mặt. Hệ thống đang giữ các khung hình rõ nhất để gửi đi.")
        else:
            self.lbl_verify_live_status.setText("Chưa phát hiện khuôn mặt trong khung xem trước.")

    def _load_reference_face(self):
        headers = self._auth_headers()
        reference_images = []

        try:
            meta_res = requests.get(f"{self.api_url}/api/get_face_refs/{self.msv}", headers=headers, timeout=10)
            meta_res.raise_for_status()
            payload = meta_res.json()
            for item in payload.get("references", []):
                face_image = str(item.get("face_image") or "").strip()
                if not face_image:
                    continue
                image_res = requests.get(
                    f"{self.api_url}/api/get_face/{self.msv}",
                    params={"face_image": face_image},
                    headers=headers,
                    timeout=10,
                )
                image_res.raise_for_status()
                ref_image = cv2.imdecode(np.asarray(bytearray(image_res.content), np.uint8), 1)
                if ref_image is not None:
                    reference_images.append(ref_image)
        except Exception:
            logger.exception("Could not load multiple reference faces; falling back to primary face")

        if not reference_images:
            res = requests.get(f"{self.api_url}/api/get_face/{self.msv}", headers=headers, timeout=10)
            res.raise_for_status()
            ref_image = cv2.imdecode(np.asarray(bytearray(res.content), np.uint8), 1)
            if ref_image is None:
                raise ValueError("Ảnh tham chiếu không hợp lệ")
            reference_images = [ref_image]

        self.init_q.put({"student_id": self.msv, "reference_images": reference_images, "face_threshold": self.face_threshold})
        return reference_images[0]

    def init_ui(self):
        self.stack = QStackedWidget(); self.setCentralWidget(self.stack)
        
        # PAGE 1: LOGIN
        self.page_login = QWidget(); l_lay = QVBoxLayout(self.page_login)
        set_page_margins(self.page_login)
        login_toolbar = AppToolbar(
            "S-MONITOR Student",
            "Đăng nhập an toàn để truy cập phiên thi đã được phân công.",
            role="student",
        )
        login_toolbar.badge.setText("Secure access")
        box = QGroupBox("1. ĐĂNG NHẬP"); box.setFixedSize(400, 280); bl = QVBoxLayout(box)
        self.inp_msv = QLineEdit(); self.inp_msv.setPlaceholderText("Mã Sinh Viên")
        self.inp_pw = QLineEdit(); self.inp_pw.setPlaceholderText("Mật Khẩu"); self.inp_pw.setEchoMode(QLineEdit.Password)
        btn = QPushButton("TIẾP TỤC"); btn.clicked.connect(self.process_login)
        btn_recover = QPushButton("YÊU CẦU CẤP LẠI MẬT KHẨU"); btn_recover.clicked.connect(self.request_student_password_reset)
        btn_recover.setStyleSheet("background:#2E2E2E; color:#FFFFFF; padding:10px;")
        self.inp_msv.returnPressed.connect(self.process_login)
        self.inp_pw.returnPressed.connect(self.process_login)
        bl.addWidget(self.inp_msv); bl.addWidget(self.inp_pw); bl.addWidget(btn); bl.addWidget(btn_recover)
        login_logo = build_login_logo_label(role="student")
        l_lay.addWidget(login_toolbar)
        l_lay.addStretch(); l_lay.addWidget(login_logo, 0, Qt.AlignCenter); l_lay.addWidget(box, 0, Qt.AlignCenter); l_lay.addStretch()

        # PAGE 2: JOIN CLASS
        self.page_class = QWidget(); c_lay = QVBoxLayout(self.page_class)
        set_page_margins(self.page_class)
        class_toolbar = AppToolbar(
            "Join Exam Room",
            "Xác thực thông tin lớp thi trước khi chuyển sang bước kiểm tra danh tính.",
            role="student",
        )
        class_toolbar.badge.setText("Room check")
        box2 = QGroupBox("2. VÀO LỚP THI"); box2.setFixedSize(430, 340); bl2 = QVBoxLayout(box2)
        self.lbl_welcome = QLabel("Xin chào!"); self.lbl_welcome.setAlignment(Qt.AlignCenter)
        self.lbl_join_hint = QLabel("Nhập đúng mã lớp và mật khẩu phòng thi để tiếp tục.")
        self.lbl_join_hint.setAlignment(Qt.AlignCenter)
        self.lbl_join_hint.setStyleSheet("color:#666666; padding-bottom:10px;")
        self.inp_cid = QLineEdit(); self.inp_cid.setPlaceholderText("ID Lớp")
        self.inp_cpass = QLineEdit(); self.inp_cpass.setPlaceholderText("Mật khẩu Lớp"); self.inp_cpass.setEchoMode(QLineEdit.Password)
        btn2 = QPushButton("TIẾP TỤC ĐẾN BƯỚC XÁC MINH"); btn2.clicked.connect(self.process_join_class)
        self.inp_cid.returnPressed.connect(self.process_join_class)
        self.inp_cpass.returnPressed.connect(self.process_join_class)
        bl2.addWidget(self.lbl_welcome); bl2.addWidget(self.lbl_join_hint); bl2.addWidget(self.inp_cid); bl2.addWidget(self.inp_cpass); bl2.addWidget(btn2)
        c_lay.addWidget(class_toolbar)
        c_lay.addStretch(); c_lay.addWidget(box2, 0, Qt.AlignCenter); c_lay.addStretch()

        # PAGE 3: VERIFY IDENTITY
        self.page_verify = QWidget(); v_lay = QHBoxLayout(self.page_verify)
        set_page_margins(self.page_verify)
        verify_left = QFrame(); verify_left.setStyleSheet("background:#FFFFFF; border:1px solid #E3E3E3; border-radius:14px;")
        verify_left_layout = QVBoxLayout(verify_left)
        verify_title = QLabel("3. XÁC MINH DANH TÍNH")
        verify_title.setStyleSheet("font-size:22px; font-weight:bold; color:#111111;")
        self.lbl_verify_class = QLabel("Lớp thi: --")
        self.lbl_verify_class.setStyleSheet("color:#666666; font-size:14px;")
        self.lbl_verify_status = QLabel("Xác minh danh tính trước khi vào phòng thi")
        self.lbl_verify_status.setWordWrap(True)
        self.lbl_verify_status.setStyleSheet("font-size:20px; font-weight:bold; color:#111111;")
        self.lbl_verify_hint = QLabel("Đặt khuôn mặt ở giữa khung hình, đủ sáng và không đeo vật che mặt.")
        self.lbl_verify_hint.setWordWrap(True)
        self.lbl_verify_hint.setStyleSheet("color:#666666; font-size:14px;")
        verify_steps = QLabel("1. Ngồi thẳng trước webcam\n2. Bấm quét danh tính\n3. Chờ thông báo thành công rồi vào phòng thi")
        verify_steps.setStyleSheet("background:#F5F5F5; border-radius:10px; padding:14px; color:#333333;")
        self.btn_verify_identity = QPushButton("QUÉT DANH TÍNH")
        self.btn_verify_identity.setStyleSheet("background-color:#111111; padding:14px; font-size:15px;")
        self.btn_verify_identity.clicked.connect(self.process_identity_verification)
        self.btn_enter_exam = QPushButton("VÀO PHÒNG THI")
        self.btn_enter_exam.setEnabled(False)
        self.btn_enter_exam.setStyleSheet("background-color:#2B2B2B; padding:14px; font-size:15px;")
        self.btn_enter_exam.clicked.connect(self.enter_exam_room)
        verify_left_layout.addWidget(verify_title)
        verify_left_layout.addWidget(self.lbl_verify_class)
        verify_left_layout.addSpacing(20)
        verify_left_layout.addWidget(self.lbl_verify_status)
        verify_left_layout.addWidget(self.lbl_verify_hint)
        verify_left_layout.addSpacing(16)
        verify_left_layout.addWidget(verify_steps)
        verify_left_layout.addStretch()
        verify_left_layout.addWidget(self.btn_verify_identity)
        verify_left_layout.addWidget(self.btn_enter_exam)

        verify_right = QFrame(); verify_right.setStyleSheet("background:qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FFFFFF, stop:1 #F2F2F2); border:1px solid #E2E2E2; border-radius:14px;")
        verify_right_layout = QVBoxLayout(verify_right)
        verify_badge = QLabel("Thiết bị phù hợp: webcam tích hợp, ánh sáng phòng bình thường")
        verify_badge.setStyleSheet("background:#EFEFEF; color:#222222; padding:10px; border-radius:8px;")
        verify_hero = QLabel("Xác minh riêng trước khi vào bài giúp tránh lỗi gộp bước và dễ thử lại khi ảnh chưa đạt.")
        verify_hero.setWordWrap(True)
        verify_hero.setStyleSheet("font-size:18px; font-weight:bold; color:#111111; padding:8px 0;")
        verify_note = QLabel("Nếu vừa đổi ảnh gốc, hãy nhìn thẳng vào camera và giữ khoảng cách như khi chụp ảnh tham chiếu.")
        verify_note.setWordWrap(True)
        verify_note.setStyleSheet("color:#666666; font-size:14px;")
        self.verify_view = QLabel()
        self.verify_view.setMinimumSize(520, 390)
        self.verify_view.setStyleSheet("background:#111111; border:1px solid #2D2D2D; border-radius:12px;")
        self.verify_view.setAlignment(Qt.AlignCenter)
        self.verify_view.setText("Đang chờ mở webcam...")
        self.lbl_verify_live_status = QLabel("Chưa mở camera xem trước.")
        self.lbl_verify_live_status.setWordWrap(True)
        self.lbl_verify_live_status.setStyleSheet("background:#F5F5F5; color:#333333; padding:10px; border-radius:8px;")
        verify_selected_title = QLabel("3 khung hình sẽ gửi lên server")
        verify_selected_title.setStyleSheet("font-weight:bold; color:#111111; padding-top:6px;")
        verify_selected_layout = QHBoxLayout()
        self.verify_selected_labels = []
        for _ in range(3):
            label = QLabel("Chưa có ảnh")
            label.setFixedSize(150, 112)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("background:#F8F8F8; border:1px dashed #C9C9C9; border-radius:10px; color:#666666;")
            self.verify_selected_labels.append(label)
            verify_selected_layout.addWidget(label)
        verify_right_layout.addWidget(verify_badge)
        verify_right_layout.addSpacing(18)
        verify_right_layout.addWidget(verify_hero)
        verify_right_layout.addWidget(verify_note)
        verify_right_layout.addSpacing(16)
        verify_right_layout.addWidget(self.verify_view, 1)
        verify_right_layout.addWidget(self.lbl_verify_live_status)
        verify_right_layout.addWidget(verify_selected_title)
        verify_right_layout.addLayout(verify_selected_layout)
        verify_right_layout.addStretch()
        v_lay.addWidget(verify_left, 3)
        v_lay.addWidget(verify_right, 2)

        # PAGE 4: MAIN EXAM
        self.page_main = QWidget(); m_lay = QHBoxLayout(self.page_main)
        set_page_margins(self.page_main)
        left_panel = QVBoxLayout()
        left_panel.setSpacing(12)
        exam_title = QLabel("Bài thi đang diễn ra")
        exam_title.setStyleSheet("font-size:20px; font-weight:bold; color:#111111;")
        exam_subtitle = QLabel("Bài thi hiển thị tối giản để thí sinh tập trung làm bài. Khung webcam nhỏ chỉ dùng để tự căn tư thế trước camera.")
        exam_subtitle.setWordWrap(True)
        exam_subtitle.setStyleSheet("color:#666666; font-size:13px;")
        exam_header_row = QHBoxLayout()
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.btn_filter_unanswered = QPushButton("Chưa chọn")
        self.btn_filter_answered = QPushButton("Đã chọn")
        self.btn_filter_all = QPushButton("Tất cả")
        for button in [self.btn_filter_unanswered, self.btn_filter_answered, self.btn_filter_all]:
            button.setCheckable(True)
            button.setStyleSheet("padding:8px 12px; background:#EFEFEF; color:#111111;")
        self.btn_filter_all.setChecked(True)
        self.btn_filter_unanswered.clicked.connect(lambda: self._set_question_filter_mode("unanswered"))
        self.btn_filter_answered.clicked.connect(lambda: self._set_question_filter_mode("answered"))
        self.btn_filter_all.clicked.connect(lambda: self._set_question_filter_mode("all"))
        filter_row.addWidget(self.btn_filter_unanswered)
        filter_row.addWidget(self.btn_filter_answered)
        filter_row.addWidget(self.btn_filter_all)
        self.btn_submit_exam = QPushButton("NỘP BÀI")
        self.btn_submit_exam.setEnabled(False)
        self.btn_submit_exam.setStyleSheet("background:#111111; color:#FFFFFF; padding:10px 16px; font-weight:bold;")
        self.btn_submit_exam.clicked.connect(self.submit_action)
        self.lbl_exam_timer = QLabel("00:00:00")
        self.lbl_exam_timer.setStyleSheet("background:#111111; color:#FFFFFF; padding:10px 16px; border-radius:10px; font-size:18px; font-weight:bold;")
        exam_header_row.addLayout(filter_row)
        exam_header_row.addStretch()
        exam_header_row.addWidget(self.lbl_exam_timer)
        exam_header_row.addWidget(self.btn_submit_exam)

        self.scroll = QScrollArea(); self.quiz_cont = QWidget(); self.quiz_inner = QVBoxLayout(self.quiz_cont)
        self.scroll.setWidget(self.quiz_cont); self.scroll.setWidgetResizable(True)
        self.quiz_empty_state = EmptyState(
            "Chưa nạp bài thi",
            "Đề thi sẽ xuất hiện tại đây sau khi kết nối phiên thi và xác minh danh tính hoàn tất.",
            parent=self.page_main,
        )
        left_panel.addWidget(exam_title)
        left_panel.addWidget(exam_subtitle)
        left_panel.addLayout(exam_header_row)
        left_panel.addWidget(self.quiz_empty_state)
        left_panel.addWidget(self.scroll, 1)

        side_panel = QFrame()
        side_panel.setFixedWidth(260)
        side_panel.setStyleSheet("background:transparent; border:none;")
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)
        side_layout.addStretch()

        preview_card = QGroupBox("Căn tư thế webcam")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(10, 14, 10, 10)
        self.exam_view = QLabel()
        self.exam_view.setFixedSize(220, 150)
        self.exam_view.setAlignment(Qt.AlignCenter)
        self.exam_view.setStyleSheet("background:#000000; border-radius:12px; color:#BBBBBB;")
        self.exam_view.setText("Đang chờ camera...")
        self.lbl_broadcast = QLabel("📢 Sẵn sàng...")
        self.lbl_broadcast.setStyleSheet("background:#171717; color:white; padding:8px; border-radius:8px; font-size:12px;")
        self.lbl_exam_status = QLabel("AI giám sát đang sẵn sàng.")
        self.lbl_exam_status.setWordWrap(True)
        self.lbl_exam_status.setStyleSheet("background:#F3F3F3; color:#111111; padding:8px; border-radius:8px; font-weight:bold; font-size:12px;")
        preview_layout.addWidget(self.exam_view, 0, Qt.AlignCenter)
        self.lbl_hint_banner = QLabel("")
        self.lbl_hint_banner.setWordWrap(True)
        self.lbl_hint_banner.setAlignment(Qt.AlignCenter)
        self.lbl_hint_banner.setFixedWidth(200)
        self.lbl_hint_banner.hide()
        preview_layout.addWidget(self.lbl_hint_banner, 0, Qt.AlignCenter)
        side_layout.addWidget(preview_card)

        self.lbl_broadcast.hide()
        self.lbl_exam_status.hide()
        self.lbl_ai_identity = QLabel()
        self.lbl_ai_camera = QLabel()
        self.lbl_ai_attention = QLabel()
        self.lbl_ai_risk = QLabel()
        self.pb_phone_risk = QProgressBar()
        self.pb_people_risk = QProgressBar()
        self.pb_attention_risk = QProgressBar()
        self.pb_intruder_risk = QProgressBar()
        for progress_bar in [self.pb_phone_risk, self.pb_people_risk, self.pb_attention_risk, self.pb_intruder_risk]:
            progress_bar.hide()

        m_lay.addLayout(left_panel, 1)
        m_lay.addWidget(side_panel, 0, Qt.AlignRight)

        self.stack.addWidget(self.page_login); self.stack.addWidget(self.page_class); self.stack.addWidget(self.page_verify); self.stack.addWidget(self.page_main)

    def process_login(self):
        self.msv = self.inp_msv.text().strip()
        pw = self.inp_pw.text().strip()
        if not self.msv or not pw:
            self._show_dialog("Thiếu thông tin", "Vui lòng nhập Mã Sinh Viên và Mật Khẩu.")
            return
        try:
            r = requests.post(f"{self.api_url}/api/student/login", data={"msv": self.msv, "password": pw}, timeout=10)
            r.raise_for_status()
            data = r.json()
            if data.get('status') == 'success':
                self.full_name = data.get('full_name')
                self.lbl_welcome.setText(f"Xin chào,\n{self.full_name}")
                self.stack.setCurrentIndex(1)
            else:
                self._show_dialog("Đăng nhập thất bại", data.get('message', 'Không xác thực được tài khoản.'))
        except requests.RequestException:
            logger.exception("Student login request failed")
            self._show_dialog("Kết nối thất bại", "Không kết nối được Server")
        except ValueError:
            logger.exception("Student login returned invalid JSON")
            self._show_dialog("Phản hồi không hợp lệ", "Server trả về dữ liệu không đọc được.")

    def request_student_password_reset(self):
        dialog = PasswordRecoveryDialog("Sinh viên", "Mã Sinh Viên", account_value=self.inp_msv.text().strip(), parent=self)
        if dialog.exec_() != QDialog.Accepted:
            return
        payload = dialog.get_payload()
        if not payload["account_id"] or not payload["full_name"]:
            return self._show_dialog("Thiếu thông tin", "Vui lòng nhập Mã Sinh Viên và Họ tên để gửi yêu cầu.")
        try:
            response = requests.post(
                f"{self.api_url}/api/password-recovery/request",
                data={
                    "role": "student",
                    "account_id": payload["account_id"],
                    "full_name": payload["full_name"],
                    "note": payload["note"],
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "success":
                self._show_dialog("Đã gửi yêu cầu", data.get("message", "Quản trị viên sẽ cấp lại mật khẩu tạm sau khi kiểm tra."))
            else:
                self._show_dialog("Không gửi được yêu cầu", data.get("message", "Hệ thống từ chối yêu cầu cấp lại mật khẩu."))
        except requests.RequestException as exc:
            logger.exception("Student password recovery request failed")
            self._show_dialog("Kết nối thất bại", str(exc))
        except ValueError:
            logger.exception("Student password recovery returned invalid JSON")
            self._show_dialog("Phản hồi không hợp lệ", "Server trả về dữ liệu không đọc được.")

    def capture_face_images(self):
        candidates = list(self.verify_preview_frames)
        if not candidates:
            logger.error("No preview frames available for face capture")
            return []

        candidates.sort(key=lambda item: item["score"], reverse=True)
        images = []
        self.selected_verify_frames = []
        selected_items = []
        min_gap_seconds = 0.35

        for item in candidates:
            captured_at = float(item.get("captured_at", 0.0) or 0.0)
            if any(abs(captured_at - float(chosen.get("captured_at", 0.0) or 0.0)) < min_gap_seconds for chosen in selected_items):
                continue
            selected_items.append(item)
            if len(selected_items) >= 3:
                break

        if len(selected_items) < 3:
            for item in candidates:
                if item in selected_items:
                    continue
                selected_items.append(item)
                if len(selected_items) >= 3:
                    break

        for item in selected_items:
            face_frame = item.get("face")
            if face_frame is None:
                continue
            success, buf = cv2.imencode('.jpg', face_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            if success:
                images.append(buf.tobytes())
            self.selected_verify_frames.append(item["frame"].copy())
        self._render_verify_selected_frames()
        return images

    def process_join_class(self):
        cid = self.inp_cid.text().strip()
        cpass = self.inp_cpass.text().strip()
        if not cid or not cpass:
            self._show_dialog("Thiếu thông tin", "Vui lòng nhập ID Lớp và mật khẩu lớp.")
            return
        try:
            r = requests.post(
                f"{self.api_url}/api/student/join_class",
                data={"msv": self.msv, "class_id": cid, "class_password": cpass},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            if data.get('status') == 'success':
                self.exam_id = int(cid)
                self.class_name = data.get("class_name", "")
                self.session_token = data.get("session_token")
                if not self.session_token:
                    logger.error("Join class response missing session token")
                    return QMessageBox.critical(self, "Lỗi", "Server không trả về phiên thi hợp lệ.")

                self._reset_exam_state()
                self.start_verify_preview()
                self.stack.setCurrentIndex(2)
                class_display = self.class_name or f"Lớp {cid}"
                self.lbl_verify_class.setText(f"Lớp thi: {class_display}")
                self.lbl_verify_status.setText("Cần xác minh danh tính trước khi vào phòng thi")
                self.lbl_verify_hint.setText("Bước này chỉ ghi nhận trạng thái chờ xác minh. DeepFace chỉ chạy khi bạn bấm quét danh tính.")
            else:
                self._show_dialog("Không thể vào lớp", data.get('message', 'Không thể vào lớp thi.'))
        except requests.RequestException as exc:
            logger.exception("Join class request failed")
            QMessageBox.critical(self, "Lỗi", f"Kết nối thất bại: {exc}")
        except ValueError:
            logger.exception("Join class returned invalid JSON")
            QMessageBox.critical(self, "Lỗi", "Phản hồi từ server không hợp lệ")

    def process_identity_verification(self):
        if not self.session_token:
            self._show_dialog("Thiếu phiên", "Bạn cần vào lớp thi trước khi xác minh danh tính.")
            return
        if self.verify_camera is None or not self.verify_camera.isOpened():
            self.start_verify_preview()
        if self.verify_camera is None or not self.verify_camera.isOpened():
            self._show_dialog("Không mở được camera", "Hệ thống chưa truy cập được webcam để chụp lại ảnh xác minh.")
            return

        timer_was_active = self.verify_preview_timer.isActive()
        try:
            self.btn_verify_identity.setEnabled(False)
            if timer_was_active:
                self.verify_preview_timer.stop()
            self._reset_verify_capture_state()
            self.lbl_verify_status.setText("Đang chuẩn bị xác minh danh tính...")
            self.lbl_verify_hint.setText("Đã xóa 3 ảnh cũ. Giữ yên đầu trong vài giây để hệ thống chụp lại 3 ảnh mới rõ nhất.")
            QApplication.processEvents()

            captured_count = self._collect_fresh_verify_frames(duration_seconds=2.2, target_samples=12)
            if captured_count <= 0:
                self.lbl_verify_status.setText("Không phát hiện được khuôn mặt")
                self.lbl_verify_hint.setText("Chưa chụp được ảnh mới. Thử lại với ánh sáng tốt hơn hoặc ngồi gần camera hơn.")
                self._show_dialog("Không thấy khuôn mặt", "Hệ thống chưa chụp lại được khung hình phù hợp để xác minh.")
                return

            face_bytes = self.capture_face_images()
            if not face_bytes:
                self.lbl_verify_status.setText("Không phát hiện được khuôn mặt")
                self.lbl_verify_hint.setText("Thử lại với ánh sáng tốt hơn hoặc ngồi gần camera hơn.")
                self._show_dialog("Không thấy khuôn mặt", "Hệ thống chưa lấy được khung hình phù hợp để xác minh.")
                return

            self.lbl_verify_status.setText("Đã lấy được khung hình rõ. Đang gửi xác minh...")
            self.lbl_verify_hint.setText("Hệ thống đang đối chiếu với ảnh gốc trên server.")
            QApplication.processEvents()

            files = [("files", (f"verify_{i}.jpg", b, "image/jpeg")) for i, b in enumerate(face_bytes)]
            r = requests.post(
                f"{self.api_url}/api/student/verify_identity",
                headers=self._auth_headers(),
                files=files,
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "success":
                self.verify_ok = True
                self.lbl_verify_status.setText("Xác minh danh tính thành công")
                self.lbl_verify_hint.setText("Bạn có thể vào phòng thi. AI sẽ tiếp tục giám sát trong suốt thời gian làm bài.")
                self.btn_enter_exam.setEnabled(True)
            else:
                confidence = data.get("confidence")
                threshold = data.get("threshold")
                if confidence is not None:
                    if threshold is not None:
                        self.lbl_verify_hint.setText(f"Độ khớp hiện tại: {confidence}. Ngưỡng yêu cầu: {threshold}. Hãy thử lại với ảnh gốc và ánh sáng gần giống hơn.")
                    else:
                        self.lbl_verify_hint.setText(f"Độ khớp hiện tại: {confidence}. Hãy thử lại với ảnh gốc và ánh sáng gần giống hơn.")
                else:
                    self.lbl_verify_hint.setText("Hãy thử lại và giữ khuôn mặt ở chính giữa camera.")
                self.lbl_verify_status.setText("Xác minh danh tính chưa đạt")
                self._show_dialog("Xác minh chưa đạt", data.get("message", "Xác minh khuôn mặt thất bại"))
        except requests.RequestException as exc:
            logger.exception("Identity verification request failed")
            self.lbl_verify_status.setText("Không thể xác minh do lỗi kết nối")
            self.lbl_verify_hint.setText("Kiểm tra server hoặc mạng nội bộ rồi thử lại.")
            QMessageBox.critical(self, "Lỗi", f"Kết nối thất bại: {exc}")
        except ValueError:
            logger.exception("Identity verification returned invalid JSON")
            QMessageBox.critical(self, "Lỗi", "Phản hồi từ server không hợp lệ")
        finally:
            self.btn_verify_identity.setEnabled(True)
            if timer_was_active and not self.verify_preview_timer.isActive() and self.verify_camera is not None and self.verify_camera.isOpened():
                self.verify_preview_timer.start(50)

    def enter_exam_room(self):
        if not self.verify_ok:
            return QMessageBox.warning(self, "Chưa xác minh", "Bạn cần xác minh danh tính trước khi vào phòng thi.")
        try:
            self.stop_verify_preview()
            # Give Windows camera stack a brief moment to release the verify handle.
            time.sleep(0.35)
            self._load_runtime_config()
            self._load_reference_face()
            self.start_ai_engines()
            self.exam_started_at = time.time()
            self.identity_grace_until = self.exam_started_at + self.identity_startup_grace_seconds
            self._set_monitor_feedback("Đang giám sát nền.", "Không có sự kiện đáng chú ý.")
            self.lbl_broadcast.hide()
            self.lbl_exam_status.hide()
            self._update_exam_timer()
            self.exam_clock_timer.start(1000)
            self.stack.setCurrentIndex(3)
            self.ui_load_quiz()
        except requests.RequestException as exc:
            self.start_verify_preview()
            logger.exception("Could not fetch reference face after verification")
            QMessageBox.critical(self, "Lỗi", f"Không tải được ảnh tham chiếu cho AI: {exc}")
        except ValueError as exc:
            self.start_verify_preview()
            logger.exception("Reference face decode failed after verification")
            QMessageBox.critical(self, "Lỗi", str(exc))

    def closeEvent(self, event):
        self.stop_verify_preview()
        try:
            self.shared_frame_shm.close()
            self.shared_frame_shm.unlink()
        except Exception:
            logger.debug("Shared frame buffer cleanup skipped", exc_info=True)
        super().closeEvent(event)

    def start_ai_engines(self):
        if self.ai_started:
            return
        self.ai_started = True
        self.camera_thread = threading.Thread(
            target=ai.camera_thread_worker,
            args=(self.frame_buffer_q,),
            kwargs={"display_q": self.display_q},
            daemon=True,
        )
        self.camera_thread.start()
        self.ai_process = ai.mp.Process(
            target=ai.ai_worker,
            args=(
                self.frame_q,
                self.tracker_q,
                ai.MODEL_PATH,
                self.frame_buffer_q,
                self.shared_frame_shm.name,
                self.shared_frame_shape,
                self.shared_frame_slots,
                self.shared_frame_ids,
            ),
            daemon=True,
        )
        self.ai_process.start()
        threading.Thread(target=ai.deepface_worker, args=(self.crop_q, self.alert_q, self.init_q), daemon=True).start()
        self.timer.start(50); self.sync_timer.start(3000); self.display_timer.start(25)

    def ui_load_quiz(self):
        if self.room_locked:
            return
        if not self.verify_ok:
            self._show_dialog("Chưa xác minh", "Hoàn tất xác minh danh tính trước khi vào bài thi.")
            return
        if self.quiz_loaded:
            return
        try:
            r = requests.get(f"{self.api_url}/api/student/quiz/{self.exam_id}", headers=self._auth_headers(), timeout=10)
            r.raise_for_status()
            data = r.json()
            if data.get('status') == 'success':
                self._clear_layout(self.quiz_inner)
                self.quiz_empty_state.hide()
                self.scroll.show()
                self.question_widgets.clear()
                self.question_cards.clear()
                for q in data["data"]:
                    q_id = str(q['question_id'])
                    gb = QGroupBox(f"Câu hỏi {q_id}"); gl = QVBoxLayout(gb)
                    gl.addWidget(QLabel(q['question_text']))
                    bg = QButtonGroup(self); self.question_widgets[q_id] = bg
                    self.question_cards[q_id] = gb
                    for k, v in [('A', q['option_a']), ('B', q['option_b']), ('C', q['option_c']), ('D', q['option_d'])]:
                        if v:
                            rb = QRadioButton(f"{k}. {v}"); rb.val = k
                            bg.addButton(rb); gl.addWidget(rb)
                    bg.buttonClicked.connect(self._apply_question_filter)
                    self.quiz_inner.addWidget(gb)
                self.question_total = len(data["data"])
                self.quiz_loaded = True
                self.btn_submit_exam.setEnabled(True)
                self._apply_question_filter()
            else:
                self.quiz_empty_state.show()
                self.scroll.hide()
                self.btn_submit_exam.setEnabled(False)
                self._show_dialog("Không tải được đề", data.get('message', 'Không tải được đề!'))
        except requests.RequestException:
            logger.exception("Quiz fetch failed")
            self.quiz_empty_state.show()
            self.scroll.hide()
            self.btn_submit_exam.setEnabled(False)
            self._show_dialog("Không tải được đề", "Kết nối tới server lấy đề thi thất bại.")
        except ValueError:
            logger.exception("Quiz fetch returned invalid JSON")
            self.btn_submit_exam.setEnabled(False)
            self._show_dialog("Phản hồi không hợp lệ", "Server trả về dữ liệu đề thi không hợp lệ.")

    def submit_action(self):
        ans = {qid: bg.checkedButton().val for qid, bg in self.question_widgets.items() if bg.checkedButton()}
        if not ans:
            return QMessageBox.warning(self, "Thiếu đáp án", "Bạn chưa chọn đáp án nào để nộp.")
        try:
            r = requests.post(
                f"{self.api_url}/api/student/submit",
                headers=self._auth_headers(),
                data={"answers_json": json.dumps(ans)},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            if data.get('status') == 'success':
                if data.get("already_submitted"):
                    QMessageBox.information(self, "Đã nộp trước đó", f"Bài thi đã được nộp trước đó. Điểm hiện tại: {data.get('score')}")
                else:
                    QMessageBox.information(self, "Xong", f"Điểm của bạn: {data.get('score')}")
                QApplication.quit()
            else:
                QMessageBox.warning(self, "Lỗi", data.get('message', 'Nộp bài thất bại!'))
        except requests.RequestException:
            logger.exception("Submit exam request failed")
            QMessageBox.warning(self, "Lỗi", "Nộp bài thất bại!")
        except ValueError:
            logger.exception("Submit exam returned invalid JSON")
            QMessageBox.warning(self, "Lỗi", "Phản hồi từ server không hợp lệ")

    def update_ui(self):
        latest_payload = None
        try:
            while True:
                latest_payload = self.tracker_q.get_nowait()
        except Empty:
            if latest_payload is None:
                if self.ai_started and self.ai_process is not None and not self.ai_process.is_alive():
                    self.exam_view.setText("AI worker đã dừng. Vui lòng thoát app và mở lại.")
                    self.lbl_ai_camera.setText("Camera: Không khả dụng (worker dừng)")
                return
        except Exception:
            logger.exception("Failed to read from tracker queue")
            return

        if isinstance(latest_payload, dict) and latest_payload.get("worker_error"):
            error_message = str(latest_payload.get("worker_error"))
            model_path = str(latest_payload.get("model_path") or "")
            self.exam_view.setText(error_message)
            self.lbl_ai_camera.setText("Camera: Không khả dụng (lỗi khởi tạo AI)")
            if model_path:
                self._set_monitor_feedback("Lỗi AI worker", f"Model path: {model_path}")
            else:
                self._set_monitor_feedback("Lỗi AI worker", error_message)
            return

        self._set_monitor_feedback("Đang giám sát nền.", "Không có sự kiện đáng chú ý.")

        if isinstance(latest_payload, dict):
            self.current_frame_id = int(latest_payload.get("frame_id", 0))
            frame = self._resolve_frame_from_payload(latest_payload)
            tracked = latest_payload.get("tracked", [])
            away = bool(latest_payload.get("away", False))
            self.pose_reliable = bool(latest_payload.get("pose_reliable", False))
            p = float(latest_payload.get("pitch", 0.0))
            y = float(latest_payload.get("yaw", 0.0))
            # Tầng 1 nghi vấn tai nghe: gửi crop lên server để giám thị chạy Tầng 2
            earpiece_suspects = latest_payload.get("earpiece_suspects", [])
        else:
            frame, tracked, away, p, y = latest_payload
            self.current_frame_id += 1
            self.pose_reliable = False
            earpiece_suspects = []

        if frame is None:
            return

        # Hạn chế gọi hàm thuộc tính lặp lại trong vòng xử lý UI nóng.
        is_track_identity_suspicious = self._is_track_identity_suspicious
        should_probe_track_identity = self._should_probe_track_identity
        should_probe_track_identity_periodically = self._should_probe_track_identity_periodically
        current_track_generation = self._current_track_generation
        queue_crop = self._queue_crop
        evaluate_identity_quality_gate = self._evaluate_identity_quality_gate
        start_identity_burst = self._start_identity_burst
        consume_identity_burst = self._consume_identity_burst

        tracked_lookup = {}
        active_person_tracks = set()
        phone_boxes = []
        for obj in tracked:
            class_name = obj.get("class", "")
            confidence = float(obj.get("confidence", 0.0))
            is_person = self._is_person_class(class_name)
            if is_person and confidence >= 0.45:
                track_id = int(obj["id"])
                active_person_tracks.add(track_id)
                tracked_lookup[track_id] = obj
                self.last_track_boxes[track_id] = tuple(int(v) for v in obj["box"])
            elif obj.get("is_phone") and confidence >= max(0.30, self.phone_min_confidence * 0.8):
                phone_boxes.append((tuple(int(v) for v in obj.get("box", (0, 0, 0, 0))), confidence))

        primary_track_id = self._select_primary_person_track(tracked_lookup, frame.shape)
        primary_person_box = tracked_lookup.get(primary_track_id, {}).get("box") if primary_track_id is not None else None
        valid_people_track_ids = self._get_valid_people_track_ids(tracked_lookup, primary_track_id, frame.shape)
        person_count = len(valid_people_track_ids)
        detected_person_tracks_count = len(active_person_tracks)
        identity_single_person_mode = detected_person_tracks_count <= 1
        now = time.time()
        stable_secondary_ids = self._update_stable_secondary_people(valid_people_track_ids, primary_track_id, now=now)
        stable_secondary_count = len(stable_secondary_ids)
        primary_face_visible = False
        if primary_person_box is not None:
            primary_face_visible = extract_face_roi(frame, person_box=primary_person_box) is not None
        if primary_face_visible:
            self.primary_face_last_seen_at = now
        elif person_count <= 0:
            self.primary_face_last_seen_at = 0.0
        self.identity_face_recently_visible = (
            person_count > 0
            and self.primary_face_last_seen_at > 0.0
            and (now - float(self.primary_face_last_seen_at)) <= float(self.identity_face_presence_grace_seconds)
        )

        phone_signal = self._compute_phone_signal(phone_boxes, primary_person_box, frame.shape)
        phone_candidate = phone_signal >= self.phone_signal_threshold

        filtered_earpiece_suspects, _earpiece_head_stats = self._filter_earpiece_suspects_by_head_region(
            earpiece_suspects,
            primary_person_box,
            frame.shape,
        )
        if filtered_earpiece_suspects and phone_signal >= (self.phone_signal_threshold + self.phone_earpiece_arbitration_margin):
            filtered_earpiece_suspects = []
        if filtered_earpiece_suspects and phone_signal < (self.phone_signal_threshold + 0.04):
            phone_candidate = False

        phone_streak_state = self._update_phone_streak(phone_candidate)
        phone_detected = phone_streak_state["confirmed"]
        smoothed_pitch, smoothed_yaw = self._update_pose_smoothing(p, y, primary_person_box is not None)
        self.head_pose_audit = bool(self.pose_reliable and away and person_count == 1)
        away_detected = False

        self._refresh_track_generations(active_person_tracks)

        while True:
            try:
                res = self.alert_q.get_nowait()
            except Empty:
                break
            except Exception:
                logger.exception("Failed to read from alert queue")
                break

            result_track_id = int(res.get("id", -1))
            result_generation = int(res.get("track_generation", 0))
            if result_track_id >= 0 and not self._is_identity_alert_current(res, tracked_lookup):
                self.pending_identity_tracks.discard(self._track_key(result_track_id, result_generation))
                continue

            final_status = self._record_identity_result(res)
            if final_status == "THI SINH":
                self._set_monitor_feedback("Đang giám sát danh tính", "AI xác nhận đúng thí sinh trong khung hình.")
            elif final_status == "KE DOT NHAP":
                intruder_count = self._get_recent_intruder_probe_count(result_track_id)
                self._refresh_intruder_ratio(active_person_tracks)
                if identity_single_person_mode:
                    self._set_monitor_feedback(
                        "Cần giám thị xem xét danh tính",
                        f"Đã ghi nhận {intruder_count}/{self.intruder_policy['mismatch_votes']} probe mismatch trong 10 giây; yêu cầu giám thị xác minh.",
                    )
                    self._queue_snapshot_upload("identity", frame=frame, reason="Nghi vấn danh tính hoặc đổi người trong khung hình.")
                    self._record_violation("Nguoi la xuat hien", frame, cooldown_seconds=15)
                else:
                    self._set_monitor_feedback(
                        "Cần giám thị xem xét nhiều người",
                        f"Đang có {detected_person_tracks_count} người trong khung hình; ưu tiên xử lý vi phạm nhiều người trước khi đánh dấu người lạ.",
                    )
            elif final_status == "CAN XAC MINH":
                if identity_single_person_mode:
                    self._set_monitor_feedback("Cần giám thị xem xét danh tính", "Có khuôn mặt chưa đủ chắc chắn để kết luận. Hệ thống tiếp tục đối chiếu nền.")
                    self._queue_snapshot_upload("identity", frame=frame, reason="Danh tính cần giám thị xác minh thêm.")
                    _intruder_hint_count = self._get_recent_intruder_probe_count(result_track_id)
                    if _intruder_hint_count >= 2:
                        self._show_soft_hint(2, "⚠️ Vui lòng điều chỉnh góc ngồi và đảm bảo khuôn mặt hiển thị rõ trong camera.")
                    elif _intruder_hint_count >= 1:
                        self._show_soft_hint(1, "Ọ Hệ thống chưa xác nhận được danh tính. Vui lòng nhìn thẳng vào camera.")
                else:
                    self._set_monitor_feedback(
                        "Cần giám thị xem xét nhiều người",
                        f"Đang có {detected_person_tracks_count} người trong khung hình; tạm bỏ qua nhắc nhở danh tính để ưu tiên vi phạm nhiều người.",
                    )

        _ring_ts = time.time()
        self._last_overlay_boxes = []
        for o in tracked:
            class_name = o.get("class", "")
            confidence = float(o.get("confidence", 0.0))
            is_person = self._is_person_class(class_name)
            fallback_label = str(class_name) if not is_person else "Checking..."
            if is_person and o["id"] not in self.identity_dict and o["id"] not in self.identity_votes:
                self._attempt_track_handover(o["id"], o["box"])
            st = self._get_track_display_status(o["id"], fallback_label)
            if is_person and o["id"] == primary_track_id and st == "THI SINH" and not self.identity_face_recently_visible:
                st = "CAN XAC MINH"
            if st == "THI SINH":
                clr = (0, 255, 0)
            elif st == "CAN XAC MINH":
                clr = (0, 215, 255)
            else:
                clr = (0, 0, 255)
            self._last_overlay_boxes.append(([int(c) for c in o["box"]], st, clr))

            if is_person:
                vote_state = self.identity_votes.get(o["id"], {"samples": 0})
                track_status = self.identity_dict.get(o["id"])
                suspicious_probe_needed = identity_single_person_mode and (
                    track_status in {"CAN XAC MINH", "KE DOT NHAP"}
                    or vote_state.get("intruder", 0) > 0
                    or vote_state.get("uncertain", 0) > 0
                    or is_track_identity_suspicious(o["id"])
                )
                periodic_probe_needed = identity_single_person_mode and track_status == "THI SINH"
                initial_probe_needed = identity_single_person_mode and track_status is None and vote_state.get("samples", 0) < self.identity_probe_limit
                if suspicious_probe_needed and should_probe_track_identity_periodically(o["id"], suspicious=True):
                    start_identity_burst(o["id"])

                burst_probe_needed = identity_single_person_mode and int(self.identity_burst_remaining.get(int(o["id"]), 0)) > 0
                should_attempt_probe = (
                    (initial_probe_needed and should_probe_track_identity(o["id"]))
                    or burst_probe_needed
                    or (periodic_probe_needed and should_probe_track_identity_periodically(o["id"], suspicious=False))
                )
                if should_attempt_probe:
                    quality_gate = evaluate_identity_quality_gate(
                        frame,
                        o["box"],
                        person_count,
                        pitch=smoothed_pitch,
                        yaw=smoothed_yaw,
                        allow_full_frame_fallback=(identity_single_person_mode and not burst_probe_needed),
                    )
                    face_data = quality_gate.get("face_data") if isinstance(quality_gate, dict) else None
                    if face_data and quality_gate.get("passed"):
                        queued = queue_crop(face_data["face"], o["id"], self.current_frame_id, current_track_generation(o["id"]), o["box"])
                        if queued and burst_probe_needed:
                            consume_identity_burst(o["id"])
                    elif burst_probe_needed:
                        consume_identity_burst(o["id"])
                        if quality_gate.get("reason") in {"face_missing", "face_small", "pose", "person_count"}:
                            self.identity_dict[o["id"]] = "CAN XAC MINH"

        self._cleanup_identity_state(active_person_tracks)
        self._refresh_intruder_ratio(active_person_tracks, now=now)
        if detected_person_tracks_count > 1:
            self._set_violation_ratio("intruder", 0.0)

        phone_state = self._update_temporal_presence("phone", phone_detected, now=now)
        if phone_detected:
            self._set_monitor_feedback(
                "Cần giám thị xem xét thiết bị",
                f"Điện thoại nghi vấn (streak {phone_streak_state['streak']}/{self.phone_min_streak_frames}): liên tục {phone_state['continuous_seconds']:.1f}s, cộng dồn {phone_state['cumulative_seconds']:.1f}s/5.0s.",
            )
            self._queue_snapshot_upload("phone", frame=frame, reason="Phát hiện vật thể nghi là điện thoại.")
            if phone_state["ready"]:
                self._record_violation("Su dung cell phone", frame, cooldown_seconds=12)
            elif phone_state["continuous_seconds"] >= 1.0:
                self._show_soft_hint(2, "⚠️ Hệ thống đang ghi nhận thiết bị điện tử. Vui lòng cất ngay để tránh ảnh hưởng kết quả thi.")
            elif phone_state["continuous_seconds"] >= 0.5:
                self._show_soft_hint(1, "Ọ Vui lòng đặt thiết bị điện tử xuống bàn.")
        elif phone_candidate:
            self._set_monitor_feedback(
                "Đang xác nhận điện thoại",
                f"Nghi vấn điện thoại: {phone_streak_state['streak']}/{self.phone_min_streak_frames} frame liên tiếp.",
            )
        else:
            self._update_temporal_presence("phone", False, now=now)

        # Tai nghe — Tầng 1: chỉ chuyển tiếp khi nghi vấn lặp lại đủ nhanh trong cửa sổ ngắn.
        earpiece_detected = False
        if filtered_earpiece_suspects and frame is not None:
            earpiece_state = self._update_earpiece_tier1_state(filtered_earpiece_suspects, now=now)
            earpiece_detected = bool(earpiece_state["hit_count"] >= int(self.earpiece_tier1_policy["min_hits"]))
            if earpiece_state["ready"]:
                self._set_monitor_feedback(
                    "Cần giám thị xem xét tai nghe",
                    f"Tầng 1 phát hiện tai nghe gần vùng đầu {earpiece_state['hit_count']} lần; chuyển Tầng 2 để xác minh.",
                )
                self._upload_earpiece_suspects(filtered_earpiece_suspects, frame)
            elif earpiece_state["hit_count"] >= 1:
                self._show_soft_hint(1, "Ọ Vui lòng đảm bảo không đeo phụ kiện tai trong khu vực thi.")
        else:
            self._update_earpiece_tier1_state([], now=now)

        people_active = stable_secondary_count > 0
        people_state = self._update_temporal_presence("multiple_people", people_active, now=now)
        if stable_secondary_count > 0:
            extra_people = max(0, stable_secondary_count)
            self._set_monitor_feedback(
                "Cần giám thị xem xét nhiều người",
                f"Có {extra_people + 1} người ổn định: liên tục {people_state['continuous_seconds']:.1f}s, cộng dồn {people_state['cumulative_seconds']:.1f}s/3.0s.",
            )
            self._queue_snapshot_upload("multiple_people", frame=frame, reason="Phát hiện nhiều người trong khung hình.")
            if people_state["ready"]:
                self._record_violation("Phat hien nhieu nguoi", frame, cooldown_seconds=15)
            elif people_state["continuous_seconds"] >= 0.7:
                self._show_soft_hint(2, "⚠️ Phát hiện thêm người trong khu vực thi. Hệ thống sẽ ghi nhận nếu tiếp tục.")
            elif people_state["continuous_seconds"] >= 0.4:
                self._show_soft_hint(1, "Ọ Vui lòng đảm bảo chỉ có bạn trong khung hình camera.")
        else:
            self._update_temporal_presence("multiple_people", False, now=now)

        self.behavior_counters["away"] = 0
        self._set_violation_ratio("away", 0.0)

        risk_text, risk_ratio = self._compute_risk_level()
        if risk_ratio >= 1.0:
            self._queue_snapshot_upload("high_risk", frame=frame, reason=f"Mức rủi ro {risk_text.lower()} vượt ngưỡng giám sát.")

        # Vẽ box lên bản sao frame để ghi vào ring buffer bằng chứng — không hiển thị cho user.
        if self._last_overlay_boxes:
            evidence_frame = frame.copy()
            for box, label, clr in self._last_overlay_boxes:
                cv2.rectangle(evidence_frame, (box[0], box[1]), (box[2], box[3]), clr, 2)
                cv2.putText(evidence_frame, label, (box[0], max(20, box[1] - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, clr, 2)
        else:
            evidence_frame = frame
        self._push_to_ring_buffer(evidence_frame, _ring_ts)

        monitor_people_count = max(int(person_count), int(detected_person_tracks_count))
        self._update_monitor_state(frame, active_person_tracks, monitor_people_count, phone_detected, away_detected, earpiece_detected, smoothed_pitch, smoothed_yaw)

    def sync_with_server(self):
        if self.exam_id is None or not self.session_token:
            return
        self._flush_offline_queue(max_jobs=4)
        self._queue_monitor_snapshot()
        try:
            r = requests.get(f"{self.api_url}/api/student/sync/{self.exam_id}", headers=self._auth_headers(), timeout=5)
            r.raise_for_status()
            data = r.json()
            self._set_network_status(True)
            if data.get("status") == "error":
                logger.warning("Student sync rejected: %s", data)
                return
            if data.get("require_snapshot"):
                self.snapshot_request_pending = True
                self._queue_snapshot_upload(
                    "proctor_request",
                    reason=data.get("snapshot_reason", "Giám thị yêu cầu ảnh snapshot mới nhất."),
                    force=True,
                )
            if data.get("locked"):
                self._handle_room_locked(data.get("broadcast", "Phòng thi đã bị khóa."))
        except requests.RequestException:
            logger.exception("Sync with server failed")
            self._set_network_status(False, "không kết nối được endpoint đồng bộ")
        except ValueError:
            logger.exception("Sync endpoint returned invalid JSON")

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    app = QApplication(sys.argv); win = ExamMonitorApp(); win.show(); sys.exit(app.exec_())