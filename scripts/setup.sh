#!/bin/bash
# =============================================================================
# MietRadar — Setup Script
# =============================================================================
# Automates: venv creation, dependency install, config scaffolding, data dirs.
# Usage:  bash scripts/setup.sh
# =============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "📡 MietRadar Setup"
echo "=================================="
echo ""

# 1. Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 Creating Python virtual environment..."
    python3 -m venv venv
    echo "   ✅ venv created."
else
    echo "📦 Virtual environment already exists."
fi

# 2. Activate venv and install dependencies
echo "📦 Installing Python dependencies..."
source venv/bin/activate
pip install -r requirements.txt --quiet
echo "   ✅ Dependencies installed."

# 3. Create data directory if it doesn't exist
echo "📁 Ensuring data/ directory exists..."
mkdir -p data
echo "   ✅ data/ ready."

# 4. Scaffold config files from examples if they don't exist
echo "📋 Scaffolding config files..."

if [ ! -f "config/.env" ]; then
    cp config/.env.example config/.env
    echo "   ✅ Created config/.env from .env.example — EDIT THIS with your credentials!"
else
    echo "   ⏭  config/.env already exists."
fi

if [ ! -f "config/message.txt" ]; then
    cp config/message.txt.example config/message.txt
    echo "   ✅ Created config/message.txt — EDIT THIS with your message template!"
else
    echo "   ⏭  config/message.txt already exists."
fi

if [ ! -f "config/wg_blacklist.txt" ]; then
    cp config/wg_blacklist.txt.example config/wg_blacklist.txt
    echo "   ✅ Created config/wg_blacklist.txt"
else
    echo "   ⏭  config/wg_blacklist.txt already exists."
fi

if [ ! -f "config/immo_blacklist.txt" ]; then
    cp config/immo_blacklist.txt.example config/immo_blacklist.txt
    echo "   ✅ Created config/immo_blacklist.txt"
else
    echo "   ⏭  config/immo_blacklist.txt already exists."
fi

echo ""
echo "=================================="
echo "✅ Setup complete!"
echo "=================================="
echo ""
echo "Next steps:"
echo "  1. Edit config/.env with your credentials"
echo "  2. Edit config/message.txt with your application message"
echo "  3. (Optional) Edit config/llm_persona.txt for LLM personalisation"
echo ""
echo "To run the WG-Gesucht bot:"
echo "   source venv/bin/activate"
echo "   python src/wg-gesucht.py"
echo ""
echo "To run the ImmobilienScout24 bot:"
echo "   source venv/bin/activate"
echo "   python src/immoscout.py"
echo ""
