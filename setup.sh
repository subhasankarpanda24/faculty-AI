#!/bin/bash
# ═══════════════════════════════════════════
# setup.sh — NIST Faculty AI Setup Script
# ═══════════════════════════════════════════
# Usage: bash setup.sh

set -e

echo "═══════════════════════════════════════════"
echo "  NIST Faculty AI — Setup"
echo "═══════════════════════════════════════════"
echo

# Step 1: Install dependencies
echo "[1/3] Installing Python dependencies..."
pip install -r requirements.txt
echo "✅ Dependencies installed."
echo

# Step 2: Run scraper & generate Excel
echo "[2/3] Running faculty scraper..."
python run_scraper.py
echo "✅ Faculty data generated."
echo

# Step 3: Start the app
echo "[3/3] Starting Faculty AI server..."
python app.py
