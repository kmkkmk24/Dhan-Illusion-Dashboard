from __future__ import annotations

import logging
from datetime import date
from html import escape
from typing import Any

import httpx

from backend.config import get_config
from backend.models.database import get_session_factory
from backend.models.tables import Instrument, SectorScore, Signal
from backend.services.sector_mapping import get_stocks_in_sector

logger = logging.getLogger(__name__)
_TELEGRAM_OFFSET: int = 0


def _telegram_settings() -> tuple[str, str]:
    cfg = get_config().get("telegram") or {}
    token = str(cfg.get("bot_token") or "").strip()
    chat_id = str(cfg.get("chat_id") or "").strip()
    return token, chat_id


def telegram_is_configured() -> bool:
    token, chat_id = _telegram_settings()
    return bool(token and chat_id)


async def send_telegram_message(message: str, *, parse_mode: str = "HTML") -> dict[str, Any]:
    return await send_telegram_message_ex(
        message=message,
        parse_mode=parse_mode,
        reply_markup=None,
    )


async def send_telegram_message_ex(
    *,
    message: str,
    parse_mode: str = "HTML",
    reply_markup: dict[str, Any] | None = None,
    chat_id: str | None = None,
) -> dict[str, Any]:
    token, configured_chat_id = _telegram_settings()
    target_chat = str(chat_id or configured_chat_id or "").strip()
    if not token or not target_chat:
        return {"ok": False, "reason": "telegram_not_configured"}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, json=payload)
        data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        if response.status_code >= 400 or not data.get("ok"):
            logger.warning("Telegram send failed: status=%s body=%s", response.status_code, data)
            return {
                "ok": False,
                "reason": "telegram_api_error",
                "status_code": response.status_code,
                "error": data.get("description"),
            }
        return {"ok": True, "message_id": (data.get("result") or {}).get("message_id")}
    except Exception as exc:
        logger.exception("Telegram send exception")
        return {"ok": False, "reason": "telegram_exception", "error": str(exc)}


def _trend_emoji(trend: str) -> str:
    t = str(trend or "").lower()
    if "strong" in t:
        return "🟢"
    if "moderate" in t:
        return "🟡"
    if "weak" in t:
        return "🟠"
    if "bear" in t:
        return "🔴"
    return "⚪"


def _fmt_pct_cell(value: Any) -> str:
    try:
        if value is None:
            return "--"
        v = float(value)
        if v <= 1.0:
            v *= 100.0
        return f"{v:>5.0f}%"
    except Exception:
        return "  -- "


def _short_trend(value: Any) -> str:
    t = str(value or "").strip().lower()
    if t.startswith("strong"):
        return "STR"
    if t.startswith("moderate"):
        return "MOD"
    if t.startswith("weak"):
        return "WEK"
    if t.startswith("bear"):
        return "BER"
    return "---"


def _resolve_trend_code(row: dict) -> str:
    trend_raw = str(row.get("trend") or "").strip()
    if trend_raw and trend_raw not in {"—", "-", "--", "---"}:
        return _short_trend(trend_raw)
    try:
        score = float(row.get("combined_score") or 0)
        if score > 1.0:
            score = score / 100.0
    except Exception:
        score = 0.0
    if score >= 0.65:
        return "STR"
    if score >= 0.45:
        return "MOD"
    if score >= 0.25:
        return "WEK"
    return "BER"


def _build_sector_table_lines(results: list[dict]) -> list[str]:
    # Keep table narrow for Telegram mobile width.
    ranked = sorted(results, key=lambda x: float(x.get("combined_score") or 0), reverse=True)
    name_width = 12
    header = f"{'SECTOR':<{name_width}} {'H':>3} {'D':>3} {'W':>3} {'M':>3} {'T':>3} {'S':>4}"
    sep = "-" * len(header)
    lines = [header, sep]
    for row in ranked:
        name = str(row.get("sector_name") or "-").replace("Nifty ", "").strip()
        if len(name) > name_width:
            name = name[: name_width - 1] + "…"
        hourly = _fmt_pct_cell(row.get("hourly_rs") or row.get("daily_rs")).strip()
        daily = _fmt_pct_cell(row.get("daily_rs")).strip()
        weekly = _fmt_pct_cell(row.get("weekly_rs")).strip()
        monthly = _fmt_pct_cell(row.get("monthly_rs")).strip()
        trend = _resolve_trend_code(row)
        score = _fmt_pct_cell(row.get("combined_score")).strip()
        lines.append(f"{name:<{name_width}} {hourly:>3} {daily:>3} {weekly:>3} {monthly:>3} {trend:>3} {score:>4}")
    return lines


