"""CVM (ENET no mesmo dia, com hora de entrega) e o cadastro de emissores da B3.

A consulta de hora em hora usa o mesmo pedido da consulta pública do ENET
(`ListarDocumentos`), só nas categorias ligadas a provento, com pausa entre
pedidos. Se a CVM pedir captcha, a varredura para. Não há nova tentativa.
"""

from __future__ import annotations

import base64
import calendar
import io
import json
import logging
import time
import zipfile
from datetime import date, timedelta

from alertas_dividendo.cvm_parse import Filing, parse_enet_dados, parse_ipe_csv
from alertas_dividendo.http_client import get, post_raw

log = logging.getLogger(__name__)

ENET_URL = "https://www.rad.cvm.gov.br/ENETWeb/frmConsultaExternaCVM.aspx/ListarDocumentos"
# Aviso aos Acionistas, Fato Relevante, Comunicado ao Mercado,
# Reunião da Administração, Assembleia, Relatório Proventos.
ENET_CATEGORY_CODES = (3, 4, 6, 2, 1, 107)
ENET_PAUSE_SECONDS = 5.0
IPE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{year}.zip"
B3_COMPANIES = (
    "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall/"
    "GetInitialCompanies/{token}"
)
B3_SUPPLEMENT = (
    "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall/"
    "GetListedSupplementCompany/{token}"
)


def _b3_token(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.b64encode(raw).decode()


class CaptchaRequired(RuntimeError):
    """A CVM pediu captcha. Parar. Não contornar e não repetir na hora."""

    def __init__(self, message: str, partial: list[Filing] | None = None) -> None:
        super().__init__(message)
        self.partial = list(partial or [])


def enet_categoria() -> str:
    return ",".join(f"IPE_{code}_-1_-1" for code in ENET_CATEGORY_CODES)


def enet_body(start: date, end: date) -> str:
    """Corpo idêntico ao de `frmConsultaExternaCVM.js` quando o captcha está desligado."""
    return (
        "{ dataDe: '%s', dataAte: '%s' , empresa: '', setorAtividade: '-1', categoriaEmissor: '-1', "
        "situacaoEmissor: '-1', tipoParticipante: '-1', dataReferencia: '', categoria: '%s', periodo: '2', "
        "horaIni: '', horaFim: '', palavraChave:'',ultimaDtRef:'false', tipoEmpresa:'0', token: '', versaoCaptcha: ''}"
        % (start.strftime("%d/%m/%Y"), end.strftime("%d/%m/%Y"), enet_categoria())
    )


def month_spans(start: date, end: date) -> list[tuple[date, date]]:
    """Um pedido por mês, como o coletor mensal. A janela do cron cabe em um."""
    if end < start:
        return []
    spans: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        last = calendar.monthrange(cursor.year, cursor.month)[1]
        month_end = date(cursor.year, cursor.month, last)
        span_end = min(month_end, end)
        spans.append((cursor, span_end))
        cursor = span_end + timedelta(days=1)
    return spans


def _transient(exc: Exception) -> bool:
    """500 e queda de rede. Captcha não entra aqui."""
    name = type(exc).__name__
    return name in {"HTTPError", "ConnectionError", "Timeout", "ConnectTimeout", "ReadTimeout"} or (
        "Timeout" in name or "Connection" in name
    )


def enet_paused(stored_day: str | None, today: date) -> bool:
    """Depois de um captcha, o ENET fica em pausa até o dia seguinte."""
    return bool(stored_day) and stored_day == today.isoformat()


def _listar(start: date, end: date, poster) -> list[Filing]:
    payload = poster(ENET_URL, enet_body(start, end))
    data = payload.get("d") or {}
    if data.get("SolicitarCaptcha") == "S":
        raise CaptchaRequired("CVM pediu captcha — parando (não contornar).")
    if data.get("temErro"):
        raise RuntimeError(data.get("msgErro") or "ENET temErro")
    return parse_enet_dados(data.get("dados") or "")


def fetch_enet(
    start: date,
    end: date,
    *,
    pause: float = ENET_PAUSE_SECONDS,
    sleeper=time.sleep,
    poster=None,
) -> list[Filing]:
    """Consulta ENET das categorias de provento. Pausa de 5 s entre meses.

    `--hoje` é um dia só: um pedido. Um HTTP 500 ganha uma pausa e uma
    repetição. Captcha interrompe na hora e não repete.
    """
    poster = poster or post_raw
    found: list[Filing] = []
    spans = month_spans(start, end)
    for index, (left, right) in enumerate(spans):
        if index:
            sleeper(pause)
        try:
            found.extend(_listar(left, right, poster))
        except CaptchaRequired as exc:
            raise CaptchaRequired(str(exc), partial=found) from None
        except Exception as exc:
            if not _transient(exc):
                raise
            # O ENET às vezes responde 500 no primeiro POST e aceita o seguinte.
            # Uma pausa e uma repetição. Captcha não chega neste ramo.
            log.warning("ENET falhou (%s); uma nova tentativa depois de %.0f s", type(exc).__name__, pause)
            sleeper(pause)
            try:
                found.extend(_listar(left, right, poster))
            except CaptchaRequired as captcha:
                raise CaptchaRequired(str(captcha), partial=found) from None
    return found


def fetch_ipe(year: int) -> list[Filing]:
    payload = get(IPE_URL.format(year=year), timeout=120)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name = archive.namelist()[0]
        text = archive.read(name).decode("latin1")
    return parse_ipe_csv(text)


def fetch_issuers() -> list[tuple[str, str, str]]:
    url = B3_COMPANIES.format(token=_b3_token({"language": "pt-br"}))
    body = json.loads(get(url, timeout=60, headers={"Accept": "application/json"}))
    rows = []
    for item in body.get("results") or []:
        code = str(item.get("codeCVM") or "").strip()
        issuer = str(item.get("issuingCompany") or "").strip()
        name = str(item.get("companyName") or "").strip()
        if code and issuer:
            rows.append((str(int(code)), issuer, name))
    return rows


def fetch_b3_supplement(issuer: str) -> dict:
    """Caderno da B3: proventos em dinheiro e eventos em ações."""
    url = B3_SUPPLEMENT.format(token=_b3_token({"issuingCompany": issuer, "language": "pt-br"}))
    raw = json.loads(get(url, timeout=40, headers={"Accept": "application/json"}))
    if isinstance(raw, str):
        raw = json.loads(raw)
    block = raw[0] if isinstance(raw, list) and raw else raw
    if not isinstance(block, dict):
        return {}
    return block


def fetch_b3_dividends(issuer: str) -> list[dict]:
    """Proventos que a B3 já lista. Em geral só aparecem depois da data-com."""
    return list(fetch_b3_supplement(issuer).get("cashDividends") or [])


def fetch_b3_splits(issuer: str) -> list[dict]:
    """Desdobramentos e grupamentos. A lista também traz bonificações."""
    return list(fetch_b3_supplement(issuer).get("stockDividends") or [])


def download_pdf(url: str) -> bytes:
    return get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/pdf,*/*"})
