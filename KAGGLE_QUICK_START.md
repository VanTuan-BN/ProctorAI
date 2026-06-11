# Quick Reference: Kaggle Fine-tuning Earpiece YOLO

## ⚡ 5-Minute Setup

### 1. Upload Dataset
```
kaggle datasets create -p Dataset_earpiece_finetune_clean/
# Hoặc upload manual: https://kaggle.com/datasets → Create
```

### 2. Create Notebook
```
https://kaggle.com/code → New Notebook
```

### 3. Add Data
- Click "Add data" 
- Search "earpiece-dataset-clean"
- Add to notebook

### 4. Run Code

**Cell 1: Install**
```python
!pip install -q ultralytics opencv-python pyyaml
import torch
from ultralytics import YOLO
print("✓ Ready")
```

**Cell 2: Train**
```python
import yaml
from pathlib import Path

dataset_yaml = "/kaggle/input/earpiece-dataset-clean/dataset.yaml"

model = YOLO("yolo11n.pt")
results = model.train(
    data=dataset_yaml,
    epochs=100,
    imgsz=640,
    batch=-1,
    device=0,
    patience=20,
    cache="ram",
    project="/kaggle/working/runs",
    name="earpiece_yolo11n",
)
```

**Cell 3: Export**
```python
model.export(format='onnx', imgsz=640, optimize=True)
print("✓ Models exported to /kaggle/working/")
```

---

## 🎯 Performance Targets

| Metric | Good | Excellent |
|--------|------|-----------|
| mAP50  | 0.75-0.85 | 0.85+ |
| mAP50-95 | 0.50-0.65 | 0.65+ |
| Precision | 0.80+ | 0.90+ |
| Recall | 0.75+ | 0.85+ |

---

## 🔧 Quick Tweaks

### For Better Accuracy
```python
epochs=150          # Longer training
imgsz=832           # Higher resolution
warmup_epochs=5     # More warmup
mixup=0.3           # More augmentation
```

### For Faster Training
```python
epochs=50           # Fewer epochs
imgsz=416           # Lower resolution
batch=8             # Smaller batch (if auto fails)
```

### For Faster Inference
```python
# Export with quantization
model.export(
    format='onnx',
    imgsz=640,
    optimize=True,
    int8=True  # INT8 quantization
)
```

---

## 📊 Monitor Training

```python
# View metrics during training
from IPython.display import Image
display(Image("/kaggle/working/runs/earpiece_yolo11n/results.png"))

# Check final metrics
import json
with open("/kaggle/working/runs/earpiece_yolo11n/results.json") as f:
    metrics = json.load(f)
```

---

## 📥 Use Trained Model

```python
from ultralytics import YOLO

# Load best model
model = YOLO("/kaggle/working/runs/earpiece_yolo11n/weights/best.pt")

# Inference
results = model.predict("image.jpg", conf=0.5)

# Visualize
for r in results:
    r.show()  # Display with boxes
```

---

## 📤 Download Results

After training:
1. Click ⋮ menu on notebook
2. "Save notebook & run"
3. Wait for completion
4. Click blue "Download" button
5. Select `/kaggle/working/` files

Or via terminal:
```bash
kaggle notebooks download-source <notebook-id>
```

---

## Common Issues

**CUDA Out of Memory**
```python
batch=8  # Fixed batch size
imgsz=480
```

**Slow training**
```python
cache="disk"  # Instead of "ram"
workers=8     # More workers
```

**Low accuracy**
```python
epochs=200
warmup_epochs=5
mixup=0.3
```

---

## 3 Model Sizes

| Model | Size | Speed | Accuracy | GPU Mem |
|-------|------|-------|----------|---------|
| yolo11n | 2.6M | ⚡⚡⚡ | ⭐⭐⭐ | 2GB |
| yolo11s | 9.1M | ⚡⚡ | ⭐⭐⭐⭐ | 4GB |
| yolo11m | 20M | ⚡ | ⭐⭐⭐⭐⭐ | 8GB |

Choose based on:
- **yolo11n**: Speed is priority
- **yolo11s**: Balance (recommended)
- **yolo11m**: Accuracy is priority

---

## Export Formats

```python
# Best for Python inference
model.export(format='pt')

# Best for C++/Edge deployment
model.export(format='onnx')

# Best for mobile/IoT
model.export(format='tflite', int8=True)

# Best for production optimization
model.export(format='onnx', optimize=True, int8=True)
```

---

## Complete Training Script

```python
from ultralytics import YOLO
import yaml

# Config
DATASET = "/kaggle/input/earpiece-dataset-clean/dataset.yaml"
OUTPUT = "/kaggle/working/runs"
MODEL = "yolo11s"  # Change as needed
EPOCHS = 100
IMGSZ = 640

# Train
model = YOLO(f"{MODEL}.pt")
results = model.train(
    data=DATASET,
    epochs=EPOCHS,
    imgsz=IMGSZ,
    batch=-1,
    device=0,
    patience=20,
    cache="ram",
    workers=4,
    project=OUTPUT,
    name=f"earpiece_{MODEL}",
    plots=True,
    verbose=True,
)

# Export
model.export(format='onnx', imgsz=IMGSZ, optimize=True)
model.export(format='pt', imgsz=IMGSZ)

print("✅ Done!")
```

---

## Post-Training Checklist

- [ ] Training completed without errors
- [ ] Metrics show reasonable improvement
- [ ] Models exported successfully
- [ ] Test predictions look good
- [ ] Results downloaded
- [ ] Ready to integrate into core_ai.py

---

## Integration (core_ai.py)

```python
from ultralytics import YOLO

# Load specialist earpiece model
EARPIECE_MODEL = YOLO("models/earpiece_specialist_yolo11n.pt")

# In your detection pipeline
def detect_earpiece(frame):
    results = EARPIECE_MODEL(frame, conf=0.5)
    return results
```

---

**Total Time:**
- Dataset prep: 5 min
- Setup: 5 min
- Training (yolo11n): 30-60 min
- Export: 2 min
- **Total: ~1 hour**

🚀 Ready to train!
