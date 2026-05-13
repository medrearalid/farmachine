"""
YoloVision - YOLO26 ONNX-based Object Detection for Metin2
===========================================================
Uses the exported YOLO26 ONNX model as the primary detector.

Key Features:
- Real-time object detection
- Thread-safe detection queue for decoupled rendering
- Smart target selection with distance heuristics
"""

from ultralytics import YOLO
import cv2
import numpy as np
import mss
import torch
import math
import os
import threading
import queue
import time
from typing import Optional, List, Dict, Tuple

# ===== MODEL CONFIGURATION =====
CUSTOM_MODEL_PATH = 'models/metin2_yolo26.onnx'
SECONDARY_MODEL_PATH = 'models/best.pt'
FALLBACK_MODEL_PATH = 'yolo11n.pt'
TARGET_CLASS_ID = 0


def normalize_mask_regions(mask_regions, frame_width: int, frame_height: int) -> List[Tuple[int, int, int, int]]:
    """Clamp and validate ROI masks against frame bounds."""
    normalized: List[Tuple[int, int, int, int]] = []
    if not isinstance(mask_regions, list):
        return normalized

    max_x = max(0, int(frame_width))
    max_y = max(0, int(frame_height))
    if max_x <= 0 or max_y <= 0:
        return normalized

    for item in mask_regions:
        if not isinstance(item, dict):
            continue

        try:
            raw_x = int(item.get("x", 0))
            raw_y = int(item.get("y", 0))
            raw_w = int(item.get("width", 0))
            raw_h = int(item.get("height", 0))
        except Exception:
            continue

        if raw_w <= 0 or raw_h <= 0:
            continue

        x1 = max(0, min(max_x, raw_x))
        y1 = max(0, min(max_y, raw_y))
        x2 = max(0, min(max_x, raw_x + raw_w))
        y2 = max(0, min(max_y, raw_y + raw_h))

        if x2 <= x1 or y2 <= y1:
            continue

        normalized.append((x1, y1, x2, y2))

    return normalized


def apply_mask_regions(frame: np.ndarray, mask_regions) -> np.ndarray:
    """Return a masked frame copy when valid regions exist, else original frame."""
    if frame is None or not isinstance(frame, np.ndarray):
        return frame

    height, width = frame.shape[:2]
    normalized = normalize_mask_regions(mask_regions, width, height)
    if not normalized:
        return frame

    masked = frame.copy()
    for x1, y1, x2, y2 in normalized:
        cv2.rectangle(masked, (x1, y1), (x2 - 1, y2 - 1), (0, 0, 0), thickness=-1)
    return masked


def rect_intersects_mask_regions(rect, mask_regions, frame_width: int, frame_height: int) -> bool:
    """Return True when a bbox overlaps any normalized mask region."""
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        return False

    normalized = mask_regions
    if not normalized or not isinstance(normalized, list) or not all(isinstance(item, tuple) and len(item) == 4 for item in normalized):
        normalized = normalize_mask_regions(mask_regions, frame_width, frame_height)
    if not normalized:
        return False

    x1, y1, x2, y2 = [float(value) for value in rect]
    for mask_x1, mask_y1, mask_x2, mask_y2 in normalized:
        if x1 < mask_x2 and x2 > mask_x1 and y1 < mask_y2 and y2 > mask_y1:
            return True
    return False


def point_in_mask_regions(x: int, y: int, mask_regions, frame_width: int, frame_height: int) -> bool:
    """Return True when a local point falls inside any normalized mask region."""
    normalized = mask_regions
    if not normalized or not isinstance(normalized, list) or not all(isinstance(item, tuple) and len(item) == 4 for item in normalized):
        normalized = normalize_mask_regions(mask_regions, frame_width, frame_height)
    if not normalized:
        return False

    px = int(x)
    py = int(y)
    for x1, y1, x2, y2 in normalized:
        if x1 <= px < x2 and y1 <= py < y2:
            return True
    return False


