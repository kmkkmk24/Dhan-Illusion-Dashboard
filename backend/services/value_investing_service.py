import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from backend.config import get_config
from backend.models.tables import Instrument, ValueCandidate
from backend.services.dhan_client import DhanClient
from backend.services.moneycontrol_client import MoneycontrolClient
from backend.services.tapetide_mcp_client import TapetideMcpClient
from backend.services.tickertape_client import TickertapeClient

logger = logging.getLogger(__name__)


class ValueInvestingService:
    def __init__(self, db: Session):
        self.db = db
        cfg = get_config().get("value_investing") or {}
        self.cfg = cfg
        self.max_candidates = int(cfg.get("max_candidates", 200))
        self.page_size = int(cfg.get("page_size", 50))
        self.max_pages = int(cfg.get("max_pages", 6))
        self.min_score = float(cfg.get("min_score", 0.5))

        self.weights = cfg.get("weights") or {
            "valuation": 0.25,
            "quality": 0.35,
            "growth": 0.25,
            "ownership": 0.15,
        }

        self.thresholds = cfg.get("thresholds") or {
            "min_roce": 15,
            "min_roe": 12,
            "max_debt_to_eq": 1,
            "max_pe": 35,
            "max_pb": 8,
            "min_operating_margin": 12,
            "min_promoter_holding": 25,
            "min_mcap": 300,
        }

        self.score_caps = cfg.get("score_caps") or {
            "roce_best": 30,
            "roe_best": 25,
            "debt_best": 0.5,
            "pe_best": 12,
            "pb_best": 2,
            "margin_best": 25,
            "promoter_best": 60,
        }
        self.sources = cfg.get("sources") or ["tapetide"]
        self.valuation_range = cfg.get("valuation_range") or {
            "pe_low": 12,
            "pe_high": 20,
            "industry_low_factor": 0.8,
            "industry_high_factor": 1.0,
        }
        self.dcf_cfg = cfg.get("dcf") or {
            "years": 5,
            "discount_rate": 0.12,
            "terminal_multiple": 12,
            "growth_floor": 0.04,
            "growth_cap": 0.20,
            "growth_buffer": 0.05,
        }
        self.time_cfg = cfg.get("time_to_fair") or {
            "fixed_growth_rate": 0.12,
            "min_growth": 0.04,
            "max_growth": 0.25,
            "max_years": 10,
        }

    async def refresh(self) -> dict[str, Any]:
        client = TapetideMcpClient()
        try:
            results = await self._fetch_candidates(client)
            await self._apply_dhan_ltp(results)
            stored = self._store_candidates(results)
            self.db.commit()
            return {
                "ok": True,
                "scanned": len(results),
                "stored": stored,
            }
        finally:
            await client.close()

    async def get_details(self, candidate_id: int) -> dict[str, Any] | None:
        candidate = self.db.query(ValueCandidate).filter(ValueCandidate.id == candidate_id).first()
        if not candidate:
            return None

        raw = self._parse_raw_data(candidate.raw_data)
        ltp = raw.get("Ltp")
        ltp_source = raw.get("LtpSource")
        details = {
            "candidate": {
                "id": candidate.id,
                "security_id": candidate.security_id,
                "symbol": candidate.symbol,
                "display_name": candidate.display_name,
                "exchange": candidate.exchange,
                "sector": candidate.sector,
                "isin": candidate.isin,
                "score": candidate.score,
                "valuation_score": candidate.valuation_score,
                "quality_score": candidate.quality_score,
                "growth_score": candidate.growth_score,
                "ownership_score": candidate.ownership_score,
                "market_cap": candidate.market_cap,
                "ltp": ltp,
                "ltp_source": ltp_source,
                "pe": candidate.pe,
                "pb": candidate.pb,
                "roce": candidate.roce,
                "roe": candidate.roe,
                "debt_to_eq": candidate.debt_to_eq,
                "operating_margin": candidate.operating_margin,
                "revenue_growth": candidate.revenue_growth,
                "eps_growth": candidate.eps_growth,
                "promoter_holding": candidate.promoter_holding,
                "source": candidate.source,
                "updated_at": candidate.updated_at.isoformat() if candidate.updated_at else None,
            },
            "moat_flags": self._build_moat_flags(candidate),
            "undervalued_reasons": self._undervalued_reasons(candidate),
            "mini_dcf": self._compute_mini_dcf_from_candidate(candidate),
            "time_to_fair": self._estimate_time_to_fair_from_candidate(candidate),
            "sources": {},
        }

        if "tapetide" in self.sources:
            details["sources"]["tapetide"] = await self._tapetide_details(candidate)
        if "tickertape" in self.sources:
            details["sources"]["tickertape"] = self._tickertape_details(candidate)
        if "moneycontrol" in self.sources:
            details["sources"]["moneycontrol"] = self._moneycontrol_details(candidate)

        return details

    async def _fetch_candidates(self, client: TapetideMcpClient) -> list[dict[str, Any]]:
        params = [
            {"field": "OgInst", "op": "", "val": "ES"},
            {"field": "ROCE", "op": "gte", "val": str(self.thresholds["min_roce"])},
            {"field": "ReturnOnEquity", "op": "gte", "val": str(self.thresholds["min_roe"])},
            {"field": "Debt2Eq", "op": "lte", "val": str(self.thresholds["max_debt_to_eq"])},
            {"field": "Mcap", "op": "gte", "val": str(self.thresholds["min_mcap"])},
        ]

        if "min_promoter_holding" in self.thresholds:
            params.append(
                {"field": "PromoterHolding", "op": "gte", "val": str(self.thresholds["min_promoter_holding"])}
            )

        rows: list[dict[str, Any]] = []
        for page in range(1, self.max_pages + 1):
            payload = {
                "params": params,
                "fields": [
                    "DispSym",
                    "Symbol",
                    "Seosym",
                    "Exch",
                    "Isin",
                    "Sector",
                    "Mcap",
                    "Pe",
                    "Pb",
                    "IndustryPe",
                    "IndustryPb",
                    "EPS",
                    "Ltp",
                    "ROCE",
                    "ReturnOnEquity",
                    "Debt2Eq",
                    "OperatingMargin",
                    "RevenueGrowthPer",
                    "EPSGrowthPer",
                    "PromoterHolding",
                    "Sid",
                ],
                "sort": "Mcap",
                "sort_order": "desc",
                "count": self.page_size,
                "page": page,
            }
            data = await client.call_tool("screen_stocks", payload)
            if "data" not in data:
                raw = data.get("raw") if isinstance(data, dict) else None
                try:
                    parsed = json.loads(raw) if raw else None
                    if isinstance(parsed, dict) and "data" in parsed:
                        data = parsed
                    else:
                        raise RuntimeError(
                            f"Tapetide screener returned unexpected payload: {raw or data}"
                        )
                except json.JSONDecodeError:
                    raise RuntimeError(
                        f"Tapetide screener returned unexpected payload: {raw or data}"
                    )
            if data.get("code") not in (0, None):
                raise RuntimeError(f"Tapetide screener error: {data.get('remarks') or data}")

            page_rows = data.get("data") or []
            if not page_rows:
                break
            rows.extend(page_rows)
            if len(rows) >= self.max_candidates:
                rows = rows[: self.max_candidates]
                break

        return rows

    async def _apply_dhan_ltp(self, rows: list[dict[str, Any]]) -> None:
        isins = {r.get("Isin") for r in rows if r.get("Isin")}
        if not isins:
            return
        instruments = (
            self.db.query(Instrument)
            .filter(Instrument.isin.in_(list(isins)))
            .all()
        )
        inst_map = {i.isin: i for i in instruments if i.isin}

        seg_to_ids: dict[str, list[str]] = {"NSE_EQ": [], "BSE_EQ": []}
        for inst in instruments:
            segment = "BSE_EQ" if inst.exchange == "BSE" else "NSE_EQ"
            seg_to_ids.setdefault(segment, []).append(str(inst.security_id))

        if not any(seg_to_ids.values()):
            return

        ltp_map: dict[str, float] = {}
        try:
            client = DhanClient()
            try:
                for segment, ids in seg_to_ids.items():
                    if not ids:
                        continue
                    for chunk in self._chunk(ids, 1000):
                        payload = await client.get_market_quote_ltp(chunk, segment)
                        ltp_map.update(self._parse_ltp_map(payload))
            finally:
                await client.close()
        except Exception as e:
            logger.warning("Dhan LTP fetch failed: %s", e)
            return

        for row in rows:
            inst = inst_map.get(row.get("Isin"))
            if not inst:
                continue
            ltp = ltp_map.get(str(inst.security_id))
            if ltp is not None:
                row["Ltp"] = ltp
                row["LtpSource"] = "dhan"

    def _store_candidates(self, rows: list[dict[str, Any]]) -> int:
        stored = 0
        for row in rows:
            isin = row.get("Isin")
            symbol = self._resolve_symbol(isin, row)
            scored = self._score_row(row)
            if scored["score"] < self.min_score:
                continue
            exchange = row.get("Exch") or ""

            existing = (
                self.db.query(ValueCandidate)
                .filter(ValueCandidate.symbol == symbol, ValueCandidate.exchange == exchange)
                .first()
            )
            if not existing:
                existing = ValueCandidate(symbol=symbol, exchange=exchange)
                self.db.add(existing)

            existing.security_id = scored["security_id"]
            existing.display_name = row.get("DispSym")
            existing.sector = row.get("Sector")
            existing.isin = isin
            existing.market_cap = scored["market_cap"]
            existing.pe = scored["pe"]
            existing.pb = scored["pb"]
            existing.roce = scored["roce"]
            existing.roe = scored["roe"]
            existing.debt_to_eq = scored["debt_to_eq"]
            existing.operating_margin = scored["operating_margin"]
            existing.revenue_growth = scored["revenue_growth"]
            existing.eps_growth = scored["eps_growth"]
            existing.promoter_holding = scored["promoter_holding"]
            existing.score = scored["score"]
            existing.valuation_score = scored["valuation_score"]
            existing.quality_score = scored["quality_score"]
            existing.growth_score = scored["growth_score"]
            existing.ownership_score = scored["ownership_score"]
            existing.source = "tapetide"
            existing.source_id = str(row.get("Sid") or "")
            existing.source_slug = row.get("Seosym")
            existing.raw_data = json.dumps(row)
            stored += 1

        return stored

    def _resolve_symbol(self, isin: str | None, row: dict[str, Any]) -> str:
        symbol = None
        security_id = None
        if isin:
            instrument = self.db.query(Instrument).filter(Instrument.isin == isin).first()
            if instrument:
                symbol = instrument.trading_symbol or instrument.symbol
                security_id = instrument.security_id
        if not symbol:
            symbol = row.get("Symbol") or row.get("DispSym") or row.get("Seosym") or str(isin or "UNKNOWN")
        row["_security_id"] = security_id
        return symbol

    def _score_row(self, row: dict[str, Any]) -> dict[str, Any]:
        pe = self._num(row.get("Pe"))
        pb = self._num(row.get("Pb"))
        roce = self._num(row.get("ROCE"))
        roe = self._num(row.get("ReturnOnEquity"))
        debt = self._num(row.get("Debt2Eq"))
        promoter = self._num(row.get("PromoterHolding"))
        mcap = self._num(row.get("Mcap"))
        eps = self._num(row.get("EPS"))
        ltp = self._num(row.get("Ltp"))
        industry_pe = self._num(row.get("IndustryPe"))
        if not eps and ltp and pe:
            eps = ltp / pe

        operating_margin = self._num(row.get("OperatingMargin"))
        revenue_growth = self._num(row.get("RevenueGrowthPer"))
        eps_growth = self._num(row.get("EPSGrowthPer"))

        valuation = self._avg(
            [
                self._lower_is_better(pe, self.score_caps["pe_best"], self.thresholds["max_pe"]),
                self._lower_is_better(pb, self.score_caps["pb_best"], self.thresholds["max_pb"]),
            ]
        )
        quality = self._avg(
            [
                self._higher_is_better(roce, self.thresholds["min_roce"], self.score_caps["roce_best"]),
                self._higher_is_better(roe, self.thresholds["min_roe"], self.score_caps["roe_best"]),
                self._lower_is_better(debt, 0, self.score_caps["debt_best"]),
                self._higher_is_better(
                    operating_margin,
                    self.thresholds["min_operating_margin"],
                    self.score_caps["margin_best"],
                ),
            ]
        )
        growth = self._avg(
            [
                self._higher_is_better(revenue_growth, 5, 20),
                self._higher_is_better(eps_growth, 5, 20),
            ]
        )
        ownership = self._avg(
            [
                self._higher_is_better(
                    promoter, self.thresholds.get("min_promoter_holding", 0), self.score_caps["promoter_best"]
                )
            ]
        )

        score = (
            valuation * self.weights["valuation"]
            + quality * self.weights["quality"]
            + growth * self.weights["growth"]
            + ownership * self.weights["ownership"]
        )

        bonus = 0.0
        if roce >= 25 and debt <= self.score_caps["debt_best"]:
            bonus += 0.05
        if revenue_growth >= 15 and eps_growth >= 15:
            bonus += 0.05

        penalty = 0.0
        if pe and pe > self.thresholds["max_pe"]:
            penalty += min(0.2, (pe - self.thresholds["max_pe"]) / self.thresholds["max_pe"] * 0.2)
        if pb and pb > self.thresholds["max_pb"]:
            penalty += min(0.1, (pb - self.thresholds["max_pb"]) / self.thresholds["max_pb"] * 0.1)
        if debt and debt > self.thresholds["max_debt_to_eq"]:
            penalty += 0.05

        score = max(0.0, min(1.0, score + bonus - penalty))

        value_range = self._compute_fair_value(
            eps=eps,
            ltp=ltp,
            industry_pe=industry_pe,
        )
        if value_range.get("undervalued"):
            score = min(1.0, score + 0.05)

        return {
            "security_id": row.get("_security_id"),
            "market_cap": mcap,
            "pe": pe,
            "pb": pb,
            "industry_pe": industry_pe,
            "eps": eps,
            "ltp": ltp,
            "roce": roce,
            "roe": roe,
            "debt_to_eq": debt,
            "operating_margin": operating_margin,
            "revenue_growth": revenue_growth,
            "eps_growth": eps_growth,
            "promoter_holding": promoter,
            "valuation_score": round(valuation, 3),
            "quality_score": round(quality, 3),
            "growth_score": round(growth, 3),
            "ownership_score": round(ownership, 3),
            "score": round(score, 3),
        }

    async def _tapetide_details(self, candidate: ValueCandidate) -> dict[str, Any]:
        client = TapetideMcpClient()
        symbol = candidate.symbol
        try:
            try:
                profile = await client.call_tool("get_company_profile", {"symbol": symbol})
            except Exception:
                profile = None
                if candidate.isin:
                    search = await client.call_tool("search_stocks", {"query": candidate.isin, "limit": 1})
                    hit = (search.get("data") or [None])[0]
                    symbol = (hit or {}).get("nse_symbol") or (hit or {}).get("screener_symbol") or symbol
                    profile = await client.call_tool("get_company_profile", {"symbol": symbol})

            ratios = await client.call_tool("get_financials", {"symbol": symbol, "section": "ratios"})
            profit_loss = await client.call_tool("get_financials", {"symbol": symbol, "section": "profit_loss"})
            shareholding = await client.call_tool("get_shareholding", {"symbol": symbol})

            return {
                "symbol": symbol,
                "profile": (profile or {}).get("data"),
                "ratios": self._latest_financial_snapshot(ratios),
                "profit_loss": self._latest_financial_snapshot(profit_loss),
                "shareholding": shareholding.get("data"),
            }
        except Exception as e:
            logger.warning("Tapetide detail fetch failed for %s: %s", candidate.symbol, e)
            return {"error": str(e)}
        finally:
            await client.close()

    def _tickertape_details(self, candidate: ValueCandidate) -> dict[str, Any]:
        try:
            client = TickertapeClient()
            scorecard = client.get_scorecard(candidate.symbol)
            return {"scorecard": scorecard}
        except Exception as e:
            logger.warning("Tickertape details unavailable for %s: %s", candidate.symbol, e)
            return {"error": str(e)}

    def _moneycontrol_details(self, candidate: ValueCandidate) -> dict[str, Any]:
        try:
            client = MoneycontrolClient()
            overview = client.get_overview(candidate.symbol)
            ratios = client.get_ratios(candidate.symbol)
            return {"overview": overview, "ratios": ratios}
        except Exception as e:
            logger.warning("Moneycontrol details unavailable for %s: %s", candidate.symbol, e)
            return {"error": str(e)}

    def _build_moat_flags(self, candidate: ValueCandidate) -> list[str]:
        flags = []
        if candidate.roce and candidate.roce >= 25:
            flags.append("High ROCE")
        if candidate.roe and candidate.roe >= 20:
            flags.append("High ROE")
        if candidate.debt_to_eq is not None and candidate.debt_to_eq <= 0.3:
            flags.append("Low Debt")
        if candidate.operating_margin and candidate.operating_margin >= 20:
            flags.append("Strong Margins")
        if candidate.revenue_growth and candidate.revenue_growth >= 10 and candidate.eps_growth and candidate.eps_growth >= 10:
            flags.append("Consistent Growth")
        if candidate.promoter_holding and candidate.promoter_holding >= 50:
            flags.append("Promoter Skin in Game")
        if candidate.pe and candidate.pe <= 15:
            flags.append("Value PE")
        if candidate.pb and candidate.pb <= 2.5:
            flags.append("Reasonable PB")
        try:
            fair = self._compute_fair_value_from_candidate(candidate)
            if fair.get("undervalued"):
                flags.append("Undervalued vs Fair")
        except Exception:
            pass
        return flags

    def _undervalued_reasons(self, candidate: ValueCandidate) -> list[str]:
        raw = self._parse_raw_data(candidate.raw_data)
        fair = self._compute_fair_value_from_candidate(candidate)
        reasons = []

        ltp = raw.get("Ltp")
        if fair.get("undervalued") and ltp:
            reasons.append(
                f"Trading below fair value low (LTP ₹{ltp:.2f} vs fair low ₹{fair['fair_value_low']})"
            )

        if candidate.pe and candidate.pe <= self.score_caps["pe_best"]:
            reasons.append(f"Low PE ({candidate.pe:.1f}) vs target {self.score_caps['pe_best']}")
        if candidate.pb and candidate.pb <= self.score_caps["pb_best"]:
            reasons.append(f"Low PB ({candidate.pb:.1f}) vs target {self.score_caps['pb_best']}")
        if candidate.roce and candidate.roce >= self.score_caps["roce_best"]:
            reasons.append(f"High ROCE ({candidate.roce:.1f}%)")
        if candidate.roe and candidate.roe >= self.score_caps["roe_best"]:
            reasons.append(f"High ROE ({candidate.roe:.1f}%)")
        if candidate.debt_to_eq is not None and candidate.debt_to_eq <= self.score_caps["debt_best"]:
            reasons.append(f"Low debt/equity ({candidate.debt_to_eq:.2f})")
        if candidate.operating_margin and candidate.operating_margin >= self.score_caps["margin_best"]:
            reasons.append(f"Strong operating margin ({candidate.operating_margin:.1f}%)")
        if candidate.revenue_growth and candidate.revenue_growth >= 15:
            reasons.append(f"Healthy revenue growth ({candidate.revenue_growth:.1f}%)")
        if candidate.eps_growth and candidate.eps_growth >= 15:
            reasons.append(f"Healthy EPS growth ({candidate.eps_growth:.1f}%)")
        if candidate.promoter_holding and candidate.promoter_holding >= self.score_caps["promoter_best"]:
            reasons.append(f"High promoter holding ({candidate.promoter_holding:.1f}%)")

        if not reasons:
            reasons.append("Meets core value filters (ROCE/ROE, low debt, promoter holding)")
        return reasons

    def _latest_financial_snapshot(self, data: dict[str, Any]) -> dict[str, Any] | None:
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            return None
        entry = rows[0]
        periods = entry.get("periods") or []
        latest_period = periods[-1] if periods else None
        out = {"latest_period": latest_period, "metrics": {}}
        for key, values in (entry.get("data") or {}).items():
            if isinstance(values, dict) and latest_period in values:
                out["metrics"][key] = values.get(latest_period)
        return out

    def _compute_fair_value_from_candidate(self, candidate: ValueCandidate) -> dict[str, Any]:
        raw = self._parse_raw_data(candidate.raw_data)
        eps = self._num(raw.get("EPS"))
        ltp = self._num(raw.get("Ltp"))
        industry_pe = self._num(raw.get("IndustryPe"))
        pe = self._num(raw.get("Pe"))
        if not eps and ltp and pe:
            eps = ltp / pe
        return self._compute_fair_value(eps=eps, ltp=ltp, industry_pe=industry_pe)

    def _compute_mini_dcf_from_candidate(self, candidate: ValueCandidate) -> dict[str, Any]:
        raw = self._parse_raw_data(candidate.raw_data)
        eps = self._num(raw.get("EPS"))
        ltp = self._num(raw.get("Ltp"))
        pe = self._num(raw.get("Pe"))
        if not eps and ltp and pe:
            eps = ltp / pe

        growth = self._num(raw.get("EPSGrowthPer"))
        if not growth:
            growth = self._num(raw.get("RevenueGrowthPer"))

        return self._compute_mini_dcf(eps=eps, ltp=ltp, growth_pct=growth)

    def _estimate_time_to_fair_from_candidate(self, candidate: ValueCandidate) -> dict[str, Any]:
        raw = self._parse_raw_data(candidate.raw_data)
        ltp = self._num(raw.get("Ltp"))
        pe = self._num(raw.get("Pe"))
        eps = self._num(raw.get("EPS"))
        if not eps and ltp and pe:
            eps = ltp / pe

        fair = self._compute_fair_value(eps=eps, ltp=ltp, industry_pe=self._num(raw.get("IndustryPe")))
        if not fair or not fair.get("fair_value_low") or not ltp:
            return {}

        fair_mid = (fair["fair_value_low"] + fair["fair_value_high"]) / 2

        eps_growth = self._num(raw.get("EPSGrowthPer")) or self._num(raw.get("RevenueGrowthPer"))
        dcf = self._compute_mini_dcf(eps=eps, ltp=ltp, growth_pct=eps_growth)
        g_low = dcf.get("growth_low")
        g_high = dcf.get("growth_high")

        fixed_rate = float(self.time_cfg.get("fixed_growth_rate", 0.12))
        min_g = float(self.time_cfg.get("min_growth", 0.04))
        max_g = float(self.time_cfg.get("max_growth", 0.25))
        max_years = float(self.time_cfg.get("max_years", 10))

        growth_candidates = []
        if eps_growth:
            growth_candidates.append(eps_growth / 100)
        if fixed_rate:
            growth_candidates.append(fixed_rate)
        if g_low and g_high:
            growth_candidates.append(((g_low + g_high) / 2) / 100)

        estimates = {}
        labels = []
        if eps_growth:
            labels.append("eps_growth")
        if fixed_rate:
            labels.append("fixed")
        if g_low and g_high:
            labels.append("dcf_mid")

        for label, g in zip(labels, growth_candidates):
            g = max(min_g, min(max_g, g))
            years = self._years_to_target(ltp, fair_mid, g, max_years)
            estimates[label] = round(years, 2) if years is not None else None

        valid = [v for v in estimates.values() if v is not None]
        if not valid:
            return {}
        valid.sort()
        best = valid[len(valid) // 2]

        return {
            "fair_mid": round(fair_mid, 2),
            "best_years": best,
            "estimates": estimates,
            "growth_rates": {
                "eps_growth_pct": round(eps_growth, 2) if eps_growth else None,
                "fixed_rate_pct": round(fixed_rate * 100, 2),
                "dcf_growth_mid_pct": round((g_low + g_high) / 2, 2) if g_low and g_high else None,
            },
        }

    def _compute_mini_dcf(self, eps: float, ltp: float, growth_pct: float) -> dict[str, Any]:
        if not eps:
            return {}
        years = int(self.dcf_cfg.get("years", 5))
        discount_rate = float(self.dcf_cfg.get("discount_rate", 0.12))
        terminal_multiple = float(self.dcf_cfg.get("terminal_multiple", 12))
        growth_floor = float(self.dcf_cfg.get("growth_floor", 0.04))
        growth_cap = float(self.dcf_cfg.get("growth_cap", 0.20))
        buffer = float(self.dcf_cfg.get("growth_buffer", 0.05))

        base_growth = growth_pct / 100 if growth_pct else 0.10
        base_growth = min(growth_cap, max(growth_floor, base_growth))
        g_low = max(growth_floor, base_growth - buffer)
        g_high = min(growth_cap, base_growth + buffer)

        def pv_value(growth: float) -> float:
            total = 0.0
            for t in range(1, years + 1):
                eps_t = eps * ((1 + growth) ** t)
                total += eps_t / ((1 + discount_rate) ** t)
            terminal_eps = eps * ((1 + growth) ** years)
            terminal_value = terminal_eps * terminal_multiple
            total += terminal_value / ((1 + discount_rate) ** years)
            return total

        low_val = pv_value(g_low)
        high_val = pv_value(g_high)

        upside = None
        if ltp:
            upside = ((high_val - ltp) / ltp) * 100

        return {
            "dcf_low": round(low_val, 2),
            "dcf_high": round(high_val, 2),
            "growth_low": round(g_low * 100, 2),
            "growth_high": round(g_high * 100, 2),
            "upside_pct": round(upside, 2) if upside is not None else None,
            "undervalued": bool(ltp and ltp < low_val),
        }

    @staticmethod
    def _years_to_target(ltp: float, target: float, growth: float, max_years: float) -> float | None:
        if not ltp or not target:
            return None
        if target <= ltp:
            return 0.0
        if growth <= 0:
            return None
        # log(target/ltp) / log(1+g)
        import math

        years = math.log(target / ltp) / math.log(1 + growth)
        if years < 0:
            return 0.0
        return min(years, max_years)

    def _compute_fair_value(self, eps: float, ltp: float, industry_pe: float) -> dict[str, Any]:
        if not eps:
            return {}
        base_low = float(self.valuation_range.get("pe_low", 12))
        base_high = float(self.valuation_range.get("pe_high", 20))
        ind_low = float(self.valuation_range.get("industry_low_factor", 0.8))
        ind_high = float(self.valuation_range.get("industry_high_factor", 1.0))

        pe_low = base_low
        pe_high = base_high
        if industry_pe:
            pe_low = max(base_low, min(base_high, industry_pe * ind_low))
            pe_high = max(pe_low, min(base_high, industry_pe * ind_high))

        fair_low = eps * pe_low
        fair_high = eps * pe_high

        undervalued = False
        upside_pct = None
        if ltp:
            undervalued = ltp < fair_low
            upside_pct = ((fair_high - ltp) / ltp) * 100 if ltp else None

        return {
            "fair_value_low": round(fair_low, 2),
            "fair_value_high": round(fair_high, 2),
            "pe_low": round(pe_low, 2),
            "pe_high": round(pe_high, 2),
            "undervalued": undervalued,
            "upside_pct": round(upside_pct, 2) if upside_pct is not None else None,
        }

    @staticmethod
    def _parse_raw_data(raw_data: str | None) -> dict[str, Any]:
        if not raw_data:
            return {}
        try:
            return json.loads(raw_data)
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _parse_ltp_map(payload: dict | None) -> dict[str, float]:
        if not payload:
            return {}
        data_obj = payload.get("data", payload)
        out: dict[str, float] = {}
        if not isinstance(data_obj, dict):
            return out
        for _seg_key, segment_data in data_obj.items():
            if not isinstance(segment_data, dict):
                continue
            for sec_id, price_data in segment_data.items():
                if not isinstance(price_data, dict):
                    continue
                raw = price_data.get("last_price", price_data.get("ltp", price_data.get("lastTradedPrice")))
                if raw is not None:
                    try:
                        out[str(sec_id)] = float(raw)
                    except (TypeError, ValueError):
                        continue
        return out

    @staticmethod
    def _chunk(items: list[str], size: int) -> list[list[str]]:
        return [items[i : i + size] for i in range(0, len(items), size)]

    @staticmethod
    def _num(val: Any) -> float:
        try:
            if val is None:
                return 0.0
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _avg(values: list[float]) -> float:
        vals = [v for v in values if v is not None]
        if not vals:
            return 0.0
        return sum(vals) / len(vals)

    @staticmethod
    def _higher_is_better(value: float, min_val: float, best_val: float) -> float:
        if value <= min_val:
            return 0.0
        if best_val <= min_val:
            return 1.0
        return min(1.0, (value - min_val) / (best_val - min_val))

    @staticmethod
    def _lower_is_better(value: float, best_val: float, max_val: float) -> float:
        if value <= best_val:
            return 1.0
        if max_val <= best_val:
            return 0.0
        return max(0.0, 1 - (value - best_val) / (max_val - best_val))
