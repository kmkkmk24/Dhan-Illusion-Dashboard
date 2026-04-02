# 🎯 Dhan Illusion Dashboard

A professional **Trade Suggestion & Tracking Platform** for Swing Trades and Stock Options (F&O) using Dhan HQ APIs.

![Python](https://img.shields.io/badge/python-v3.8+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

## 🌟 Features

### 📊 **Advanced Scanning**
- **F&O Directional Scanner** - Sector-first approach with 75m candles
- **Swing Trade Scanner** - Consolidation detection with Bollinger Bands & ATR
- **Sector Analysis** - Multi-timeframe relative strength analysis
- **Real-time Market Data** - Live price feeds and option chain data

### 🤖 **Automation**
- **Auto Token Management** - TOTP-based automatic token generation
- **Scheduled Scanning** - Market hours automation with IST timezone
- **Background Processing** - Non-blocking async operations
- **Smart Revalidation** - Automatic signal validation with fresh data

### 💼 **Trade Management**
- **Signal Tracking** - Active, exploded, and invalidated signals
- **Trade Journal** - Complete trade history with P&L tracking
- **Risk Management** - Position sizing and stop-loss automation
- **Performance Analytics** - Win rate, avg returns, and drawdown analysis

### 🔐 **Enterprise Security**
- **Environment Variables** - Secure credential management
- **TOTP Authentication** - Automatic token generation
- **No Hardcoded Secrets** - Production-ready security practices
- **Multi-user Ready** - Each user manages their own credentials

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- Dhan Trading Account
- TOTP enabled on Dhan account

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/kmkkmk24/Dhan-Illusion-Dashboard.git
   cd Dhan-Illusion-Dashboard
   ```

2. **Install dependencies**
   ```bash
   pip3 install -r requirements.txt
   ```

3. **Setup environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

4. **Configure application**
   ```bash
   cp config.yaml.example config.yaml
   ```

5. **Run the application**
   ```bash
   python -m backend.main
   ```

6. **Open in browser**
   ```
   http://localhost:8000
   ```

## 📋 Configuration

### Environment Variables

```bash
# Dhan API Credentials
DHAN_CLIENT_ID="your_client_id"
DHAN_PIN="your_6_digit_pin"
DHAN_TOTP_SECRET="your_totp_secret"

# Optional: Telegram Notifications
TELEGRAM_BOT_TOKEN="your_bot_token"
TELEGRAM_CHAT_ID="your_chat_id"
```

### TOTP Setup

1. Login to [Dhan Web](https://web.dhan.co/)
2. Go to **My Profile** → **Access DhanHQ APIs**
3. Click **Setup TOTP** and scan QR code
4. Save the TOTP secret for `DHAN_TOTP_SECRET`

## 🏗️ Architecture

```
dhan/
├── backend/
│   ├── api/           # FastAPI endpoints
│   ├── models/        # Database models & schemas
│   ├── services/      # Business logic & external APIs
│   └── config.py      # Configuration management
├── frontend/          # Web UI (HTML/CSS/JS)
├── data/             # Database & backups
└── docs/             # Documentation
```

## 🔄 Token Management

The application uses **automatic token management**:

- **Daily Generation**: Fresh tokens generated at 6 AM IST
- **TOTP Integration**: No manual token copying required
- **Fallback Renewal**: Uses renewal endpoint when possible
- **Error Handling**: Comprehensive logging and retry logic

## 📊 Scanning Strategy

### F&O Scanner
- **Sector Analysis**: Identifies top momentum sectors
- **75m Candles**: Optimal timeframe for F&O setups
- **EMA Confluence**: 20/50 EMA trend confirmation
- **Directional Bias**: Separate CE/PE sector selection

### Swing Scanner
- **ChartInk-style VCP** (config `swing_scan_mode: chartink`): weekly inside bar, tight week, weekly SMA stack, 85%×250d high, volume.
- **Prefiltered universe**: each **Swing Scan** run rebuilds `data/swing_universe_latest.csv` (filters 1–4) from Dhan when `swing_universe.auto_rebuild_before_scan: true` in `config.yaml`, then runs VCP on that list. You can still run `PYTHONPATH=. python3 scripts/swing_universe_bench.py` manually. If rebuild is off or fails and `latest.csv` is missing, swing scan falls back to the full ES universe + smart filter.
- **Consolidation / Minervini mode**: `swing_scan_mode: minervini` uses Stage 2 + contraction gates.

## 🛠️ Development

### Running in Development Mode

```bash
# Install development dependencies
pip3 install -r requirements.txt

# Run with auto-reload
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

### Testing

```bash
# Run tests
pytest

# Run with coverage
pytest --cov=backend
```

## 📈 Production Deployment

### Environment Setup
```bash
export APP_ENV=production
export LOG_LEVEL=INFO
export HOST=0.0.0.0
export PORT=8000
```

### Docker Deployment
```bash
# Build image
docker build -t dhan-illusion-dashboard .

# Run container
docker run -d -p 8000:8000 --env-file .env dhan-illusion-dashboard
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## ⚠️ Disclaimer

This software is for educational and personal use only. Trading in financial markets involves substantial risk. The developers are not responsible for any financial losses incurred through the use of this software.

## 🙏 Acknowledgments

- [Dhan HQ](https://dhanhq.co/) for providing excellent trading APIs
- [FastAPI](https://fastapi.tiangolo.com/) for the amazing web framework
- [Pandas](https://pandas.pydata.org/) for data processing capabilities

---

**Built with ❤️ for the trading community**