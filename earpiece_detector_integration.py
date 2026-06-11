"""
Integration Helper: Using Trained Earpiece Model in core_ai.py
==============================================================

This shows how to integrate the fine-tuned earpiece specialist model
into your monitoring pipeline.
"""

# ============================================
# OPTION 1: Load Pre-trained Specialist Model
# ============================================

from ultralytics import YOLO
import cv2
import numpy as np

# Load the trained earpiece specialist model
EARPIECE_SPECIALIST_MODEL = YOLO("models/earpiece_specialist_yolo11n.pt")

# Keep general model for person/phone detection (use best_onlyA instead)
# GENERAL_MODEL = YOLO("models/Model_proc_exam_1024_01.pt")  # DEPRECATED
GENERAL_MODEL = YOLO("models/best_onlyA.pt")  # Use optimized model


# ============================================
# OPTION 2: Hybrid Approach (Recommended)
# ============================================

class HybridDetector:
    """
    Use specialized model for earpiece only,
    general model for person + cell phone
    """
    
    def __init__(self):
        # Specialist for earpiece (high precision)
        self.earpiece_model = YOLO("models/earpiece_specialist_yolo11n.pt")
        
        # General for other classes (use best_onlyA instead)
        self.general_model = YOLO("models/best_onlyA.pt")  # Optimized: imgsz=640
        
        # Class mappings
        self.earpiece_class_id = 2  # From dataset.yaml
        self.phone_class_id = 1
        self.person_class_id = 0
    
    def detect(self, frame, conf=0.5):
        """
        Detect all 3 classes using specialized + general models
        
        Returns:
            {
                'earpiece': [...],  # Detections from specialist
                'phone': [...],     # Detections from general
                'person': [...]     # Detections from general
            }
        """
        results = {}
        
        # Option A: Run specialist for earpiece only
        earpiece_detections = self._detect_earpiece(frame, conf)
        results['earpiece'] = earpiece_detections
        
        # Option B: Run general for other classes
        general_dets = self._detect_general(frame, conf)
        results['phone'] = general_dets.get('phone', [])
        results['person'] = general_dets.get('person', [])
        
        return results
    
    def _detect_earpiece(self, frame, conf):
        """Detect earpiece using specialist model"""
        results = self.earpiece_model(frame, conf=conf, verbose=False)
        
        detections = []
        for result in results:
            for box in result.boxes:
                if int(box.cls) == self.earpiece_class_id:
                    detections.append({
                        'class': 'earpiece',
                        'confidence': float(box.conf),
                        'bbox': box.xyxy[0].cpu().numpy(),
                        'model': 'specialist'
                    })
        
        return detections
    
    def _detect_general(self, frame, conf):
        """Detect other classes using general model"""
        results = self.general_model(frame, conf=conf, verbose=False)
        
        detections = {'phone': [], 'person': []}
        for result in results:
            for box in result.boxes:
                class_id = int(box.cls)
                
                if class_id == self.phone_class_id:
                    detections['phone'].append({
                        'class': 'phone',
                        'confidence': float(box.conf),
                        'bbox': box.xyxy[0].cpu().numpy(),
                        'model': 'general'
                    })
                elif class_id == self.person_class_id:
                    detections['person'].append({
                        'class': 'person',
                        'confidence': float(box.conf),
                        'bbox': box.xyxy[0].cpu().numpy(),
                        'model': 'general'
                    })
        
        return detections
    
    def visualize_detections(self, frame, detections):
        """Draw boxes on frame"""
        frame_vis = frame.copy()
        
        colors = {
            'earpiece': (0, 0, 255),    # Red (specialist)
            'phone': (0, 255, 0),       # Green
            'person': (255, 0, 0)       # Blue
        }
        
        for class_name in detections:
            for det in detections[class_name]:
                x1, y1, x2, y2 = det['bbox'].astype(int)
                conf = det['confidence']
                
                color = colors.get(class_name, (255, 255, 255))
                
                # Draw box
                cv2.rectangle(frame_vis, (x1, y1), (x2, y2), color, 2)
                
                # Draw label
                label = f"{class_name}: {conf:.2f}"
                cv2.putText(frame_vis, label, (x1, y1-10),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        
        return frame_vis


# ============================================
# OPTION 3: Ensemble (Highest Accuracy)
# ============================================

class EnsembleDetector:
    """
    Use multiple specialist models and vote on detections.
    Best for crucial compliance checks.
    """
    
    def __init__(self):
        # Multiple earpiece specialists trained differently
        self.earpiece_models = [
            YOLO("models/earpiece_specialist_yolo11n.pt"),
            YOLO("models/earpiece_specialist_yolo11s.pt"),  # if available
        ]
        
        # General model for other classes
        self.general_model = YOLO("models/Model_proc_exam_1024_01.pt")
    
    def detect_earpiece_ensemble(self, frame, conf=0.5):
        """
        Run all specialist models and voting
        """
        all_detections = []
        
        for model in self.earpiece_models:
            results = model(frame, conf=conf, verbose=False)
            for result in results:
                for box in result.boxes:
                    all_detections.append(box.xyxy[0].cpu().numpy())
        
        # Simple NMS voting
        if len(all_detections) > 0:
            all_detections = np.array(all_detections)
            
            # Cluster detections (simple voting)
            # Remove duplicates if multiple models detected same object
            unique_dets = self._cluster_detections(all_detections)
            return unique_dets
        
        return []
    
    def _cluster_detections(self, dets, iou_threshold=0.5):
        """Cluster similar detections"""
        if len(dets) == 0:
            return []
        
        # Simple implementation: keep only high-frequency detections
        from scipy.spatial.distance import pdist
        import scipy
        
        # This is simplified - proper NMS recommended
        unique = [dets[0]]
        for det in dets[1:]:
            # Check if duplicate of existing
            is_duplicate = False
            for existing in unique:
                iou = self._iou(det, existing)
                if iou > iou_threshold:
                    is_duplicate = True
                    break
            if not is_duplicate:
                unique.append(det)
        
        return np.array(unique)
    
    def _iou(self, box1, box2):
        """Calculate IoU between two boxes"""
        x1_min, y1_min, x1_max, y1_max = box1
        x2_min, y2_min, x2_max, y2_max = box2
        
        inter_xmin = max(x1_min, x2_min)
        inter_ymin = max(y1_min, y2_min)
        inter_xmax = min(x1_max, x2_max)
        inter_ymax = min(y1_max, y2_max)
        
        if inter_xmax < inter_xmin or inter_ymax < inter_ymin:
            return 0.0
        
        inter_area = (inter_xmax - inter_xmin) * (inter_ymax - inter_ymin)
        box1_area = (x1_max - x1_min) * (y1_max - y1_min)
        box2_area = (x2_max - x2_min) * (y2_max - y2_min)
        
        union_area = box1_area + box2_area - inter_area
        
        return inter_area / union_area


# ============================================
# INTEGRATION WITH core_ai.py
# ============================================

"""
Example integration in core_ai.py:

Replace the existing detection logic with:

---

# At module level (in core_ai.py)
from your_location import HybridDetector

detector = HybridDetector()

# In your detection worker thread:
def ai_detection_worker(frame):
    # Use hybrid detector
    detections = detector.detect(frame, conf=0.5)
    
    # Check for violations
    if detections['earpiece']:
        # Alert: Earpiece detected
        log_violation("earpiece_detected")
        trigger_warning()
    
    if detections['phone']:
        # Alert: Phone detected  
        log_violation("phone_detected")
        trigger_warning()
    
    # Continue with identity tracking, etc.
    ...

---
"""


# ============================================
# STANDALONE TESTING
# ============================================

def test_hybrid_detector():
    """Test the detector"""
    import cv2
    
    detector = HybridDetector()
    
    # Test on video
    cap = cv2.VideoCapture(0)
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        # Detect
        detections = detector.detect(frame, conf=0.5)
        
        # Visualize
        frame_vis = detector.visualize_detections(frame, detections)
        
        # Display
        cv2.imshow("Hybrid Detection", frame_vis)
        
        # Log detections
        for class_name in detections:
            if detections[class_name]:
                print(f"Detected {class_name}: {len(detections[class_name])}")
        
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    # Test
    detector = HybridDetector()
    print("✓ Hybrid detector initialized")
    print("✓ Earpiece specialist model loaded")
    print("✓ General model loaded")
    print("\nReady to integrate into core_ai.py")
    
    # Uncomment to test
    # test_hybrid_detector()
