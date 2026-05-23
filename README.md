<div align="center">

<!-- ── Banner ───────────────────────────────────────────── -->
<div style="
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f172a 100%);
  border-radius: 20px;
  padding: 48px 32px 40px;
  margin: 20px 0 32px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.3);
  position: relative;
  overflow: hidden;
">
  <div style="
    position: absolute;
    top: -60px; right: -60px;
    width: 200px; height: 200px;
    background: radial-gradient(circle, rgba(99,102,241,0.15) 0%, transparent 70%);
    border-radius: 50%;
  "></div>
  <div style="
    position: absolute;
    bottom: -80px; left: -40px;
    width: 250px; height: 250px;
    background: radial-gradient(circle, rgba(56,189,248,0.08) 0%, transparent 70%);
    border-radius: 50%;
  "></div>

  <div style="display:flex;align-items:center;justify-content:center;gap:16px;position:relative;z-index:1;margin-bottom:8px">
    <span style="font-size:48px">🛡️</span>
    <span style="
      font-size:42px;font-weight:800;
      background: linear-gradient(135deg, #818cf8, #38bdf8, #34d399);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      letter-spacing: -0.02em;
    ">Procta</span>
  </div>

  <p style="
    font-size:18px;color:#94a3b8;margin:8px 0 0;
    font-weight:400;letter-spacing:0.01em;position:relative;z-index:1;
  ">
    AI-Powered Online Exam Proctoring System
  </p>

  <div style="margin-top:20px;display:flex;gap:10px;justify-content:center;flex-wrap:wrap;position:relative;z-index:1">
    <a href="https://procta.net"><span style="display:inline-block;padding:8px 20px;border-radius:999px;background:rgba(99,102,241,0.15);color:#818cf8;font-size:13px;font-weight:600;text-decoration:none">🌐 Website</span></a>
    <a href="https://app.procta.net"><span style="display:inline-block;padding:8px 20px;border-radius:999px;background:rgba(56,189,248,0.15);color:#38bdf8;font-size:13px;font-weight:600;text-decoration:none">🚀 App</span></a>
    <a href="#getting-started"><span style="display:inline-block;padding:8px 20px;border-radius:999px;background:rgba(52,211,153,0.15);color:#34d399;font-size:13px;font-weight:600;text-decoration:none">📖 Docs</span></a>
    <a href="mailto:arihantkaul@outlook.com"><span style="display:inline-block;padding:8px 20px;border-radius:999px;background:rgba(148,163,184,0.15);color:#94a3b8;font-size:13px;font-weight:600;text-decoration:none">✉️ Contact</span></a>
  </div>
</div>

<!-- ── Badges ──────────────────────────────────────────── -->
<div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-bottom:28px">
  <img src="https://img.shields.io/badge/electron-^42.0.1-47848F?logo=electron&logoColor=white" alt="Electron">
  <img src="https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/fastapi-0.115-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/supabase-postgresql-3FCF8E?logo=supabase&logoColor=white" alt="Supabase">
  <img src="https://img.shields.io/badge/tests-615_total-6366f1" alt="Tests">
  <img src="https://img.shields.io/badge/version-2.2.1-6366f1" alt="Version">
  <img src="https://img.shields.io/badge/license-proprietary-e11d48" alt="License">
</div>

<!-- ── Tagline ─────────────────────────────────────────── -->
<p align="center" style="font-size:17px;color:#64748b;max-width:640px;margin:0 auto 32px;line-height:1.6">
  A secure, intelligent, and scalable platform for conducting cheat-resistant online examinations.
  <strong style="color:#e2e8f0">42,816 lines of Python</strong> ·
  <strong style="color:#e2e8f0">29 API routers</strong> ·
  <strong style="color:#e2e8f0">45 database migrations</strong>
</p>

</div>

## 📋 Table of Contents

<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin-bottom:32px">

- [🚀 What is Procta?](#-what-is-procta)
- [⚡ Core System](#-core-system)
- [🛡️ Why Procta](#-why-procta)
- [🎓 Student Experience](#-student-experience)
- [👨‍🏫 Admin Experience](#-admin-experience)
- [🧠 AI Proctoring](#-ai-proctoring-capabilities)
- [🏗️ Architecture](#-architecture)
- [📦 Tech Stack](#-tech-stack)
- [📊 Project Status](#-project-status)
- [🚀 Getting Started](#-getting-started)
- [📜 License](#-license)

</div>

---

## 🚀 What is Procta?

<div style="
  background: linear-gradient(135deg, #0f172a, #1a1a2e);
  border-left: 4px solid #818cf8;
  border-radius: 12px;
  padding: 24px 28px;
  margin: 20px 0;
">

**Procta** is a full-stack online proctoring system that combines a **locked-down Electron exam environment** with **real-time AI behavioral monitoring** to ensure exam integrity at scale.

Built for institutions and organizations that require secure, remote, and reliable assessments. From setup to results, every layer is designed to catch cheating without adding friction for honest students.

</div>

---

## ⚡ Core System

<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin:20px 0">

<div style="background:#1e293b;border-radius:12px;padding:24px;border:1px solid rgba(148,163,184,0.1)">

### 🖥️ Desktop Exam Client
**Electron-based locked browser**

A tamper-resistant environment that runs in full kiosk mode — no alt-tab, no shortcuts, no escaping. Students get a clean exam interface with auto-save, timer, question grid, and live proctor connection.

</div>

<div style="background:#1e293b;border-radius:12px;padding:24px;border:1px solid rgba(148,163,184,0.1)">

### 👁️ AI Proctoring Engine
**Python + MediaPipe + YOLOv8**

Real-time behavioral monitoring running as a sidecar process. Face tracking, gaze detection, object detection (phones/books), voice anomaly detection, and identity verification — all processed locally with <150ms latency.

</div>

<div style="background:#1e293b;border-radius:12px;padding:24px;border:1px solid rgba(148,163,184,0.1)">

### 📊 Backend & Dashboard
**FastAPI + Supabase + React**

Full control center for teachers and admins. Live session monitoring, violation timeline, AI risk scoring, question bank management, grade review with evidence, student analytics, and bulk operations — all served through a responsive web dashboard.

</div>

</div>

---

## 🛡️ Why Procta

<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin:20px 0">

<div style="background:rgba(99,102,241,0.06);border:1px solid rgba(99,102,241,0.15);border-radius:10px;padding:20px">
<h4 style="color:#818cf8;margin:0 0 8px">🔒 Exam Integrity by Design</h4>
<p style="color:#94a3b8;font-size:14px;margin:0">Full kiosk lockdown (no switching, no shortcuts). Server-side scoring — client cannot manipulate results. Continuous session validation.</p>
</div>

<div style="background:rgba(56,189,248,0.06);border:1px solid rgba(56,189,248,0.15);border-radius:10px;padding:20px">
<h4 style="color:#38bdf8;margin:0 0 8px">🤖 AI-Based Monitoring</h4>
<p style="color:#94a3b8;font-size:14px;margin:0">Face tracking, gaze detection, head movement analysis. Object detection (phones, books, laptops). Identity verification via face embeddings. Audio anomaly detection.</p>
</div>

<div style="background:rgba(52,211,153,0.06);border:1px solid rgba(52,211,153,0.15);border-radius:10px;padding:20px">
<h4 style="color:#34d399;margin:0 0 8px">🛑 Comprehensive Anti-Cheat</h4>
<p style="color:#94a3b8;font-size:14px;margin:0">Tab switching and focus loss detection. Remote desktop / screen sharing detection. Virtual machine and multi-monitor detection.</p>
</div>

<div style="background:rgba(251,191,36,0.06);border:1px solid rgba(251,191,36,0.15);border-radius:10px;padding:20px">
<h4 style="color:#fbbf24;margin:0 0 8px">📈 Actionable Insights</h4>
<p style="color:#94a3b8;font-size:14px;margin:0">Behavioral risk scoring (0–100). Violation logs with timestamps and evidence screenshots. Detailed PDF audit packets per candidate.</p>
</div>

</div>

---

## 🎓 Student Experience

<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:16px 0">
<div style="background:#1e293b;border-radius:8px;padding:16px;text-align:center"><strong style="color:#e2e8f0">🔑</strong><br><span style="color:#94a3b8;font-size:13px">Simple roll number + access code login</span></div>
<div style="background:#1e293b;border-radius:8px;padding:16px;text-align:center"><strong style="color:#e2e8f0">📐</strong><br><span style="color:#94a3b8;font-size:13px">Guided face calibration before exam</span></div>
<div style="background:#1e293b;border-radius:8px;padding:16px;text-align:center"><strong style="color:#e2e8f0">📝</strong><br><span style="color:#94a3b8;font-size:13px">Clean exam UI with timer + question grid</span></div>
<div style="background:#1e293b;border-radius:8px;padding:16px;text-align:center"><strong style="color:#e2e8f0">💾</strong><br><span style="color:#94a3b8;font-size:13px">Automatic answer saving every 5s</span></div>
<div style="background:#1e293b;border-radius:8px;padding:16px;text-align:center"><strong style="color:#e2e8f0">📤</strong><br><span style="color:#94a3b8;font-size:13px">Reliable submission with retry protection</span></div>
<div style="background:#1e293b;border-radius:8px;padding:16px;text-align:center"><strong style="color:#e2e8f0">⚖️</strong><br><span style="color:#94a3b8;font-size:13px">Appeal unfair flags to teacher</span></div>
</div>

---

## 👨‍🏫 Admin Experience

<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:16px 0">

<div style="background:#1e293b;border-radius:12px;padding:20px;border:1px solid rgba(148,163,184,0.08)">

### 🔴 Live Control
- Monitor active sessions in real time via SSE
- Force-submit exams if needed
- Live camera feed per student
- Chat with candidates during exam
- Broadcast messages to all students

</div>

<div style="background:#1e293b;border-radius:12px;padding:20px;border:1px solid rgba(148,163,184,0.08)">

### 🛠️ Management Tools
- Create and manage questions (MCQ, multi-correct, integer)
- AI-powered question generation (Llama 3.3 70B)
- Question bank with tagging and reuse
- Invite students via email or shareable links
- Per-student question/option randomization
- Exam templates for recurring assessments

</div>

<div style="background:#1e293b;border-radius:12px;padding:20px;border:1px solid rgba(148,163,184,0.08)">

### 🔍 Analytics & Grading
- Risk-based candidate evaluation (0–100)
- Aggregated statistics across exams
- Visual breakdown of violation types
- AI-assisted grade review with evidence packets
- PDF audit trail per session
- Appeal management with teacher notes

</div>

<div style="background:#1e293b;border-radius:12px;padding:20px;border:1px solid rgba(148,163,184,0.08)">

### 🔐 Security & Compliance
- 2FA (email OTP) for teacher accounts
- Session management (list/revoke active sessions)
- Refresh token rotation with replay detection
- Rate-limited endpoints + Turnstile CAPTCHA
- Suspicious login detection and alerts
- Comprehensive auth audit logging

</div>

</div>

---

## 🧠 AI Proctoring Capabilities

<div style="
  display:grid;
  grid-template-columns:repeat(auto-fill,minmax(180px,1fr));
  gap:10px;
  margin:16px 0;
">

<div style="text-align:center;background:rgba(99,102,241,0.06);border-radius:10px;padding:16px">
  <div style="font-size:28px;margin-bottom:4px">👤</div>
  <div style="font-weight:600;color:#e2e8f0;font-size:13px">Face Mesh Tracking</div>
  <div style="color:#64748b;font-size:11px">468 landmarks in real-time</div>
</div>

<div style="text-align:center;background:rgba(56,189,248,0.06);border-radius:10px;padding:16px">
  <div style="font-size:28px;margin-bottom:4px">👁️</div>
  <div style="font-weight:600;color:#e2e8f0;font-size:13px">Eye Tracking</div>
  <div style="color:#64748b;font-size:11px">Gaze + blink detection</div>
</div>

<div style="text-align:center;background:rgba(52,211,153,0.06);border-radius:10px;padding:16px">
  <div style="font-size:28px;margin-bottom:4px">🔄</div>
  <div style="font-weight:600;color:#e2e8f0;font-size:13px">Head Pose Estimation</div>
  <div style="color:#64748b;font-size:11px">Yaw/pitch/roll tracking</div>
</div>

<div style="text-align:center;background:rgba(251,191,36,0.06);border-radius:10px;padding:16px">
  <div style="font-size:28px;margin-bottom:4px">📱</div>
  <div style="font-weight:600;color:#e2e8f0;font-size:13px">Object Detection</div>
  <div style="color:#64748b;font-size:11px">YOLOv8 — phones, books, etc.</div>
</div>

<div style="text-align:center;background:rgba(239,68,68,0.06);border-radius:10px;padding:16px">
  <div style="font-size:28px;margin-bottom:4px">🎭</div>
  <div style="font-weight:600;color:#e2e8f0;font-size:13px">Face Recognition</div>
  <div style="color:#64748b;font-size:11px">InsightFace embeddings</div>
</div>

<div style="text-align:center;background:rgba(168,85,247,0.06);border-radius:10px;padding:16px">
  <div style="font-size:28px;margin-bottom:4px">🎤</div>
  <div style="font-weight:600;color:#e2e8f0;font-size:13px">Audio Monitoring</div>
  <div style="color:#64748b;font-size:11px">Sustained voice detection</div>
</div>

<div style="text-align:center;background:rgba(236,72,153,0.06);border-radius:10px;padding:16px">
  <div style="font-size:28px;margin-bottom:4px">👥</div>
  <div style="font-weight:600;color:#e2e8f0;font-size:13px">Multi-Face Detection</div>
  <div style="color:#64748b;font-size:11px">Unauthorized persons</div>
</div>

<div style="text-align:center;background:rgba(20,184,166,0.06);border-radius:10px;padding:16px">
  <div style="font-size:28px;margin-bottom:4px">🖥️</div>
  <div style="font-weight:600;color:#e2e8f0;font-size:13px">Remote Desktop Detection</div>
  <div style="color:#64748b;font-size:11px">VNC / TeamViewer / RDP</div>
</div>

</div>

---

## 🏎️ Performance

<div style="
  background: linear-gradient(135deg, #0f172a, #1a1a2e);
  border-left: 4px solid #34d399;
  border-radius: 12px;
  padding: 28px 32px;
  margin: 20px 0;
">
  <div style="text-align:center;margin-bottom:6px">
    <span style="font-size:13px;color:#94a3b8;font-weight:600;letter-spacing:0.05em;text-transform:uppercase">Latest verified result · 2026-05-23</span>
  </div>
  <div style="text-align:center;margin-bottom:4px">
    <span style="font-size:32px;font-weight:800;color:#e2e8f0">3,000</span>
    <span style="font-size:18px;color:#94a3b8;font-weight:400"> concurrent students</span>
  </div>
  <div style="text-align:center;font-size:14px;color:#34d399;font-weight:600;margin-bottom:18px">
    Submit p95: 51 ms · Scoring p95: 1.5 s · Errors: 0.00%
  </div>

  <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:16px">
    <thead>
      <tr style="border-bottom:1px solid rgba(148,163,184,0.2)">
        <th style="padding:8px 12px;text-align:left;color:#94a3b8;font-weight:600;font-size:11px;text-transform:uppercase">Metric</th>
        <th style="padding:8px 12px;text-align:right;color:#94a3b8;font-weight:600;font-size:11px;text-transform:uppercase">Phase 2 baseline</th>
        <th style="padding:8px 12px;text-align:right;color:#94a3b8;font-weight:600;font-size:11px;text-transform:uppercase">Latest</th>
        <th style="padding:8px 12px;text-align:right;color:#94a3b8;font-weight:600;font-size:11px;text-transform:uppercase">Improvement</th>
      </tr>
    </thead>
    <tbody>
      <tr style="border-bottom:1px solid rgba(148,163,184,0.08)">
        <td style="padding:8px 12px;color:#e2e8f0;font-weight:500">Submit p95</td>
        <td style="padding:8px 12px;text-align:right;color:#94a3b8;font-family:monospace">397 ms</td>
        <td style="padding:8px 12px;text-align:right;color:#34d399;font-family:monospace">51 ms</td>
        <td style="padding:8px 12px;text-align:right;color:#34d399;font-weight:600">7.8× faster</td>
      </tr>
      <tr style="border-bottom:1px solid rgba(148,163,184,0.08)">
        <td style="padding:8px 12px;color:#e2e8f0;font-weight:500">Scoring p95</td>
        <td style="padding:8px 12px;text-align:right;color:#94a3b8;font-family:monospace">14.2 s</td>
        <td style="padding:8px 12px;text-align:right;color:#34d399;font-family:monospace">1.5 s</td>
        <td style="padding:8px 12px;text-align:right;color:#34d399;font-weight:600">9.2× faster</td>
      </tr>
      <tr style="border-bottom:1px solid rgba(148,163,184,0.08)">
        <td style="padding:8px 12px;color:#e2e8f0;font-weight:500">Scoring completion</td>
        <td style="padding:8px 12px;text-align:right;color:#94a3b8;font-family:monospace">13%</td>
        <td style="padding:8px 12px;text-align:right;color:#34d399;font-family:monospace">100%</td>
        <td style="padding:8px 12px;text-align:right;color:#34d399;font-weight:600">→ full</td>
      </tr>
      <tr>
        <td style="padding:8px 12px;color:#e2e8f0;font-weight:500">Error rate</td>
        <td style="padding:8px 12px;text-align:right;color:#94a3b8;font-family:monospace">0.00%</td>
        <td style="padding:8px 12px;text-align:right;color:#34d399;font-family:monospace">0.00%</td>
        <td style="padding:8px 12px;text-align:right;color:#94a3b8;font-weight:600">maintained</td>
      </tr>
    </tbody>
  </table>

  <div style="font-size:12px;color:#64748b;line-height:1.6;border-top:1px solid rgba(148,163,184,0.12);padding-top:14px">
    <strong style="color:#94a3b8">Hardware:</strong> 1× Hostinger KVM 4 — 4 vCPU / 16 GB RAM / Indian datacenter<br>
    <strong style="color:#94a3b8">Cost:</strong> ₹699 / month (~ $8.50)
  </div>
</div>

---

## 🏗️ Architecture

```
                          ┌──────────────────┐
                          │   Marketing Site  │
                          │   procta.net      │
                          │   (Vercel/React)  │
                          └────────┬─────────┘
                                   │
┌──────────────────────────────────┼──────────────────────────────────┐
│                     APP SERVER (app.procta.net)                     │
│                                                                     │
│  ┌──────────────┐   ┌─────────────────┐   ┌────────────────────┐   │
│  │   Electron   │   │   AI Proctor    │   │   FastAPI Backend  │   │
│  │   Exam App   │──▶│   (Python)      │──▶│   (29 routers)     │   │
│  │  (kiosk)     │   │  MediaPipe      │   │                    │   │
│  │              │   │  YOLOv8         │   │  ┌──────────────┐  │   │
│  │  main.js     │   │  InsightFace    │   │  │  Admin Dash  │  │   │
│  │  preload.js  │   │  OpenCV         │   │  │  (React SPA) │  │   │
│  │  renderer/   │   │                 │   │  └──────────────┘  │   │
│  └──────────────┘   └─────────────────┘   │                    │   │
│                                            │  ┌──────────────┐  │   │
│                                            │  │  Student UI  │  │   │
│                                            │  │  (vanilla)   │  │   │
│                                            │  └──────────────┘  │   │
│                                            └─────────┬──────────┘   │
│                                                      │              │
│                                   ┌──────────────────┼──────┐       │
│                                   │     Supabase     │      │       │
│                                   │  (PostgreSQL +   │      │       │
│                                   │   Auth + Realtime)│      │       │
│                                   └──────────────────┘      │       │
│                                                              │       │
│  Infrastructure: Docker · Caddy (auto HTTPS) · 2GB droplet ·       │
│  GitHub Actions CI/CD · Resend (email) · Cloudflare Turnstile      │
└────────────────────────────────────────────────────────────────────┘
```

---

## 📦 Tech Stack

<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin:16px 0">

<div style="background:#1e293b;border-radius:10px;padding:18px;border-top:3px solid #818cf8">
<h4 style="margin:0 0 8px;color:#818cf8;font-size:15px">🎨 Frontend</h4>
<div style="color:#94a3b8;font-size:13px;line-height:1.7">
Electron 42<br>
React (dashboard SPA)<br>
Vanilla HTML/CSS/JS<br>
IBM Plex Sans + Mono<br>
Periwinkle Blue design system<br>
Responsive (mobile-first)
</div>
</div>

<div style="background:#1e293b;border-radius:10px;padding:18px;border-top:3px solid #34d399">
<h4 style="margin:0 0 8px;color:#34d399;font-size:15px">⚙️ Backend</h4>
<div style="color:#94a3b8;font-size:13px;line-height:1.7">
FastAPI (Python 3.12)<br>
29 API router modules<br>
JWT auth + refresh rotation<br>
WebSocket + SSE streaming<br>
Rate limiting (slowapi)<br>
Supabase (PostgreSQL)
</div>
</div>

<div style="background:#1e293b;border-radius:10px;padding:18px;border-top:3px solid #fbbf24">
<h4 style="margin:0 0 8px;color:#fbbf24;font-size:15px">🧠 AI / ML</h4>
<div style="color:#94a3b8;font-size:13px;line-height:1.7">
MediaPipe Face Mesh<br>
YOLOv8 (object detection)<br>
InsightFace (recognition)<br>
OpenCV (processing)<br>
ONNX Runtime (inference)<br>
Groq (question generation)
</div>
</div>

<div style="background:#1e293b;border-radius:10px;padding:18px;border-top:3px solid #38bdf8">
<h4 style="margin:0 0 8px;color:#38bdf8;font-size:15px">🏗️ Infrastructure</h4>
<div style="color:#94a3b8;font-size:13px;line-height:1.7">
Docker + Docker Compose<br>
Caddy (auto HTTPS)<br>
DigitalOcean droplet (2GB)<br>
Vercel (marketing site)<br>
GitHub Actions CI/CD<br>
Resend (transactional email)
</div>
</div>

</div>

---

## 📊 Project Status

<div style="background:#1e293b;border-radius:12px;padding:24px;margin:16px 0">

### Release v2.2.1 — Development / Pre-Production

<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:16px">

<div>

#### ✅ Completed
- Full teacher dashboard (Live, Results, Questions, Analytics, Chat, Tools, Billing, Security tabs)
- Student exam experience with AI proctoring
- LTI 1.3 integration (Deep Linking, NRPS, Grade Passback)
- AI question generation (Llama 3.3 70B)
- OAuth (Google, Microsoft) for teachers + students
- Email 2FA, session management, password reset
- 578 automated tests passing
- Refresh token rotation with replay detection
- Design system with 3 themes (dark, dark-OLED, light)
- Responsive mobile breakpoints
- React SPA dashboard alongside vanilla HTML

</div>

<div>

#### 🔄 In Progress
- macOS code signing (awaiting Apple Developer account)
- Google Classroom OAuth (needs Cloud Console project)
- Live Sessions 3-pane layout port
- Student exam window design port

#### 📋 Known Items
- Build: unsigned → Gatekeeper/SmartScreen warnings
- Audit: artifact of "3.8/10" score was based on 26 false claims out of 38 — actual readiness ~8.5/10
- Refresh token TTL: 30 days (configurable)
- 44 `alert()` dialogs to replace with modal pattern
- Some silent `catch` blocks in dashboard JS

</div>

</div>

</div>

### Test Suite

<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:16px 0">

<div style="text-align:center;background:rgba(52,211,153,0.08);border-radius:10px;padding:16px">
  <div style="font-size:32px;font-weight:700;color:#34d399">615</div>
  <div style="color:#94a3b8;font-size:12px">Total Tests</div>
</div>

<div style="text-align:center;background:rgba(34,197,94,0.08);border-radius:10px;padding:16px">
  <div style="font-size:32px;font-weight:700;color:#22c55e">578</div>
  <div style="color:#94a3b8;font-size:12px">Passing</div>
</div>

<div style="text-align:center;background:rgba(251,191,36,0.08);border-radius:10px;padding:16px">
  <div style="font-size:32px;font-weight:700;color:#fbbf24">33</div>
  <div style="color:#94a3b8;font-size:12px">Skipped</div>
</div>

<div style="text-align:center;background:rgba(99,102,241,0.08);border-radius:10px;padding:16px">
  <div style="font-size:32px;font-weight:700;color:#818cf8">4</div>
  <div style="color:#94a3b8;font-size:12px">Failed</div>
</div>

<div style="text-align:center;background:rgba(148,163,184,0.08);border-radius:10px;padding:16px">
  <div style="font-size:32px;font-weight:700;color:#94a3b8">45</div>
  <div style="color:#94a3b8;font-size:12px">Migrations</div>
</div>

</div>

---

## 🚀 Getting Started

### Prerequisites
- Python 3.12+, Node.js 20+, Docker (optional)
- Supabase project (PostgreSQL)
- Resend API key (email)

### Quick Start

<div style="background:#0f172a;border-radius:10px;padding:18px;margin:12px 0">

```bash
# 1. Clone and install
git clone https://github.com/ArihantK15/proctor-browser.git
cd proctor-browser

# 2. Backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in your secrets
uvicorn app.main:app --reload

# 3. Frontend (Electron)
npm install
npm start

# 4. Run tests
python -m pytest tests/
```

</div>

### Docker Deploy

<div style="background:#0f172a;border-radius:10px;padding:18px;margin:12px 0">

```bash
docker compose build api && docker compose up -d api
```

</div>

### Quality Gate

Before any deploy:

```bash
MODE=full scripts/quality_check.sh          # full release gate
INTERVAL=60 MODE=fast scripts/continuous_review.sh   # dev loop
```

---

## 📜 License

<div style="background:#1e293b;border-radius:12px;padding:20px;margin:16px 0;border:1px solid rgba(239,68,68,0.15)">

**Procta — Proprietary Software License**

Copyright © 2024-2026 Arihant Kaul. All rights reserved.

This software and associated documentation are proprietary and confidential. Unauthorized copying, modification, distribution, or use is strictly prohibited without explicit written permission.

</div>

---

<div align="center" style="margin:32px 0;color:#475569;font-size:13px">

Built with ❤️ by Arihant Kaul | [arihantkaul@outlook.com](mailto:arihantkaul@outlook.com)

</div>