===========================================
  SchoolTrack Pro — PWA Setup Guide
===========================================

INSTALL:
  pip install -r requirements.txt

RUN:
  python app.py
  Open: http://localhost:5000

ADD TO PHONE SCREEN (PWA):
  Android: Open in Chrome → 3 dots menu → "Add to Home Screen"
  iPhone:  Open in Safari → Share → "Add to Home Screen"

MAKE.COM WEBHOOK:
  URL: http://YOUR_IP:5000/webhook/daily-report
  Method: POST
  Body: {"secret": "schooltrack_webhook_2026", "school_id": 1}

WHATSAPP SETUP:
  1. Go to Settings tab in app
  2. Enter your Meta WA Token
  3. Enter your Phone Number ID
  4. Save — alerts will start going automatically

DEPLOY ONLINE (Free):
  1. Push code to GitHub
  2. Go to render.com → New Web Service
  3. Connect GitHub repo
  4. Build: pip install -r requirements.txt
  5. Start: python app.py
  6. Your app is live at https://yourapp.onrender.com

===========================================