def build_sector_report_message(results: list[dict], *, report_date: date, top_n: int = 5) -> str:
    if not results:
        return f"📊 <b>Sector Analysis Report</b> <i>({report_date.isoformat()})</i>\nNo sector data available."

    ranked = sorted(results, key=lambda x: float(x.get("combined_score") or 0), reverse=True)
    table_lines = _build_sector_table_lines(ranked)

    lines: list[str] = [
        f"📊 <b>Sector Analysis Report</b> <i>({report_date.isoformat()})</i>",
        f"Total sectors analyzed: <b>{len(ranked)}</b>",
        "",
        "<b>Dashboard-style Sector Matrix</b>",
        "<pre>",
        *table_lines,
        "</pre>",
        "",
        "Tap a sector button below to get stock-level drill-down.",
    ]

    return "\n".join(lines)


def build_sector_report_keyboard(results: list[dict], *, top_n: int = 5) -> dict[str, Any] | None:
    if not results:
        return None
    ranked = sorted(results, key=lambda x: float(x.get("combined_score") or 0), reverse=True)
    # Expose drill-down access for all sectors in ranked order.
    top = ranked
    buttons: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for s in top:
        name = str(s.get("sector_name") or "").strip()
        if not name:
            continue
        row.append({"text": name.replace("Nifty ", ""), "callback_data": f"sec:{name}"})
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return {"inline_keyboard": buttons} if buttons else None


def _load_latest_sector_rows() -> list[dict]:
    session = get_session_factory()()
    try:
        latest_date = session.query(SectorScore.date).order_by(SectorScore.date.desc()).first()
        if not latest_date:
            return []
        rows = (
            session.query(SectorScore)
            .filter(SectorScore.date == latest_date[0])
            .order_by(SectorScore.combined_score.desc())
            .all()
        )
        return [
            {
                "sector_name": r.sector_name,
                "combined_score": r.combined_score,
                "trend": "Strong" if (r.combined_score or 0) >= 0.65 else "Moderate" if (r.combined_score or 0) >= 0.45 else "Weak",
                "date": str(r.date),
            }
            for r in rows
        ]
    finally:
        session.close()


