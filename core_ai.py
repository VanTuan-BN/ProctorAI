import cv2, time, logging, os, sys
import numpy as np
import multiprocessing as mp
from multiprocessing import shared_memory
from queue import Empty, Full
from pathlib import Path


def _ensure_console_streams():
    """PyInstaller --windowed can set stdio streams to None in child processes."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8", buffering=1)


_ensure_console_streams()

# Mitigate OpenMP duplicate runtime abort (libiomp5md.dll) when combining
# Torch, NumPy/Scipy, and MediaPipe wheels on Windows.
if os.name == "nt":
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ.setdefault("OMP_NUM_THREADS", "1")

from face_verifier import DEFAULT_FACE_THRESHOLD, build_reference_signature, compare_with_signature
from runtime_env import configure_windows_dll_paths

configure_windows_dll_paths()

MODEL_PATH = "models/best_onlyA.pt"
TIER2_MODEL_PATH = "models/Model_ft_person_earpiece.pt"
INFER_IMGSZ = 640  # Giữ tracking tổng quát ở 640 để ổn định tốc độ realtime
TIER1_SCAN_IMGSZ = 960  # Tăng nhẹ độ phân giải riêng cho scan earpiece để cải thiện recall
PERSON_TRACK_CONF = 0.45
PHONE_TRACK_CONF = 0.35
EARPIECE_TRACK_CONF = 0.16
TRACK_CONF = min(PERSON_TRACK_CONF, PHONE_TRACK_CONF, EARPIECE_TRACK_CONF)
TRACK_IOU = 0.3
# ─── Two-tier Hard Negative Mining constants ───────────────────────────────
# Tầng 1 (best_onlyA): quét nhanh với ngưỡng thấp → ưu tiên Recall
# Tầng 2 (Model_ft_person_earpiece): xác nhận sâu → ưu tiên Precision
TIER1_SCAN_CONF = 0.16     # Tầng 1: hạ nhẹ ngưỡng để ưu tiên recall/f1 cho nhánh earpiece
TIER2_VERIFY_CONF = 0.35   # Tầng 2: chốt kết quả (high precision)
EARPIECE_SCAN_INTERVAL = 3 # Quét Tầng 1 mỗi N frame để tiết kiệm CPU
# ───────────────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
PERSON_LABEL_TOKENS = ("person",)
PHONE_LABEL_TOKENS = ("phone", "cell")
EARPIECE_LABEL_TOKENS = ("earpiece",)
CAMERA_FRAME_SHAPE = (480, 640, 3)
SHARED_FRAME_SLOTS = 4

# ============================================
# CAMERA THREAD: Chỉ lấy frame mà không xử lý
# ============================================
camera_frame_buffer = None  # Sẽ được gán từ main_ui


def _resolve_runtime_path(rel_path):
    """Resolve file path reliably for source run and PyInstaller frozen run."""
    rel = str(rel_path).replace("/", os.sep).replace("\\", os.sep)
    candidates = []

    # 1) Current working directory
    candidates.append(Path.cwd() / rel)

    # 2) Directory containing executable/script
    try:
        candidates.append(Path(sys.executable).resolve().parent / rel)
    except Exception:
        pass

    # 3) Directory containing this module file
    try:
        candidates.append(Path(__file__).resolve().parent / rel)
    except Exception:
        pass

    # 4) PyInstaller temporary extraction dir (_MEIPASS)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / rel)

    for path in candidates:
        if path.exists():
            return str(path)

    # Keep original relative path as final fallback for better diagnostics.
    return str(Path(rel))


def _normalize_pose_angle(angle):
    normalized = float(angle)
    while normalized <= -180.0:
        normalized += 360.0
    while normalized > 180.0:
        normalized -= 360.0
    if normalized > 90.0:
        normalized -= 180.0
    elif normalized < -90.0:
        normalized += 180.0
    return normalized


def _safe_class_name(model_names, class_id):
    try:
        if isinstance(model_names, dict):
            return str(model_names.get(int(class_id), class_id))
        if isinstance(model_names, list):
            return str(model_names[int(class_id)])
    except Exception:
        pass
    return str(class_id)


def _find_class_id(model_names, tokens):
    """Tự động tìm class_id trong model.names khớp với từ khóa (tokens).
    Trả về int class_id hoặc None nếu không tìm thấy.
    """
    try:
        items = model_names.items() if isinstance(model_names, dict) else enumerate(model_names)
        for cid, name in items:
            if any(tok in str(name).lower() for tok in tokens):
                return int(cid)
    except Exception:
        pass
    return None


def camera_thread_worker(frame_buffer_q, target_fps=30, display_q=None):
    """Luồng camera: capture frame nhanh, không xử lý AI"""
    while True:
        cap = cv2.VideoCapture(0, cv2.CAP_MSMF)
        if not cap.isOpened():
            logger.warning("CAP_MSMF failed; falling back to CAP_DSHOW")
            cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not cap.isOpened():
            logger.error("Camera could not be opened; retrying...")
            time.sleep(1)
            continue

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, target_fps)
        frame_delay = 1.0 / target_fps
        last_time = time.time()
        logger.info("Camera thread started")

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    logger.warning("Camera frame grab failed; reopening camera")
                    break

                try:
                    frame_buffer_q.put_nowait(frame)
                except Full:
                    try:
                        frame_buffer_q.get_nowait()
                    except Empty:
                        pass
                    try:
                        frame_buffer_q.put_nowait(frame)
                    except Full:
                        logger.debug("Frame buffer still full after dropping oldest frame")

                # Also push raw frame to display queue (same-process thread, no serialization).
                if display_q is not None:
                    try:
                        display_q.put_nowait(frame)
                    except Full:
                        try:
                            display_q.get_nowait()
                        except Empty:
                            pass
                        try:
                            display_q.put_nowait(frame)
                        except Full:
                            pass

                elapsed = time.time() - last_time
                sleep_time = max(0, frame_delay - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                last_time = time.time()
        finally:
            cap.release()

def get_head_pose(frame, landmarks):
    h, w, _ = frame.shape
    face_2d = np.array([[int(landmarks.landmark[i].x * w), int(landmarks.landmark[i].y * h)] for i in [1, 152, 226, 446, 57, 287]], dtype=np.float64)
    face_3d = np.array([(0,0,0), (0,-330,-65), (-225,170,-135), (225,170,-135), (-150,-150,-125), (150,-150,-125)], dtype=np.float64)
    cam_matrix = np.array([[w, 0, h/2], [0, w, w/2], [0, 0, 1]], dtype=np.float64)
    success, rot_vec, _ = cv2.solvePnP(face_3d, face_2d, cam_matrix, np.zeros((4, 1)))
    if not success:
        return False, 0.0, 0.0, False

    rotation_matrix = cv2.Rodrigues(rot_vec)[0]
    angles, _, _, _, _, _ = cv2.RQDecomp3x3(rotation_matrix)
    pitch = _normalize_pose_angle(angles[0])
    yaw = _normalize_pose_angle(angles[1])

    left_face = np.array([landmarks.landmark[226].x * w, landmarks.landmark[226].y * h], dtype=np.float64)
    right_face = np.array([landmarks.landmark[446].x * w, landmarks.landmark[446].y * h], dtype=np.float64)
    chin = np.array([landmarks.landmark[152].x * w, landmarks.landmark[152].y * h], dtype=np.float64)
    nose = np.array([landmarks.landmark[1].x * w, landmarks.landmark[1].y * h], dtype=np.float64)
    face_width = float(np.linalg.norm(right_face - left_face))
    face_height = float(np.linalg.norm(chin - nose))
    pose_reliable = face_width >= (w * 0.18) and face_height >= (h * 0.16)
    audit_away = bool(pose_reliable and (abs(yaw) >= 28.0 or abs(pitch) >= 20.0))
    return audit_away, pitch, yaw, pose_reliable

def ai_worker(frame_q, tracker_q, model_path, frame_buffer_q, shared_frame_name=None, shared_frame_shape=None, shared_frame_slots=SHARED_FRAME_SLOTS, shared_frame_ids=None):
    """YOLO + MediaPipe worker: Lấy frame từ camera buffer, không capture"""
    logger.info("AI worker started")
    try:
        from ultralytics import YOLO
    except Exception as exc:
        logger.exception("Failed to import Ultralytics in AI worker")
        try:
            tracker_q.put_nowait({"worker_error": f"Không nạp được Ultralytics/Torch: {exc}"})
        except Exception:
            pass
        return
    try:
        import mediapipe as mp_vision
    except Exception as exc:
        logger.exception("Failed to import MediaPipe in AI worker")
        try:
            tracker_q.put_nowait({"worker_error": f"Không nạp được MediaPipe: {exc}"})
        except Exception:
            pass
        return
    import torch
    resolved_model_path = _resolve_runtime_path(model_path)
    logger.info("Loading primary YOLO model from: %s", resolved_model_path)
    try:
        primary_model = YOLO(resolved_model_path)
    except Exception as exc:
        logger.exception("Failed to load primary YOLO model")
        try:
            tracker_q.put_nowait({
                "worker_error": f"Không tải được model AI: {exc}",
                "model_path": str(resolved_model_path),
            })
        except Exception:
            pass
        return
    # Chỉ tải Tầng 1 (best_onlyA) — Tầng 2 chạy ở phía giám thị
    tier1_earpiece_cid = _find_class_id(primary_model.names, EARPIECE_LABEL_TOKENS)
    logger.info("Earpiece class ID (Tier1 only): %s", tier1_earpiece_cid)
    shared_frame = None
    shared_frame_view = None
    if shared_frame_name and shared_frame_shape:
        try:
            shared_frame = shared_memory.SharedMemory(name=shared_frame_name)
            shared_frame_view = np.ndarray((int(shared_frame_slots), *tuple(shared_frame_shape)), dtype=np.uint8, buffer=shared_frame.buf)
        except Exception:
            logger.exception("Could not attach to shared frame buffer")
            shared_frame = None
            shared_frame_view = None
    device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        primary_model.to(device)
        logger.info("Primary YOLO loaded on %s", device)
    except Exception as e:
        logger.warning("Could not move YOLO model to %s: %s", device, e)
    try:
        face_mesh = mp_vision.solutions.face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=1,
            refine_landmarks=False,
        )
    except Exception as exc:
        logger.exception("Failed to initialize MediaPipe FaceMesh")
        try:
            tracker_q.put_nowait({"worker_error": f"Không khởi tạo được FaceMesh: {exc}"})
        except Exception:
            pass
        return
    frame_count = 0
    
    while True:
        # Chờ frame mới ngắn hạn để tránh busy-wait đốt CPU khi queue rỗng.
        try:
            frame = frame_buffer_q.get(timeout=0.05)
        except Empty:
            continue
        except Exception:
            logger.exception("Failed to read from frame buffer queue")
            continue

        # Luôn xử lý frame mới nhất đang có để giảm độ trễ tích lũy khi model bận.
        while True:
            try:
                frame = frame_buffer_q.get_nowait()
            except Empty:
                break
            except Exception:
                logger.exception("Failed to drain frame buffer queue")
                break
        
        frame_count += 1
        
        # 1. MediaPipe: head-pose mỗi 2 frame
        is_away, p, y, pose_reliable = False, 0, 0, False
        if frame_count % 2 == 0:
            try:
                res_mesh = face_mesh.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                if res_mesh.multi_face_landmarks: 
                    is_away, p, y, pose_reliable = get_head_pose(frame, res_mesh.multi_face_landmarks[0])
            except Exception:
                logger.exception("MediaPipe head-pose inference failed")
        
        # 2. YOLO primary tracking (best_onlyA, imgsz=960)
        try:
            if device == "cuda":
                try:
                    with torch.inference_mode(), torch.cuda.amp.autocast():
                        results = primary_model.track(frame, persist=True, conf=TRACK_CONF, iou=TRACK_IOU, imgsz=INFER_IMGSZ, verbose=False)
                except Exception:
                    with torch.inference_mode():
                        results = primary_model.track(frame, persist=True, conf=TRACK_CONF, iou=TRACK_IOU, imgsz=INFER_IMGSZ, verbose=False)
            else:
                with torch.inference_mode():
                    results = primary_model.track(frame, persist=True, conf=TRACK_CONF, iou=TRACK_IOU, imgsz=INFER_IMGSZ, verbose=False)
        except Exception as e:
            results = None
            logger.exception("Primary YOLO tracking failed: %s", e)

        tracked = []
        if results and results[0].boxes.id is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            clss = results[0].boxes.cls.cpu().numpy()
            track_ids = results[0].boxes.id.int().cpu().numpy()
            confs = results[0].boxes.conf.cpu().numpy()
            
            for b, c, tid, conf in zip(boxes, clss, track_ids, confs):
                label = _safe_class_name(primary_model.names, int(c))
                label_lc = str(label).lower()
                conf_f = float(conf)
                is_person_label = any(token in label_lc for token in PERSON_LABEL_TOKENS)
                is_phone_label = any(token in label_lc for token in PHONE_LABEL_TOKENS)
                is_earpiece_label = any(token in label_lc for token in EARPIECE_LABEL_TOKENS)

                # Áp ngưỡng riêng theo class cho Tầng 1.
                if is_person_label and conf_f < PERSON_TRACK_CONF:
                    continue
                if is_phone_label and conf_f < PHONE_TRACK_CONF:
                    continue
                if is_earpiece_label and conf_f < EARPIECE_TRACK_CONF:
                    continue

                box_int = b.astype(int)
                tracked.append(
                    {
                        "id": int(tid),
                        "class": label,
                        "box": box_int,
                        "confidence": conf_f,
                        "is_phone": is_phone_label,
                        "is_earpiece": is_earpiece_label,
                    }
                )

        # 3. Tầng 1 — Quét tai nghe nhanh (Recall ưu tiên)
        # Tầng 2 chạy ở phía giám thị sau khi nhận crop gửi lên server.
        earpiece_suspects = []
        if tier1_earpiece_cid is not None and frame_count % EARPIECE_SCAN_INTERVAL == 0:
            try:
                with torch.inference_mode():
                    scan_res = primary_model.predict(
                        frame, conf=TIER1_SCAN_CONF, imgsz=TIER1_SCAN_IMGSZ, verbose=False
                    )
                if scan_res and scan_res[0].boxes is not None:
                    for box in scan_res[0].boxes:
                        if int(box.cls) == tier1_earpiece_cid:
                            earpiece_suspects.append({
                                "box": box.xyxy[0].cpu().numpy().astype(int).tolist(),
                                "conf_tier1": float(box.conf),
                            })
            except Exception:
                logger.exception("Tier 1 earpiece scan failed")
            if earpiece_suspects:
                logger.debug("Tier 1 earpiece suspects: %d", len(earpiece_suspects))

        # 4. Đẩy kết quả ra UI cùng frame metadata để đồng bộ alert trả về
        try:
            payload = {
                "frame_id": int(frame_count),
                "frame_ts": time.time(),
                "tracked": tracked,
                "earpiece_suspects": earpiece_suspects,
                "away": bool(is_away),
                "pose_reliable": bool(pose_reliable),
                "pitch": float(p),
                "yaw": float(y),
            }
            if shared_frame_view is not None and shared_frame_ids is not None:
                slot_index = int(frame_count % int(shared_frame_slots))
                shared_frame_view[slot_index][:] = frame
                shared_frame_ids[slot_index] = int(frame_count)
                payload["shared_slot"] = slot_index
            else:
                payload["frame"] = frame
            tracker_q.put_nowait(
                payload
            )
        except Full:
            logger.debug("Tracker queue full; dropping tracking result")

def deepface_worker(crop_q, alert_q, init_q):
    logger.info("Initializing face verification worker")
    ref_signatures = {}
    face_thresholds = {}
    
    while True:
        # Trên Windows, multiprocessing.Queue.empty() không đáng tin cậy.
        while True:
            try:
                msv_dict = init_q.get_nowait()  # {'MSV': ảnh_cv2}
            except Empty:
                break
            except Exception:
                logger.exception("Failed to read from init queue")
                break

            if isinstance(msv_dict, dict) and "student_id" in msv_dict:
                student_id = msv_dict.get("student_id")
                reference_images = msv_dict.get("reference_images")
                img_cv2 = msv_dict.get("reference_image")
                face_threshold = float(msv_dict.get("face_threshold", DEFAULT_FACE_THRESHOLD))
                try:
                    normalized_signatures = []
                    if isinstance(reference_images, list) and reference_images:
                        for reference_image in reference_images:
                            signature = build_reference_signature(reference_image)
                            if signature is not None:
                                normalized_signatures.append(signature)
                    elif img_cv2 is not None:
                        signature = build_reference_signature(img_cv2)
                        if signature is not None:
                            normalized_signatures.append(signature)

                    if not normalized_signatures:
                        logger.warning("Reference face for %s could not be normalized", student_id)
                        continue
                    ref_signatures[student_id] = normalized_signatures
                    face_thresholds[student_id] = face_threshold
                    logger.info("Cached %s face signatures for %s with threshold %.2f", len(normalized_signatures), student_id, face_threshold)
                except Exception as e:
                    logger.exception("Failed to cache face signature for %s: %s", student_id, e)
                continue

            for msv, img_cv2 in msv_dict.items():
                try:
                    signature = build_reference_signature(img_cv2)
                    if signature is None:
                        logger.warning("Reference face for %s could not be normalized", msv)
                        continue
                    ref_signatures[msv] = [signature]
                    face_thresholds[msv] = DEFAULT_FACE_THRESHOLD
                    logger.info("Cached face signature for %s", msv)
                except Exception as e:
                    logger.exception("Failed to cache face signature for %s: %s", msv, e)
                    
        try:
            payload = crop_q.get(timeout=0.05)
        except Empty:
            continue
        except Exception:
            logger.exception("Failed to read from crop queue")
            continue

        if isinstance(payload, dict):
            img = payload.get("crop")
            fid = int(payload.get("track_id", -1))
            sid = payload.get("student_id")
            frame_id = int(payload.get("frame_id", 0))
            track_generation = int(payload.get("track_generation", 0))
            source_box = payload.get("box")
            assume_face_roi = bool(payload.get("is_face_roi", False))
        else:
            img, fid, sid = payload
            frame_id = 0
            track_generation = 0
            source_box = None
            assume_face_roi = False

        reference_signatures = ref_signatures.get(sid)
        if not reference_signatures:
            continue
        face_threshold = float(face_thresholds.get(sid, DEFAULT_FACE_THRESHOLD))

        try:
            verified = False
            confidence = 0.0
            for reference_signature in reference_signatures:
                candidate_verified, candidate_confidence = compare_with_signature(
                    img,
                    reference_signature,
                    threshold=face_threshold,
                    assume_face_roi=assume_face_roi,
                )
                if candidate_verified or float(candidate_confidence) > float(confidence):
                    verified, confidence = candidate_verified, candidate_confidence
            status = "THI SINH" if verified else "KE DOT NHAP"
            try:
                alert_q.put_nowait(
                    {
                        "id": fid,
                        "status": status,
                        "confidence": float(confidence),
                        "frame_id": frame_id,
                        "track_generation": track_generation,
                        "box": tuple(int(v) for v in source_box) if source_box is not None else None,
                    }
                )
            except Full:
                logger.debug("Alert queue full; dropping identity result for %s", fid)
        except Exception:
            logger.exception("Failed to compare crop for track %s", fid)