class YoloVision:
    """
    YOLO Vision for Metin2 stone detection.
    
    Architecture:
    - Detection at ~15-30 FPS depending on GPU
    - Thread-safe queue for decoupled overlay rendering
    - Class-0 focused detection for Metin stones
    """
    
    # Default threshold - can be overridden by set_confidence_threshold()
    DEFAULT_CONFIDENCE_THRESHOLD = 0.45
    DEFAULT_INPUT_SIZE = 640
    DEFAULT_MAX_DET = 64
    DEFAULT_MAX_INFER_FPS_GPU = 30.0
    DEFAULT_MAX_INFER_FPS_CPU = 10.0
    DEFAULT_RETENTION_MS = 220.0
    
    def __init__(self, model_path=None):
        """Initialize YOLO Vision with tracking support."""
        
        # Dynamic confidence threshold (can be updated from GUI)
        self.confidence_threshold = self.DEFAULT_CONFIDENCE_THRESHOLD
        self.input_size = self.DEFAULT_INPUT_SIZE
        self.max_det = self.DEFAULT_MAX_DET
        self.device = self._resolve_device()
        self.is_gpu_runtime = self.device.startswith("cuda")
        # Inference is pinned to GPU index 0 for the YOLO26 runtime path.
        self.inference_device = 0
        self.model_path = ""
        self.is_onnx_model = False
        self.use_half = False
        self.max_infer_fps = self.DEFAULT_MAX_INFER_FPS_GPU if self.is_gpu_runtime else self.DEFAULT_MAX_INFER_FPS_CPU
        self.min_infer_interval = 1.0 / self.max_infer_fps
        self.last_infer_time = 0.0
        self.last_infer_latency_ms = 0.0
        self.last_target: Optional[Tuple[int, int]] = None
        self.last_targets: List[Tuple[int, int]] = []
        self.retention_ms = self.DEFAULT_RETENTION_MS

        # Determine model path
        if model_path is None:
            model_path = self._resolve_model_path()

        self.model_path = str(model_path)
        self.is_onnx_model = self.model_path.lower().endswith(".onnx")
        self.use_half = self.device.startswith("cuda") and not self.is_onnx_model
        self.max_infer_fps = self.DEFAULT_MAX_INFER_FPS_GPU if self.is_gpu_runtime else self.DEFAULT_MAX_INFER_FPS_CPU
        self.min_infer_interval = 1.0 / self.max_infer_fps

        if self.use_half:
            torch.backends.cudnn.benchmark = True
        
        # Load Model (GPU if available)
        print(f"🧠 YOLO Model Loading... (Device: {self.device})")
        
        try:
            self.model = YOLO(model_path, task="detect")
            if hasattr(self.model, "to") and not self.is_onnx_model:
                self.model.to(self.device)
            self._warmup_model()
            print(f"✅ Model loaded: {model_path}")
            print(f"📊 Confidence threshold: {self.confidence_threshold}")
            print(f"[YOLO] Runtime -> device={self.device}, half={self.use_half}, max_fps={self.max_infer_fps:.0f}, imgsz={self.input_size}")
        except Exception as e:
            print(f"❌ Failed to load model: {e}")
            fallback_path = SECONDARY_MODEL_PATH
            if os.path.normpath(fallback_path) == os.path.normpath(self.model_path) or not os.path.exists(fallback_path):
                fallback_path = FALLBACK_MODEL_PATH
            print(f"⚠️ Attempting fallback to {fallback_path}...")
            self.model_path = fallback_path
            self.is_onnx_model = self.model_path.lower().endswith(".onnx")
            self.use_half = self.device.startswith("cuda") and not self.is_onnx_model
            self.max_infer_fps = self.DEFAULT_MAX_INFER_FPS_GPU if self.is_gpu_runtime else self.DEFAULT_MAX_INFER_FPS_CPU
            self.min_infer_interval = 1.0 / self.max_infer_fps
            self.model = YOLO(fallback_path, task="detect")
            if hasattr(self.model, "to") and not self.is_onnx_model:
                self.model.to(self.device)
            self._warmup_model()
        
        # Screen anchor (character position)
        self.center_x = None
        self.center_y = None
        
        # Detection state
        self.last_results = None
        self.frame_count = 0
        
        # Thread-safe detection queue (maxsize=1 = always latest)
        self.detection_queue = queue.Queue(maxsize=1)
        self.last_detections = []  # Cache for rendering when queue is empty
        self.last_detection_time = 0
        
        # Lock for thread safety
        self._lock = threading.Lock()

    def _resolve_device(self) -> str:
        if torch.cuda.is_available():
            return "cuda:0"
        return "cpu"

    def _warmup_model(self):
        """
        Prime kernels once to reduce first-frame latency spikes.
        """
        try:
            warmup = np.zeros((self.input_size, self.input_size, 3), dtype=np.uint8)
            with torch.inference_mode():
                self.model.predict(
                    warmup,
                    conf=0.45,
                    device=self.inference_device,
                    half=self.use_half,
                    imgsz=self.input_size,
                    max_det=1,
                    verbose=False,
                )
        except Exception:
            # Warmup failure should never block startup.
            pass
    
    def set_confidence_threshold(self, threshold: float):
        """
        Set runtime confidence preference from GUI/settings.

        YOLO26 inference uses a fixed model confidence of 0.45. This setter
        is retained for compatibility with existing UI/config wiring.
        
        Args:
            threshold: Confidence threshold (0.0 to 1.0)
        """
        old_threshold = self.confidence_threshold
        # YOLO26 pipeline uses fixed model inference threshold (0.45).
        # Keep runtime value bounded for diagnostics/UI compatibility.
        self.confidence_threshold = max(0.1, min(0.45, threshold))
        
        print(f"[YOLO] 📊 Confidence Threshold Updated: {old_threshold:.2f} -> {self.confidence_threshold:.2f}")
        print(f"[YOLO] DEBUG: Type={type(self.confidence_threshold)}, Value={self.confidence_threshold}")
    
    def _resolve_model_path(self) -> str:
        """Resolve which model to use based on availability."""
        if os.path.exists(CUSTOM_MODEL_PATH):
            print(f"🎯 Custom model found: {CUSTOM_MODEL_PATH}")
            return CUSTOM_MODEL_PATH

        if os.path.exists(SECONDARY_MODEL_PATH):
            print(f"🎯 Secondary model found: {SECONDARY_MODEL_PATH}")
            return SECONDARY_MODEL_PATH
        
        runs_best = 'runs/detect/metin2_tas_modeli/weights/best.pt'
        if os.path.exists(runs_best):
            print(f"🎯 Trained model found in runs: {runs_best}")
            return runs_best
        
        print(f"⚠️ Custom model '{CUSTOM_MODEL_PATH}' not found.")
        print("⚠️ Using fallback YOLO model. NOTE: Fallback may be less accurate for Metin stones.")
        return FALLBACK_MODEL_PATH

    def _get_cached_detections_copy(self) -> List[Dict]:
        with self._lock:
            return self.last_detections.copy()

    def get_recent_detections(self, max_age_ms: float = DEFAULT_RETENTION_MS) -> List[Dict]:
        with self._lock:
            age_ms = (time.time() - self.last_detection_time) * 1000
            if self.last_detection_time <= 0 or age_ms > max_age_ms:
                return []
            return self.last_detections.copy()

    def get_runtime_info(self) -> Dict[str, object]:
        return {
            "device": self.device,
            "inference_device": self.inference_device,
            "gpu_runtime": self.is_gpu_runtime,
            "half": self.use_half,
            "max_infer_fps": self.max_infer_fps,
            "input_size": self.input_size,
            "max_det": self.max_det,
            "model_path": self.model_path,
            "last_infer_latency_ms": self.last_infer_latency_ms,
        }

    def get_top_targets(self, img=None, max_targets: int = 1, mask_regions=None) -> Tuple[List[Tuple[int, int]], List[Dict]]:  # NOSONAR
        """
        Return top-N valid targets sorted by distance to character anchor.

        Filtering rules:
        - Class-0 only (Metin)
        - Fixed confidence floor (0.45)
        - Edge exclusion (5% margins)
        - Distance-first sort, area as tie-breaker

        Args:
            img: Optional BGR frame. If None, captures from primary monitor.
            max_targets: Maximum number of targets to return.

        Returns:
            (targets, detections)
            - targets: List[(x, y)] in LOCAL frame coordinates.
            - detections: Filtered detection list for overlay/debug usage.
        """
        try:
            now = time.time()
            target_limit = max(1, int(max_targets))

            # Rate-limit heavy inference calls and reuse latest result between ticks.
            if self.last_infer_time > 0 and (now - self.last_infer_time) < self.min_infer_interval:
                return self.last_targets[:target_limit], self._get_cached_detections_copy()

            self.frame_count += 1

            # Debug: Log threshold every 100 frames
            if self.frame_count % 100 == 1:
                print(f"[DEBUG] Current Threshold: {self.confidence_threshold} | Type: {type(self.confidence_threshold)}")

            if img is None:
                with mss.mss() as sct:
                    monitor = sct.monitors[1]
                    screenshot = np.array(sct.grab(monitor))
                img = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2BGR)

            inference_img = apply_mask_regions(img, mask_regions)
            normalized_mask_regions = normalize_mask_regions(mask_regions, inference_img.shape[1], inference_img.shape[0])

            img_height, img_width = inference_img.shape[:2]

            # Character center point is the midpoint of inference resolution.
            anchor_x = img_width * 0.5
            anchor_y = img_height * 0.5

            self.center_x = int(anchor_x)
            self.center_y = int(anchor_y)

            # ============================================================
            # RESOLUTION-INDEPENDENT EDGE MARGIN
            # Use 5% of image dimensions instead of hardcoded pixels
            # ============================================================
            EDGE_MARGIN_RATIO = 0.05  # 5% of screen edges excluded
            edge_margin_x = int(img_width * EDGE_MARGIN_RATIO)
            edge_margin_y = int(img_height * EDGE_MARGIN_RATIO)

            # ============================================================
            # DETECTION MODE: Use LOW conf for YOLO, then filter manually
            # This ensures we get all possible detections and filter ourselves
            # ============================================================
            try:
                infer_start = time.time()
                with torch.inference_mode():
                    results = self.model.predict(
                        inference_img,
                        conf=0.45,
                        verbose=False,
                        device=self.inference_device,
                        half=self.use_half,
                        imgsz=self.input_size,
                        max_det=self.max_det,
                    )
                self.last_infer_time = time.time()
                self.last_infer_latency_ms = (self.last_infer_time - infer_start) * 1000.0
            except Exception as infer_err:
                print(f"❌ Inference error: {infer_err}")
                return [], []

            self.last_results = results

            all_detections = []  # Class-0 detections that pass confidence floor.
            valid_targets = []

            for r in results:
                boxes = r.boxes
                if boxes is None:
                    continue

                for box in boxes:
                    score = float(box.conf[0].item())
                    cls_id = int(box.cls[0].item())

                    # YOLO26 model class mapping: class 0 = Metin stones.
                    if cls_id != TARGET_CLASS_ID:
                        continue

                    if score < 0.45:
                        continue

                    box_cx, box_cy, box_w, box_h = [float(v) for v in box.xywh[0].tolist()]
                    if box_w <= 0.0 or box_h <= 0.0:
                        continue

                    x1 = box_cx - (box_w * 0.5)
                    y1 = box_cy - (box_h * 0.5)
                    x2 = box_cx + (box_w * 0.5)
                    y2 = box_cy + (box_h * 0.5)

                    if rect_intersects_mask_regions([x1, y1, x2, y2], normalized_mask_regions, img_width, img_height):
                        continue

                    label = self.model.names[cls_id] if hasattr(self.model, 'names') else 'metin'

                    box_area = box_w * box_h

                    distance = math.hypot(box_cx - anchor_x, box_cy - anchor_y)

                    # Keep label clean; overlay renders confidence text separately.
                    detection = {
                        'center': (int(box_cx), int(box_cy)),
                        'width': box_w,
                        'height': box_h,
                        'confidence': score,
                        'distance': distance,
                        'rect': [float(x1), float(y1), float(x2), float(y2)],
                        'label': str(label),
                        'conf': score,
                    }
                    all_detections.append(detection)

                    # Edge exclusion filter (using relative margins)
                    if box_cx < edge_margin_x or box_cx > (img_width - edge_margin_x):
                        continue
                    if box_cy < edge_margin_y or box_cy > (img_height - edge_margin_y):
                        continue

                    valid_targets.append({
                        'center': (int(box_cx), int(box_cy)),
                        'distance': distance,
                        'area': box_area,
                        'confidence': score
                    })

            # Keep detection payload aligned with closest-first target selection.
            all_detections.sort(key=lambda det: float(det.get('distance', float('inf'))))

            # Sort by distance, prefer larger boxes for ties
            valid_targets.sort(key=lambda t: (t['distance'], -t['area']))
            top_targets = [target['center'] for target in valid_targets[:target_limit]]

            self.last_targets = list(top_targets)
            self.last_target = top_targets[0] if top_targets else None

            # Update thread-safe cache
            self._update_detection_cache(all_detections)

            return top_targets, all_detections

        except Exception as e:
            print(f"Vision Error: {e}")
            import traceback
            traceback.print_exc()
            return [], []

    def get_closest_stone(self, img=None) -> Tuple[Optional[Tuple[int, int]], List[Dict]]:  # NOSONAR
        """
        Backward-compatible single-target API.
        """
        targets, detections = self.get_top_targets(img=img, max_targets=1)
        return (targets[0] if targets else None), detections
    
    def _update_detection_cache(self, detections: List[Dict]):
        """
        Thread-safe update of detection cache.
        
        Uses a non-blocking put with maxsize=1 queue to always
        keep the latest detection available for the GUI thread.
        """
        with self._lock:
            self.last_detections = detections.copy()
            self.last_detection_time = time.time()
        
        # Non-blocking queue update (replace if full)
        try:
            # Clear old data if queue is full
            try:
                self.detection_queue.get_nowait()
            except queue.Empty:
                pass
            
            # Put new detection
            self.detection_queue.put_nowait(detections)
        except queue.Full:
            pass
    
    def get_latest_detections(self) -> List[Dict]:
        """
        Get the latest detections for overlay rendering.
        
        This method is designed to be called by the GUI thread at
        60 FPS. It returns immediately with:
        1. New detections if available in queue
        2. Cached detections if queue is empty (tracking smooths gaps)
        
        Returns:
            List of detection dictionaries with rect, label, conf
        """
        try:
            # Try to get new detections (non-blocking)
            detections = self.detection_queue.get_nowait()
            with self._lock:
                self.last_detections = detections
            return detections
        except queue.Empty:
            # Return cached detections
            with self._lock:
                return self.last_detections.copy()
    
    def get_detection_age_ms(self) -> float:
        """Get the age of the last detection in milliseconds."""
        with self._lock:
            return (time.time() - self.last_detection_time) * 1000

    def clear_runtime_buffers(self) -> None:
        """
        Clear cached detections/targets after a client window switch.

        Prevents stale detections from the previously active context
        from leaking into the next context tick.
        """
        with self._lock:
            self.last_detections = []
            self.last_detection_time = 0
            self.last_targets = []
            self.last_target = None
            self.last_results = None

        try:
            while True:
                self.detection_queue.get_nowait()
        except queue.Empty:
            pass
    
    def get_debug_frame(self, img) -> np.ndarray:
        """Returns the image with bounding boxes drawn (for debugging)."""
        try:
            if self.last_results is not None and len(self.last_results) > 0:
                return self.last_results[0].plot()
            return img
        except Exception:
            return img


# ===== STANDALONE TEST =====
if __name__ == "__main__":
    vision = YoloVision()
    print("👀 Searching for stones...")
    
    while True:
        target, detections = vision.get_closest_stone()
        if target:
            print(f"🎯 Target Found: {target}")
            print(f"📦 Total Detections: {len(detections)}")

