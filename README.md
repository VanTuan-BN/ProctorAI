# Student Proctoring & Earpiece Detection

A lightweight, publishable snapshot of a student proctoring and cheating detection system. This repository includes source code, configuration, UI components, and model integration for deployment and review.

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

Update `config.yaml` before running the application to adjust:

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
- `config.yaml` — application settings

## Notes for reviewers

- This repository intentionally excludes dataset and image data.
- If you need to run the project fully, provide external data sources separately.
- You may add your own model or dataset files, but do not commit private data.

## Contributing

If you want to contribute improvements:

1. Fork the repository.
2. Create a feature branch.
3. Submit a pull request.