def _build_sector_drilldown_message(sector_name: str) -> str:
    sector = str(sector_name or "").strip()
    stocks = get_stocks_in_sector(sector)
    if not stocks:
        return f"🔍 <b>{escape(sector)}</b>\nNo mapped stocks found."

    session = get_session_factory()()
    try:
        from sqlalchemy import or_

        instruments = (
            session.query(Instrument)
            .filter(
                or_(
                    Instrument.trading_symbol.in_(stocks),
                    Instrument.symbol.in_(stocks),
                ),
                Instrument.exchange == "NSE",
            )
            .all()
        )
        if not instruments:
            return f"📌 <b>{escape(sector)}</b>\nNo instrument records found."

        symbol_keys = []
        for inst in instruments:
            if inst.trading_symbol:
                symbol_keys.append(str(inst.trading_symbol))
            if inst.symbol:
                symbol_keys.append(str(inst.symbol))

        sigs = (
            session.query(Signal)
            .filter(Signal.symbol.in_(symbol_keys), Signal.status.in_(["active", "exploded"]))
            .all()
        )
    finally:
        session.close()

    sig_map = {str(s.symbol): s for s in sigs}
    rows: list[dict] = []
    for inst in instruments:
        tsym = str(inst.trading_symbol or inst.symbol or "").strip()
        sig = sig_map.get(tsym) or sig_map.get(str(inst.symbol or ""))
        rows.append({
            "symbol": tsym or str(inst.symbol or "-"),
            "name": str(inst.display_name or inst.symbol or tsym or "-"),
            "is_fno": bool(inst.is_fno),
            "lot": int(inst.lot_size or 0),
            "signal": sig,
        })

    rows.sort(
        key=lambda r: (
            not bool(r["signal"]),
            -float((r["signal"].current_score if r["signal"] else 0) or 0),
            r["symbol"],
        )
    )
    active_count = sum(1 for r in rows if r["signal"])
    lines = [
        f"📌 <b>{escape(sector)}</b>",
        f"Constituents: <b>{len(rows)}</b> · Active signals: <b>{active_count}</b>",
        "",
        "<pre>",
        f"{'SYMBOL':<10} {'NAME':<16} {'TP':<3} {'LOT':>5} {'SIGNAL':<10}",
        "-" * 50,
    ]

    for row in rows[:30]:
        sym = str(row["symbol"])[:10]
        name = str(row["name"]).replace("Limited", "").replace("Ltd", "").strip()
        if len(name) > 16:
            name = name[:15] + "…"
        tp = "F&O" if row["is_fno"] else "EQ"
        lot = str(row["lot"] or "-")
        sig = row["signal"]
        if sig:
            score = f"{float(sig.current_score or 0) * 100:.0f}%"
            raw_type = str(sig.signal_type or "").lower()
            stype = "BRK" if raw_type == "explosion" else "VCP"
            signal_txt = f"{stype}-{score}"
        else:
            signal_txt = "-"
        lines.append(f"{sym:<10} {name:<16} {tp:<3} {lot:>5} {signal_txt:<10}")

    lines.append("</pre>")
    if len(rows) > 30:
        lines.append(f"... showing first 30 of {len(rows)}")
    return "\n".join(lines)


async def send_sector_report(results: list[dict], *, report_date: date, top_n: int = 5) -> dict[str, Any]:
    message = build_sector_report_message(results, report_date=report_date, top_n=top_n)
    keyboard = build_sector_report_keyboard(results, top_n=top_n)
    return await send_telegram_message_ex(message=message, parse_mode="HTML", reply_markup=keyboard)


async def _telegram_api_call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    token, _ = _telegram_settings()
    if not token:
        return {"ok": False, "reason": "telegram_not_configured"}
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, json=payload)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code >= 400:
            return {"ok": False, "status_code": resp.status_code, "error": data.get("description")}
        return data
    except Exception as exc:
        return {"ok": False, "reason": "telegram_exception", "error": str(exc)}


async def process_telegram_callbacks() -> dict[str, Any]:
    """Poll Telegram updates and respond to sector drill-down button clicks."""
    global _TELEGRAM_OFFSET
    if not telegram_is_configured():
        return {"ok": False, "reason": "telegram_not_configured"}

    token, _ = _telegram_settings()
    params = {"timeout": 0, "allowed_updates": ["callback_query"]}
    if _TELEGRAM_OFFSET > 0:
        params["offset"] = _TELEGRAM_OFFSET
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, params=params)
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as exc:
        return {"ok": False, "reason": "telegram_exception", "error": str(exc)}

    if not payload.get("ok"):
        return {"ok": False, "reason": "telegram_api_error", "error": payload.get("description")}

    updates = payload.get("result") or []
    handled = 0
    for upd in updates:
        upd_id = int(upd.get("update_id") or 0)
        if upd_id:
            _TELEGRAM_OFFSET = max(_TELEGRAM_OFFSET, upd_id + 1)
        cb = upd.get("callback_query") or {}
        data = str(cb.get("data") or "")
        if not data.startswith("sec:"):
            continue
        sector_name = data.split("sec:", 1)[1].strip()
        msg = cb.get("message") or {}
        chat_id = ((msg.get("chat") or {}).get("id"))
        cb_id = cb.get("id")
        if not chat_id or not cb_id or not sector_name:
            continue

        detail = _build_sector_drilldown_message(sector_name)
        await send_telegram_message_ex(message=detail, parse_mode="HTML", chat_id=str(chat_id))
        await _telegram_api_call("answerCallbackQuery", {"callback_query_id": cb_id, "text": f"{sector_name} details sent"})
        handled += 1

    return {"ok": True, "handled": handled, "updates": len(updates)}
