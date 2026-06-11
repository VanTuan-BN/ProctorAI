import cv2
import numpy as np
from deepface import DeepFace

from runtime_env import configure_windows_dll_paths


configure_windows_dll_paths()

_MODEL_NAME = "ArcFace"
_DETECTOR_BACKEND = "opencv"
_DISTANCE_METRIC = "cosine"
_ALIGN_FACE = True
DEFAULT_FACE_THRESHOLD = 0.75
MIN_FACE_THRESHOLD = 0.70
MAX_FACE_THRESHOLD = 0.99
_FACE_IMAGE_SIZE = (224, 224)
_MIN_FACE_SIZE = 60
_MIN_FOCUS_SCORE = 20.0
_FACE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def _normalize_face_image(image):
    if image is None or getattr(image, "size", 0) == 0:
        return None

    normalized = np.asarray(image)
    if normalized.dtype != np.uint8:
        normalized = np.clip(normalized, 0, 255).astype(np.uint8)

    if normalized.ndim == 2:
        normalized = cv2.cvtColor(normalized, cv2.COLOR_GRAY2BGR)
    elif normalized.ndim == 3 and normalized.shape[2] == 4:
        normalized = cv2.cvtColor(normalized, cv2.COLOR_BGRA2BGR)

    return normalized


def _face_focus_score(face_image):
    gray = cv2.cvtColor(face_image, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def extract_face_roi(image, person_box=None, min_face_size=_MIN_FACE_SIZE, min_focus_score=_MIN_FOCUS_SCORE):
    normalized = _normalize_face_image(image)
    if normalized is None:
        return None

    search_image = normalized
    offset_x = 0
    offset_y = 0
    if person_box is not None:
        x1, y1, x2, y2 = [int(v) for v in person_box]
        box_width = max(1, x2 - x1)
        box_height = max(1, y2 - y1)
        expand_x = int(box_width * 0.18)
        expand_top = int(box_height * 0.28)
        expand_bottom = int(box_height * 0.12)
        x1 -= expand_x
        x2 += expand_x
        y1 -= expand_top
        y2 += expand_bottom
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(normalized.shape[1], x2)
        y2 = min(normalized.shape[0], y2)
        if x2 <= x1 or y2 <= y1:
            return None
        search_image = normalized[y1:y2, x1:x2]
        offset_x = x1
        offset_y = y1

    gray = cv2.cvtColor(search_image, cv2.COLOR_BGR2GRAY)
    faces = _FACE_CASCADE.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(int(min_face_size), int(min_face_size)),
    )
    if len(faces) == 0:
        return None

    frame_center_x = search_image.shape[1] / 2.0
    def _score(candidate):
        x, y, width, height = [int(v) for v in candidate]
        center_x = x + (width / 2.0)
        center_penalty = abs(center_x - frame_center_x)
        upper_bias = max(0.0, y - (search_image.shape[0] * 0.45))
        return (width * height) - (center_penalty * 2.5) - (upper_bias * 3.0)

    x, y, width, height = max(faces, key=_score)
    pad_x = int(width * 0.22)
    pad_top = int(height * 0.30)
    pad_bottom = int(height * 0.18)
    crop_x1 = max(0, x - pad_x)
    crop_y1 = max(0, y - pad_top)
    crop_x2 = min(search_image.shape[1], x + width + pad_x)
    crop_y2 = min(search_image.shape[0], y + height + pad_bottom)
    face_roi = search_image[crop_y1:crop_y2, crop_x1:crop_x2]
    if face_roi.size == 0:
        return None

    focus_score = _face_focus_score(face_roi)
    if focus_score < float(min_focus_score):
        return None

    standardized = cv2.resize(face_roi, _FACE_IMAGE_SIZE, interpolation=cv2.INTER_AREA)
    return {
        "face": standardized,
        "box": (
            int(crop_x1 + offset_x),
            int(crop_y1 + offset_y),
            int(crop_x2 + offset_x),
            int(crop_y2 + offset_y),
        ),
        "focus_score": focus_score,
    }


def _extract_embedding(image, assume_face_roi=False):
    if image is None or getattr(image, "size", 0) == 0:
        return None

    if assume_face_roi:
        prepared_face = _normalize_face_image(image)
        if prepared_face is None:
            return None
        prepared_face = cv2.resize(prepared_face, _FACE_IMAGE_SIZE, interpolation=cv2.INTER_AREA)
    else:
        face_data = extract_face_roi(image)
        if face_data is None:
            return None
        prepared_face = face_data["face"]

    representations = DeepFace.represent(
        img_path=prepared_face,
        model_name=_MODEL_NAME,
        detector_backend=_DETECTOR_BACKEND,
        enforce_detection=False,
        align=_ALIGN_FACE,
        normalization="base",
    )
    if not representations:
        return None

    embedding = representations[0].get("embedding")
    if not embedding:
        return None

    return np.asarray(embedding, dtype=np.float32)


def _cosine_distance(embedding_a, embedding_b):
    norm_a = float(np.linalg.norm(embedding_a))
    norm_b = float(np.linalg.norm(embedding_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0

    cosine_similarity = float(np.dot(embedding_a, embedding_b) / (norm_a * norm_b))
    cosine_similarity = max(min(cosine_similarity, 1.0), -1.0)
    return 1.0 - cosine_similarity


def build_reference_signature(image, assume_face_roi=False):
    embedding = _extract_embedding(image, assume_face_roi=assume_face_roi)
    if embedding is None:
        return None

    return {
        "embedding": embedding,
        "model_name": _MODEL_NAME,
        "detector_backend": _DETECTOR_BACKEND,
        "distance_metric": _DISTANCE_METRIC,
    }


def compare_with_signature(image, reference_signature, threshold=DEFAULT_FACE_THRESHOLD, assume_face_roi=False):
    if reference_signature is None:
        return False, 0.0

    probe_embedding = _extract_embedding(image, assume_face_roi=assume_face_roi)
    if probe_embedding is None:
        return False, 0.0

    distance = _cosine_distance(reference_signature["embedding"], probe_embedding)
    confidence = max(0.0, min(1.0, 1.0 - distance))
    return confidence >= threshold, confidence


def compare_face_images(reference_image, probe_image, threshold=DEFAULT_FACE_THRESHOLD):
    signature = build_reference_signature(reference_image)
    return compare_with_signature(probe_image, signature, threshold=threshold)