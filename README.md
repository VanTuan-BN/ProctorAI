# Student Proctoring & Earpiece Detection

## Project Description

Online exam proctoring system for anti-cheating surveillance. This repository contains an independently researched and developed real-time AI system integrated with surveillance cameras to automate the detection of cheating behavior during exams. The project focuses on solving complex computer vision problems, including:

- detecting tiny violation devices such as smartphones and earpiece headphones,
- identifying unauthorized entries or a second person in the exam room,
- verifying the candidate's identity using face recognition.

## Application Demo

<p align="center">
  <video src="https://githubusercontent.com" width="100%" max-width="750px" autoplay loop muted playsinline></video>
</p>

## Value Proposition

- **High accuracy**: uses a 2-stage pipeline architecture with automatic ROI extraction before deep object detection. Custom loss functions like Focal Loss and CIoU improve model convergence on imbalanced datasets.
- **Tiny object detection**: optimized to identify earpieces and phones under challenging real-world conditions.
- **Hardware and cost efficiency**: delivers real-time processing at > 30 FPS directly at the edge on common PC hardware.
- **Low alert latency**: fast inference reduces bandwidth usage and keeps image data local for improved privacy.

## Two-tier Architecture

- **Edge data collection and processing layer**: runs close to the camera source, performs image preprocessing, ROI detection, violation detection, and face verification.
- **Service and management layer**: coordinates APIs, stores events, displays admin dashboards, and enables centralized monitoring.

## Tools and Platforms

- Models & architecture:  `YOLO26`
- Libraries & frameworks: `OpenCV`, `Python (OOP)`, `PyTorch`, `Hugging Face`
- Infrastructure & platforms: `Kaggle (P100 GPU)`, `Roboflow` (data preprocessing and annotation), `Git/GitHub`

## Contents

- `admin_app.py` — admin dashboard and management app for supervising the system.
- `proctor_app.py` — proctoring application for live exam monitoring.
- `api_server.py` — REST API server for client/module communication.
- `face_verifier.py` — face verification and identity matching module.
- `core_ai.py` — core AI orchestration and decision logic.
- `earpiece_detector_integration.py` — earpiece detection integration.
- `main_ui.py`, `ui_components.py`, `ui_theme.py`, `ui_branding.py` — UI code.
- `config.yaml`, `runtime_env.py` — runtime and configuration settings.
- `models/` — pretrained model weights.
- `assets/`, `logos/` — image and branding assets.
- `requirements.txt` — required Python libraries.

## Why this repository exists

This filtered repository is designed for sharing code without exposing:

- private datasets
- personal images
- sensitive user data
- local database or evidence directories

## Features

- student exam monitoring
- face recognition and verification
- earpiece detection in video/image frames
- modular UI with separate admin and proctor interfaces
- Python-based API service layer
- real-time edge inference on common hardware (>30 FPS on typical PCs)

## Installation

1. Install Python 3.8 or newer.
2. Create a virtual environment.

```bash
python -m venv venv
venv\Scripts\activate
```

3. Install dependencies.

```bash
pip install -r requirements.txt
```

## Usage

Run one of the app entrypoints depending on your scenario:

```bash
python admin_app.py
python proctor_app.py
python api_server.py
```

### Typical workflow

1. Start `api_server.py` if you need a backend API service.
2. Launch `admin_app.py` to monitor or manage the system.
3. Launch `proctor_app.py` to run the proctoring session.

## App details (short)

- `admin_app.py` — Admin dashboard and management UI. Use this to review sessions, flags, and logs.
  - Run: `python admin_app.py --config config.yaml`
- `proctor_app.py` — Real-time proctoring client for exam sessions. Captures video, runs detection, and sends events to the server.
  - Run: `python proctor_app.py --input camera --config config.yaml`
- `api_server.py` — REST API server used by the UI and clients.
  - Run: `python api_server.py --host 0.0.0.0 --port 8000`
- `face_verifier.py` — Utility/service for verifying face images against enrollment data.
  - Run: `python face_verifier.py --image path/to/image.jpg --model models/Model_ft_person_earpiece.pt`
- `core_ai.py` — Core AI orchestration (imported by other apps; not usually run standalone).
- `earpiece_detector_integration.py` — Module that integrates earpiece detection models into the pipeline.

## Usage examples

Start API server (background or separate terminal):

```bash
python api_server.py --host 0.0.0.0 --port 8000
```

Run admin UI (local):

```bash
python admin_app.py --config config.yaml
```

Run proctoring client using default camera:

```bash
python proctor_app.py --input camera --config config.yaml
```

Run face verification tool on an image:

```bash
python face_verifier.py --image samples/student1.jpg --model models/Model_ft_person_earpiece.pt
```

## Configuration

Copy `config.example.yaml` to `config.yaml` and update it before running the application. Adjust:

- input sources
- model paths
- thresholds and detection settings
- server ports

## Project structure

- `*.py` — source code for application logic
- `assets/` — image and UI resources
- `logos/` — branding and icon graphics
- `models/` — model weight files
- `requirements.txt` — Python dependencies
- `config.example.yaml` — application settings template

## Notes for reviewers

- This repository intentionally excludes dataset and image data.
- If you need to run the project fully, provide external data sources separately.
- You may add your own model or dataset files, but do not commit private data.

## Contributing

If you want to contribute improvements:

1. Fork the repository.
2. Create a feature branch.
3. Submit a pull request.

