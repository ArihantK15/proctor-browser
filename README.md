Procta

AI-Powered Online Exam Proctoring System

<p align="center">


</p>


A secure, intelligent, and scalable platform for conducting cheat-resistant online examinations.

Website: https://procta.net
App: https://app.procta.net

⸻

What is Procta?

Procta is a full-stack online proctoring system that combines a locked-down exam environment with real-time AI monitoring to ensure exam integrity.

It is built for institutions and organizations that require secure, remote, and reliable assessments at scale.

⸻

Core System

Procta operates through three integrated layers:
	•	Desktop Exam Client (Electron)
A locked, tamper-resistant environment for students
	•	AI Proctoring Engine (Python)
Real-time behavioral monitoring using vision and audio
	•	Backend & Dashboard (FastAPI + Supabase)
Control center for sessions, analytics, and reporting

⸻

Why Procta

Exam Integrity by Design
	•	Full kiosk lockdown (no switching, no shortcuts)
	•	Server-side scoring (client cannot manipulate results)
	•	Continuous session validation

AI-Based Monitoring
	•	Face tracking, gaze detection, and head movement analysis
	•	Object detection (phones, books, laptops)
	•	Identity verification using face embeddings
	•	Audio anomaly detection

Comprehensive Anti-Cheat Coverage
	•	Tab switching and focus loss detection
	•	Remote desktop and screen sharing detection
	•	Virtual machine and multi-monitor detection

Actionable Insights
	•	Behavioral risk scoring (0–100)
	•	Violation logs with timestamps and evidence
	•	Detailed PDF reports per candidate

⸻

Student Experience
	•	Simple roll number login
	•	Guided face calibration
	•	Clean exam interface with timer and navigation grid
	•	Automatic answer saving
	•	Reliable submission with retry protection

⸻

Admin Experience

Live Control
	•	Monitor active sessions in real time
	•	Force-submit exams if required
	•	Detect and review suspicious behavior instantly

Management Tools
	•	Create and manage questions directly from the dashboard
	•	Recover failed or incomplete sessions
	•	Backfill and recompute risk scores

Analytics
	•	Risk-based candidate evaluation
	•	Aggregated statistics across exams
	•	Visual breakdown of violation types

⸻

AI Proctoring Capabilities
	•	Face mesh tracking (468 landmarks)
	•	Eye tracking and blink detection
	•	Head pose estimation
	•	Multi-face detection
	•	Object detection via YOLOv8
	•	Face recognition via InsightFace
	•	Audio monitoring with sustained threshold detection

⸻

Architecture

Student App (Electron)
        ↓
AI Proctor (Python Process)
        ↓
FastAPI Backend (API Layer)
        ↓
Supabase (PostgreSQL Database)
        ↓
Admin Dashboard (Web)


⸻

Infrastructure
	•	Docker-based deployment
	•	Caddy (automatic HTTPS + reverse proxy)
	•	Optimised for low-resource servers (2GB)
	•	GitHub Actions for CI/CD
	•	DigitalOcean hosting

Marketing site: https://procta.net (Vercel)
App server: https://app.procta.net (DigitalOcean)

⸻

Tech Stack

Frontend
Electron, HTML, CSS, JavaScript

Backend
FastAPI, Supabase (PostgreSQL)

AI / ML
MediaPipe, YOLOv8, InsightFace, OpenCV

Infrastructure
Docker, Caddy, GitHub Actions

⸻

Project Status

Procta is under active development with ongoing improvements in performance, detection accuracy, and scalability.

⸻

License

This project is proprietary software. All rights reserved.

Unauthorized copying, modification, distribution, or use of this software is strictly prohibited without explicit permission.

⸻

Contact

Email: Arihantkaul@outlook.com
