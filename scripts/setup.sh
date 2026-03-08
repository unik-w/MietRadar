#!/bin/bash
# =============================================================================
# IMMO BOT - Setup Script
# =============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "🏠 Immo Bot Setup"
echo "=================================="

# 1. Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 Creating Python virtual environment..."
    python3 -m venv venv
fi

# 2. Activate venv and install dependencies
echo "📦 Installing Python dependencies..."
source venv/bin/activate
pip install -r requirements.txt --quiet

# 3. Create Scrapy project if it doesn't exist
if [ ! -d "immobot" ]; then
    echo "🕷️  Creating Scrapy project..."
    scrapy startproject immobot
fi

# 4. Copy scripts into Scrapy project
echo "📋 Copying scripts to Scrapy project..."
cp immo.py immobot/immo.py
cp submit.py immobot/submit.py
cp wg-gesucht.py immobot/wg-gesucht.py
cp submit_wg.py immobot/submit_wg.py
cp immo_spider.py immobot/immobot/spiders/immo_spider.py
cp wg-gesucht-spider.py immobot/immobot/spiders/wg_gesucht_spider.py
cp message.txt immobot/message.txt 2>/dev/null || true
cp .env immobot/.env 2>/dev/null || true

# 5. Check .env
if [ ! -f ".env" ]; then
    echo ""
    echo "⚠️  No .env file found!"
    echo "   Copy .env.example to .env and fill in your credentials:"
    echo "   cp .env.example .env"
    echo "   Then edit .env with your actual values."
    exit 1
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "To run the WG-Gesucht bot:"
echo "   source venv/bin/activate"
echo "   cd immobot"
echo "   python wg-gesucht.py"
echo ""
echo "To run the ImmobilienScout bot:"
echo "   source venv/bin/activate"
echo "   cd immobot"
echo "   python immo.py"
