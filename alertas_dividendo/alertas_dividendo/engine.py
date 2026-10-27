"""Uma passada idempotente: varre a CVM, decide sinais e dispara o que está no prazo."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from alertas_dividendo.calendar import TZ, Calendar
from alertas_dividendo.config import Config
from alertas_dividendo.cvm_parse import is_aviso_ou_ata, relevant
from alertas_dividendo.decide import Prior, Snapshot, decide
from alertas_dividendo.extract import Distribution, extract_distributions, select_for_ticker
from alertas_dividendo.messages import (
    buy_message,
    captcha_message,
    conferir_message,
    exit_message,
    result_message,
    update_dates_message,
    with_disclaimer,
)
from alertas_dividendo.pdftext import pdf_variants
from alertas_dividendo.quotes import ensure_cotahist, ensure_index, yahoo_close
from alertas_dividendo.sources import (
    CaptchaRequired,
    download_pdf,
    enet_paused,
    fetch_b3_dividends,
    fetch_enet,
    fetch_ipe,
    fetch_issuers,
)
from alertas_dividendo.store import Store
from alertas_dividendo.strategy import (
    aware,
    buy_plan,
    cumulative_result,
    dividend_received,
    index_return,
    jcp_net,
    last_close_session,
    passes_liquidity,
    trade_return,
)
from alertas_dividendo.telegram_api import send_message

log = logging.getLogger(__name__)


@dataclass
class Report:
    now: datetime
    dry_run: bool
    messages: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def ignore(self, line: str) -> None:
        self.ignored.append(line)
        log.info("ignorado %s", line)


def last_final_session(now: datetime, calendar: Calendar) -> date:
    moment = aware(now)
    day = moment.date()
    if calendar.is_session(day) and moment >= calendar.close_at(day):
        return day
    return calendar.previous_session(day)


def _parse_dt(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    return aware(moment)


def _session_or_keep(day: date, calendar: Calendar) -> date:
    if calendar.is_session(day):
        return day
    return calendar.on_or_before_session(day)


def _ex_date(stated: date | None, data_com: date | None, calendar: Calendar) -> date | None:
    """Na B3 a data-ex é o pregão seguinte à data-com. Data meses à frente é pagamento."""
    if stated and data_com and (stated <= data_com or stated > data_com + timedelta(days=10)):
        stated = None
    if stated and calendar.is_session(stated):
        return stated
    if stated:
        return calendar.next_session(stated - timedelta(days=1))
    if data_com:
        return calendar.next_session(data_com)
    return None


def _snapshot(store: Store, ticker: str, price_day: date) -> Snapshot:
    rows = store.history(ticker, price_day.isoformat())
    if not rows:
        return Snapshot(None, None, None, None, 0)
    from alertas_dividendo.strategy import average_volume

    volumes = [float(row["volume"]) for row in rows]
    average, count = average_volume(volumes)
    last = rows[-1]
    return Snapshot(
        last_close=float(last["close"]),
        last_close_date=date.fromisoformat(last["session_date"]),
        last_close_source=last["source"],
        avg_volume=average,
        volume_sessions=count,
    )


def _price(store: Store, ticker: str, session: date, now: datetime, calendar: Calendar) -> tuple[float, str] | None:
    if aware(now) < calendar.close_at(session):
        return None
    row = store.close_on(ticker, session.isoformat())
    if row is not None:
        return float(row["close"]), row["source"]
    yahoo = yahoo_close(ticker, session)
    if yahoo is None:
        return None
    store.insert_quotes_if_absent(
        [(ticker, session.isoformat(), yahoo, 0.0, "Yahoo Finance")]
    )
    return yahoo, "Yahoo Finance (COTAHIST ainda não publicado)"


def _index_level(store: Store, session: date) -> tuple[float, str] | None:
    row = store.index_on(session.isoformat())
    if row is None:
        return None
    return float(row["close"]), row["source"]


def _save_pdf(cfg: Config, protocol: str, payload: bytes) -> None:
    cfg.pdf_dir.mkdir(parents=True, exist_ok=True)
    (cfg.pdf_dir / f"{protocol}.pdf").write_bytes(payload)


def _document_text_cfg(cfg: Config, store: Store, doc, published: date) -> str:
    cached = store.get_extraction(doc["protocol"])
    if cached and cached["payload_json"]:
        return cached["payload_json"]
    try:
        payload = download_pdf(doc["url"])
    except Exception as exc:
        log.warning("PDF %s falhou: %s", doc["protocol"], exc)
        store.save_extraction(doc["protocol"], False, "", str(exc))
        return ""
    try:
        _save_pdf(cfg, doc["protocol"], payload)
    except OSError:
        log.warning("não gravei o PDF %s", doc["protocol"], exc_info=True)
    variants = pdf_variants(payload)
    best = ""
    best_score = -1
    for variant in variants:
        found = extract_distributions(variant, published=published)
        score = (2 if found else 0) + (1 if any(item.data_com for item in found) else 0)
        if score > best_score:
            best = variant
            best_score = score
    store.save_extraction(doc["protocol"], best_score > 0, best, "")
    return best


def _cross_check(issuer: str, data_com: date, gross: float, kind: str) -> str | None:
    try:
        rows = fetch_b3_dividends(issuer)
    except Exception as exc:
        log.info("conferência B3 de %s falhou: %s", issuer, exc)
        return None
    target = data_com.strftime("%d/%m/%Y")
    for item in rows:
        if (item.get("lastDatePrior") or "") != target:
            continue
        rate_raw = str(item.get("rate") or "").strip()
        try:
            rate = float(rate_raw.replace(".", "").replace(",", ".")) if "," in rate_raw else float(rate_raw)
        except ValueError:
            continue
        label = (item.get("label") or "").upper()
        notes = []
        if gross and abs(rate - gross) / gross > 0.02:
            notes.append(f"B3 lista R$ {rate_raw} por ação e o PDF R$ {gross:.6f}")
        if "JRS" in label and kind == "dividendo":
            notes.append(f"B3 classifica o evento como JCP ({item.get('label')})")
        if "DIVID" in label and kind == "jcp":
            notes.append(f"B3 classifica o evento como dividendo ({item.get('label')})")
        return "; ".join(notes) if notes else None
    return None


def _queue(cfg: Config, store: Store, report: Report, key: str, kind: str, signal_id: int | None, body: str) -> None:
    if cfg.dry_run:
        report.messages.append(body)
        return
    if store.alert_exists(key):
        return
    if not store.reserve_alert(key, kind, signal_id, body):
        return
    try:
        send_message(cfg.token, cfg.chat_id, body)
    except Exception as exc:
        name = type(exc).__name__
        if name in {"ConnectionError", "ConnectTimeout"} or "Connection" in name:
            store.conn.execute("DELETE FROM alerts WHERE dedupe_key=? AND status='reserved'", (key,))
            store.commit()
            report.errors.append(f"Telegram sem conexão ({kind}); tenta de novo na próxima hora")
            log.warning("telegram conexão %s", exc)
            return
        status = "uncertain" if "Timeout" in name else "error"
        store.mark_alert(key, status)
        report.errors.append(f"Telegram não confirmou {kind}: {name}")
        log.warning("telegram %s", exc)
        return
    store.mark_alert(key, "sent")
    report.messages.append(body)


def _warn_captcha(cfg: Config, store: Store, report: Report, day: date) -> None:
    """Um aviso por dia. A pausa do ENET vale mesmo se o Telegram falhar."""
    store.meta_set("enet_captcha_day", day.isoformat())
    store.log_event("captcha", detail="CVM pediu captcha. Consulta ENET interrompida até o dia seguinte.")
    store.commit()
    key = f"captcha|{day.isoformat()}"
    if store.alert_exists(key):
        report.errors.append("CVM pediu captcha. O aviso de hoje já foi enviado.")
        return
    body = with_disclaimer(captcha_message(), cfg.simulacao)
    report.errors.append("CVM pediu captcha. Parei o ENET e não vou repetir hoje.")
    _queue(cfg, store, report, key, "captcha", None, body)


def _ingest(cfg: Config, store: Store, report: Report) -> None:
    start = cfg.now.date() - timedelta(days=cfg.lookback_days)
    end = cfg.now.date()
    filings: list = []
    if enet_paused(store.meta_get("enet_captcha_day"), end):
        report.errors.append("ENET em pausa até amanhã: captcha já pedido hoje. Sem nova consulta.")
        log.warning("ENET em pausa no dia %s por captcha", end)
        key = f"captcha|{end.isoformat()}"
        if not cfg.dry_run and not store.alert_exists(key):
            _queue(
                cfg,
                store,
                report,
                key,
                "captcha",
                None,
                with_disclaimer(captcha_message(), cfg.simulacao),
            )
    else:
        try:
            filings = fetch_enet(start, end)
            log.info("ENET: %s documentos entre %s e %s", len(filings), start, end)
        except CaptchaRequired as exc:
            filings = list(exc.partial)
            log.warning("ENET pediu captcha depois de %s documentos já lidos", len(filings))
            _warn_captcha(cfg, store, report, end)
        except Exception as exc:
            filings = []
            report.errors.append(f"consulta ENET falhou ({type(exc).__name__}); sigo com o IPE se houver")
            log.warning("ENET falhou", exc_info=True)
    years = {start.year, end.year}
    for year in sorted(years):
        try:
            ipe = fetch_ipe(year)
            kept = [item for item in ipe if start <= item.published_at.date() <= end]
            filings.extend(kept)
            log.info("IPE %s: %s documentos de provento na janela", year, len(kept))
        except Exception as exc:
            report.errors.append(f"IPE {year} falhou ({type(exc).__name__})")
            log.warning("IPE %s falhou", year, exc_info=True)
    seen: dict[str, object] = {}
    for filing in filings:
        if not filing.protocol:
            continue
        current = seen.get(filing.protocol)
        if current is None or filing.version >= current.version:  # type: ignore[attr-defined]
            if current is not None and current.time_known and not filing.time_known:  # type: ignore[attr-defined]
                continue
            seen[filing.protocol] = filing
    now_iso = cfg.now.isoformat(timespec="seconds")
    for filing in seen.values():
        if not filing.active or not relevant(filing):
            continue
        store.upsert_document(
            {
                "protocol": filing.protocol,
                "version": filing.version,
                "cvm_code": filing.cvm_code,
                "company_name": filing.company_name,
                "category": filing.category,
                "subject": filing.subject,
                "species": filing.species,
                "published_at": filing.published_at.isoformat(timespec="minutes"),
                "time_known": 1 if filing.time_known else 0,
                "reference_date": filing.reference_date.isoformat(),
                "url": filing.url,
                "source": filing.source,
                "active": 1 if filing.active else 0,
                "first_seen": now_iso,
            }
        )


def _ensure_issuers(store: Store, report: Report) -> dict[str, str]:
    if not store.issuer_loaded():
        try:
            rows = fetch_issuers()
            store.replace_issuers(rows)
            log.info("cadastro B3: %s emissores", len(rows))
        except Exception as exc:
            report.errors.append(f"cadastro de emissores da B3 falhou ({type(exc).__name__})")
            log.warning("emissores B3", exc_info=True)
    return store.issuer_map()


def _priors(store: Store, ticker: str) -> list[Prior]:
    priors: list[Prior] = []
    for row in store.signals_for_ticker(ticker):
        if row["gross_per_share"] is None:
            continue
        if row["status"] not in {"aguardando_compra", "aberta", "encerrada", "ignorado"}:
            continue
        priors.append(
            Prior(
                gross=float(row["gross_per_share"]),
                data_com=date.fromisoformat(row["data_com"]) if row["data_com"] else None,
                kind=row["kind"],
            )
        )
    return priors


def _scan(cfg: Config, store: Store, calendar: Calendar, report: Report) -> None:
    issuers = _ensure_issuers(store, report)
    start = (cfg.now.date() - timedelta(days=cfg.lookback_days)).isoformat()
    docs = [row for row in store.documents_since(start) if relevant_row(row)]
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for doc in docs:
        groups[(doc["cvm_code"], doc["reference_date"] or doc["published_at"][:10])].append(doc)

    open_slots = store.open_position_count()
    for (cvm_code, reference), group in groups.items():
        issuer = issuers.get(cvm_code, "")
        company = group[0]["company_name"]
        if not issuer:
            line = f"{company} protocolo {group[0]['protocol']} sem_emissor código CVM {cvm_code}"
            report.ignore(line)
            store.log_event("sem_emissor", protocol=group[0]["protocol"], detail=line)
            continue
        tickers = store.tickers_of(issuer)
        if not tickers:
            line = f"{issuer} protocolo {group[0]['protocol']} sem_acao_vista"
            report.ignore(line)
            store.log_event("sem_acao_vista", protocol=group[0]["protocol"], detail=line)
            continue
        earliest = min(group, key=lambda doc: doc["published_at"])
        time_known = bool(earliest["time_known"])
        published = _parse_dt(earliest["published_at"])
        plan = buy_plan(published, time_known, calendar)
        price_day = last_close_session(published.date(), plan.timing, calendar)
        liquid = []
        for ticker in tickers:
            snap = _snapshot(store, ticker, price_day)
            gate = passes_liquidity(snap.avg_volume, snap.volume_sessions)
            if gate != "ok":
                volume = f"{snap.avg_volume:.0f}" if snap.avg_volume is not None else "sem média"
                line = (
                    f"{ticker} protocolo {earliest['protocol']} {gate} "
                    f"volume={volume} pregões={snap.volume_sessions}"
                )
                report.ignore(line)
                store.log_event(gate, ticker=ticker, protocol=earliest["protocol"], detail=line)
                continue
            liquid.append((ticker, snap))
        if not liquid:
            continue
        texts = {doc["protocol"]: _document_text_cfg(cfg, store, doc, published.date()) for doc in group}
        for ticker, snap in liquid:
            pairs: list[tuple] = []
            for doc in group:
                text = texts.get(doc["protocol"]) or ""
                found = extract_distributions(text, ticker=ticker, published=published.date())
                chosen = select_for_ticker(found, ticker)
                if chosen:
                    pairs.append((doc, chosen))
            opened = _emit_ticker(
                cfg,
                store,
                calendar,
                report,
                ticker,
                issuer,
                company,
                group,
                pairs,
                snap,
                published,
                time_known,
                open_slots,
            )
            if opened:
                open_slots += 1


def relevant_row(doc) -> bool:
    from alertas_dividendo.cvm_parse import Filing

    filing = Filing(
        protocol=doc["protocol"],
        version=doc["version"],
        cvm_code=doc["cvm_code"],
        company_name=doc["company_name"],
        category=doc["category"],
        subject=doc["subject"] or "",
        species=doc["species"] or "",
        published_at=_parse_dt(doc["published_at"]),
        time_known=bool(doc["time_known"]),
        reference_day=_parse_dt(doc["reference_date"] + "T00:00:00") if doc["reference_date"] else _parse_dt(doc["published_at"]),
        url=doc["url"],
        source=doc["source"],
        active=bool(doc["active"]),
    )
    return relevant(filing)


def _signal_key(ticker: str, kind: str, gross: float | None, data_com: date | None, approval: date | None, protocol: str) -> str:
    anchor = data_com or approval
    return f"{ticker}|{kind}|{(gross or 0):.4f}|{anchor.isoformat() if anchor else ''}|{protocol}"


def realized_returns(store: Store) -> list[float]:
    """Retornos de posições que abriram e fecharam. Sinal ignorado não tem posição."""
    out: list[float] = []
    for row in store.positions_closed():
        if row["entry_price"] is None or row["trade_return"] is None:
            continue
        out.append(float(row["trade_return"]))
    return out


def _emit_ticker(cfg, store, calendar, report, ticker, issuer, company, group, pairs, snap, published, time_known, open_slots) -> bool:
    earliest = min(group, key=lambda doc: doc["published_at"])
    approval = date.fromisoformat(earliest["reference_date"]) if earliest["reference_date"] else published.date()
    if not pairs:
        aviso = next((doc for doc in group if is_aviso_ou_ata(doc["category"])), None)
        if aviso is None:
            line = f"{ticker} protocolo {earliest['protocol']} extracao_falhou (sem alerta: não é aviso/ata)"
            report.ignore(line)
            store.log_event("extracao_falhou", ticker=ticker, protocol=earliest["protocol"], detail=line)
            return False
        decision_doc = aviso
        gross = None
        kind = "indefinido"
        data_com = None
        data_ex = None
        net = None
    else:
        with_com = [pair for pair in pairs if pair[1].data_com]
        pool = with_com or pairs
        avisos = [pair for pair in pool if pair[0]["category"] == "Aviso aos Acionistas"]
        decision_doc, dist = (avisos or pool)[0]
        data_com = dist.data_com or next((pair[1].data_com for pair in pairs if pair[1].data_com), None)
        data_ex = dist.data_ex or next((pair[1].data_ex for pair in pairs if pair[1].data_ex), None)
        gross = dist.gross
        net = dist.net
        kind = dist.kind
    if data_com is not None:
        data_com = _session_or_keep(data_com, calendar)
    data_ex = _ex_date(data_ex, data_com, calendar)
    if kind == "jcp" and gross is not None and net is None:
        net = jcp_net(gross)
    priors = _priors(store, ticker)
    decision = decide(
        ticker=ticker,
        kind=kind,
        gross=gross,
        data_com=data_com,
        approval=approval,
        published_at=published,
        time_known=time_known,
        category_is_aviso_ou_ata=is_aviso_ou_ata(decision_doc["category"]),
        snapshot=snap,
        open_positions=open_slots,
        priors=priors,
        calendar=calendar,
    )
    if decision.action == "ignorar":
        yld = f" yield={decision.yld:.4f}" if decision.yld is not None else ""
        line = f"{ticker} protocolo {decision_doc['protocol']} {decision.reason}{yld}"
        report.ignore(line)
        store.log_event(decision.reason, ticker=ticker, protocol=decision_doc["protocol"], detail=line)
        return False
    if decision.action == "atualizar":
        _apply_update(cfg, store, report, ticker, data_com, data_ex, decision, decision_doc)
        return False
    if decision.action == "conferir_valor":
        signal_key = _signal_key(ticker, kind, None, data_com, approval, decision_doc["protocol"])
        if store.signal_by_dedupe(signal_key) is not None or store.conferir_exists(decision_doc["protocol"], ticker):
            return False
        if _missed_deadline(
            cfg, store, report, ticker, company, issuer, kind, None, None, decision, snap,
            approval, data_com, data_ex, published, time_known, decision_doc,
        ):
            return False
        body = conferir_message(
            ticker=ticker,
            company=company,
            published_at=published,
            url=decision_doc["url"],
            protocol=decision_doc["protocol"],
            category=decision_doc["category"],
            subject=decision_doc["subject"] or "",
            simulacao=cfg.simulacao,
        )
        key = f"conferir|{decision_doc['cvm_code']}|{earliest['reference_date']}|{ticker}"
        store.log_event("extracao_falhou", ticker=ticker, protocol=decision_doc["protocol"], detail="conferir valor")
        _queue(cfg, store, report, key, "conferir", None, body)
        if not cfg.dry_run:
            _insert_signal(
                store, cfg, ticker, company, issuer, kind, None, None, decision, snap,
                approval, data_com, data_ex, published, time_known, decision_doc, "conferir", "extracao_falhou",
            )
        return False
    plan = decision.plan
    assert plan is not None
    if _missed_deadline(
        cfg, store, report, ticker, company, issuer, kind, gross, net, decision, snap,
        approval, data_com, data_ex, published, time_known, decision_doc,
    ):
        return False
    note = ""
    if data_com is not None and data_com <= cfg.now.date():
        mismatch = _cross_check(issuer, data_com, gross or 0, kind)
        if mismatch:
            note = mismatch
            store.log_event("divergencia_b3", ticker=ticker, protocol=decision_doc["protocol"], detail=mismatch)
    body = buy_message(
        ticker=ticker,
        company=company,
        kind=kind,
        gross=gross,
        net=net,
        yld=decision.yld or 0,
        last_close=snap.last_close or 0,
        last_close_date=snap.last_close_date or plan.buy_date,
        last_close_source=snap.last_close_source or "",
        approval=approval,
        published_at=published,
        time_known=time_known,
        timing=plan.timing,
        data_com=data_com,
        data_ex=data_ex,
        buy_date=plan.buy_date,
        exit_date=decision.exit_date,
        url=decision_doc["url"],
        protocol=decision_doc["protocol"],
        slot_number=open_slots + 1,
        late=False,
        entry_price=None,
        entry_source=None,
        simulacao=cfg.simulacao,
    )
    if note:
        body += f"\nConferência com a B3: {note}"
    if cfg.dry_run:
        report.messages.append(body)
        return True
    signal_id = _insert_signal(
        store, cfg, ticker, company, issuer, kind, gross, net, decision, snap, approval, data_com, data_ex, published, time_known, decision_doc, "aguardando_compra", "",
    )
    store.update_signal(signal_id, status="aguardando_compra", late=0)
    store.insert_position(
        {
            "signal_id": signal_id,
            "ticker": ticker,
            "status": "aguardando_compra",
            "entry_date": plan.buy_date.isoformat(),
            "entry_price": None,
            "entry_source": None,
            "exit_date": decision.exit_date.isoformat() if decision.exit_date else None,
            "exit_price": None,
            "exit_source": None,
            "data_com": data_com.isoformat() if data_com else None,
            "kind": kind,
            "gross_per_share": gross,
            "net_per_share": net,
            "dividend_received": None,
            "trade_return": None,
            "ibov_entry": None,
            "ibov_exit": None,
            "ibov_return": None,
            "ibov_source": None,
            "opened_at": cfg.now.isoformat(timespec="seconds"),
            "closed_at": None,
        }
    )
    key = f"compra|{ticker}|{kind}|{gross:.4f}|{approval.isoformat()}"
    _queue(cfg, store, report, key, "compra", signal_id, body)
    return True


def _missed_deadline(cfg, store, report, ticker, company, issuer, kind, gross, net, decision, snap, approval, data_com, data_ex, published, time_known, doc) -> bool:
    """Prazo de compra vencido: grava `ignorado` e não avisa. Vale para compra e para conferir valor."""
    plan = decision.plan
    if plan is None or aware(cfg.now) <= plan.deadline:
        return False
    reason = "prazo de compra já passou"
    signal_key = _signal_key(ticker, kind, gross, data_com, approval, doc["protocol"])
    if store.signal_by_dedupe(signal_key) is None:
        line = f"{ticker} protocolo {doc['protocol']} {reason}"
        report.ignore(line)
        store.log_event("prazo_passado", ticker=ticker, protocol=doc["protocol"], detail=line)
        if not cfg.dry_run:
            _insert_signal(
                store, cfg, ticker, company, issuer, kind, gross, net, decision, snap,
                approval, data_com, data_ex, published, time_known, doc, "ignorado", reason,
            )
    return True


def _insert_signal(store, cfg, ticker, company, issuer, kind, gross, net, decision, snap, approval, data_com, data_ex, published, time_known, doc, status, reason) -> int:
    plan = decision.plan
    return store.insert_signal(
        {
            "dedupe_key": _signal_key(ticker, kind, gross, data_com, approval, doc["protocol"]),
            "protocol": doc["protocol"],
            "ticker": ticker,
            "company_name": company,
            "issuer": issuer,
            "kind": kind,
            "gross_per_share": gross,
            "net_per_share": net,
            "yield": decision.yld,
            "last_close": snap.last_close,
            "last_close_date": snap.last_close_date.isoformat() if snap.last_close_date else None,
            "last_close_source": snap.last_close_source,
            "avg_volume": snap.avg_volume,
            "approval_date": approval.isoformat() if approval else None,
            "data_com": data_com.isoformat() if data_com else None,
            "data_ex": data_ex.isoformat() if data_ex else None,
            "published_at": published.isoformat(timespec="minutes"),
            "time_known": 1 if time_known else 0,
            "timing": plan.timing if plan else "",
            "buy_date": plan.buy_date.isoformat() if plan else None,
            "buy_deadline": plan.deadline.isoformat(timespec="minutes") if plan else None,
            "exit_date": decision.exit_date.isoformat() if decision.exit_date else None,
            "status": status,
            "ignore_reason": reason,
            "document_url": doc["url"],
            "category": doc["category"],
            "subject": doc["subject"],
            "late": 1 if plan and aware(cfg.now) > plan.deadline else 0,
            "created_at": cfg.now.isoformat(timespec="seconds"),
        }
    )


def _apply_update(cfg, store, report, ticker, data_com, data_ex, decision, doc) -> None:
    if data_com is None or decision.exit_date is None:
        return
    rows = store.conn.execute(
        """
        SELECT * FROM signals
        WHERE ticker=? AND status IN ('aguardando_compra','aberta') AND data_com IS NULL
        ORDER BY id DESC LIMIT 1
        """,
        (ticker,),
    ).fetchall()
    body = update_dates_message(
        ticker=ticker,
        data_com=data_com,
        data_ex=data_ex,
        exit_date=decision.exit_date,
        url=doc["url"],
        simulacao=cfg.simulacao,
    )
    if cfg.dry_run or not rows:
        report.messages.append(body)
        return
    signal = rows[0]
    store.update_signal(
        signal["id"],
        data_com=data_com.isoformat(),
        data_ex=data_ex.isoformat() if data_ex else None,
        exit_date=decision.exit_date.isoformat(),
    )
    store.conn.execute(
        "UPDATE positions SET data_com=?, exit_date=? WHERE signal_id=?",
        (data_com.isoformat(), decision.exit_date.isoformat(), signal["id"]),
    )
    _queue(cfg, store, report, f"data_com|{signal['id']}|{data_com.isoformat()}", "data_com", signal["id"], body)


def _advance(cfg: Config, store: Store, calendar: Calendar, report: Report, *, reminders_only: bool) -> None:
    now = aware(cfg.now)
    for position in store.positions_active():
        status = position["status"]
        entry_date = date.fromisoformat(position["entry_date"]) if position["entry_date"] else None
        if status == "aguardando_compra" and entry_date is not None:
            filled = _price(store, position["ticker"], entry_date, now, calendar)
            if filled and not cfg.dry_run:
                ibov = _index_level(store, entry_date)
                store.update_position(
                    position["id"],
                    status="aberta",
                    entry_price=filled[0],
                    entry_source=filled[1],
                    ibov_entry=ibov[0] if ibov else None,
                    ibov_source=ibov[1] if ibov else None,
                )
                store.update_signal(position["signal_id"], status="aberta")
                position = store.conn.execute("SELECT * FROM positions WHERE id=?", (position["id"],)).fetchone()
                status = "aberta"
        exit_date = date.fromisoformat(position["exit_date"]) if position["exit_date"] else None
        if exit_date is None or status != "aberta":
            continue
        if now.date() == exit_date and not reminders_only:
            signal = store.signal_by_id(position["signal_id"])
            data_com = date.fromisoformat(position["data_com"]) if position["data_com"] else None
            body = exit_message(
                ticker=position["ticker"],
                exit_date=exit_date,
                data_com=data_com,
                reminder=False,
                simulacao=cfg.simulacao,
            )
            _queue(cfg, store, report, f"saida|{position['id']}|{exit_date.isoformat()}", "saida", signal["id"], body)
        if calendar.near_close(now, exit_date):
            body = exit_message(
                ticker=position["ticker"],
                exit_date=exit_date,
                data_com=date.fromisoformat(position["data_com"]) if position["data_com"] else None,
                reminder=True,
                simulacao=cfg.simulacao,
            )
            _queue(cfg, store, report, f"lembrete|{position['id']}|{exit_date.isoformat()}", "lembrete", position["signal_id"], body)
        if status == "aberta" and position["entry_price"] is not None:
            filled_exit = _price(store, position["ticker"], exit_date, now, calendar)
            if filled_exit:
                _close_position(cfg, store, report, position, filled_exit)


def _close_position(cfg, store, report, position, filled_exit: tuple[float, str]) -> None:
    entry = float(position["entry_price"])
    exit_price, exit_source = filled_exit
    entry_date = date.fromisoformat(position["entry_date"])
    exit_date = date.fromisoformat(position["exit_date"])
    data_com = date.fromisoformat(position["data_com"]) if position["data_com"] else None
    kind = position["kind"] or "dividendo"
    gross = float(position["gross_per_share"] or 0)
    net = float(position["net_per_share"]) if position["net_per_share"] is not None else None
    dividend = dividend_received(kind, gross, net, entry_date, data_com)
    result = trade_return(entry, exit_price, dividend)
    ibov_entry = _index_level(store, entry_date)
    ibov_exit = _index_level(store, exit_date)
    ibov = None
    ibov_source = None
    if ibov_entry and ibov_exit:
        ibov = index_return(ibov_entry[0], ibov_exit[0])
        ibov_source = ibov_exit[1]
    if not cfg.dry_run:
        store.update_position(
            position["id"],
            status="encerrada",
            exit_price=exit_price,
            exit_source=exit_source,
            dividend_received=dividend,
            trade_return=result,
            ibov_entry=ibov_entry[0] if ibov_entry else position["ibov_entry"],
            ibov_exit=ibov_exit[0] if ibov_exit else None,
            ibov_return=ibov,
            ibov_source=ibov_source,
            closed_at=cfg.now.isoformat(timespec="seconds"),
        )
        store.update_signal(position["signal_id"], status="encerrada")
    returns = realized_returns(store)
    if cfg.dry_run:
        returns.append(result)
    cumulative = cumulative_result(returns)
    body = result_message(
        ticker=position["ticker"],
        entry_price=entry,
        entry_date=entry_date,
        entry_source=position["entry_source"] or "",
        exit_price=exit_price,
        exit_date=exit_date,
        exit_source=exit_source,
        dividend=dividend,
        kind=kind,
        gross=gross,
        trade=result,
        ibov=ibov,
        ibov_source=ibov_source,
        cumulative=cumulative,
        closed_trades=len(returns),
        simulacao=cfg.simulacao,
    )
    mismatch = None
    signal = store.signal_by_id(position["signal_id"])
    if data_com is not None and signal and signal["issuer"]:
        mismatch = _cross_check(signal["issuer"], data_com, gross, kind)
    if mismatch:
        body += f"\nConferência com a B3: {mismatch}"
    _queue(cfg, store, report, f"resultado|{position['id']}", "resultado", position["signal_id"], body)


def execute(cfg: Config, command: str) -> Report:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    calendar = Calendar()
    store = Store(cfg.db_path)
    report = Report(now=cfg.now, dry_run=cfg.dry_run)
    try:
        final = last_final_session(cfg.now, calendar)
        start = final - timedelta(days=160)
        try:
            ensure_cotahist(store, calendar, start, final)
            ensure_index(store, start, final)
            store.commit()
        except Exception as exc:
            report.errors.append(f"cotações falharam ({type(exc).__name__})")
            log.warning("cotações", exc_info=True)
        if command == "run":
            _ingest(cfg, store, report)
            store.commit()
            _scan(cfg, store, calendar, report)
            store.commit()
            _advance(cfg, store, calendar, report, reminders_only=False)
        elif command == "exit-reminder":
            _advance(cfg, store, calendar, report, reminders_only=True)
        store.commit()
    finally:
        store.close()
    return report
