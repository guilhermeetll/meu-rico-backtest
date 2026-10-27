"""Leitura dos documentos da CVM: consulta do dia (ENET/RAD) e dados abertos (IPE).

A resposta do ENET não é JSON de verdade. Cada documento vem numa string
separada por `&*`, com campos separados por `$&`, e o link do PDF está no
onclick `OpenDownloadDocumentos(sequencia, versao, protocolo, tipo)`.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime

from alertas_dividendo.calendar import TZ

PROVENTO_RE = re.compile(
    r"provento|dividendo|juros sobre (o )?capital|\bjcp\b|\bjscp\b",
    re.IGNORECASE,
)
TRIGGER_CATEGORIES = {
    "Aviso aos Acionistas",
    "Reunião da Administração",
    "Assembleia",
    "Comunicado ao Mercado",
    "Fato Relevante",
    "Relatório Proventos",
    "Relatório de Proventos",
}
AVISO_OU_ATA = {
    "Aviso aos Acionistas",
    "Reunião da Administração",
    "Assembleia",
}
_DOWNLOAD = re.compile(
    r"OpenDownloadDocumentos\('(\d+)','(\d+)','([^']*)'(?:,'([^']*)')?"
)
_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{2}):(\d{2}))?")
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Filing:
    protocol: str
    version: int
    cvm_code: str
    company_name: str
    category: str
    subject: str
    species: str
    published_at: datetime
    time_known: bool
    reference_day: datetime
    url: str
    source: str
    active: bool

    @property
    def reference_date(self):
        return self.reference_day.date()


def norm_cvm(code: str) -> str:
    """`02012-5` e `20125` viram `20125` (código com dígito verificador, sem zeros)."""
    digits = re.sub(r"\D", "", code or "")
    if not digits:
        return ""
    return str(int(digits))


def relevant(filing: Filing) -> bool:
    category = filing.category or ""
    if category not in TRIGGER_CATEGORIES and "provento" not in category.casefold():
        return False
    blob = f"{filing.subject} {filing.species}"
    return PROVENTO_RE.search(blob) is not None


def is_aviso_ou_ata(category: str) -> bool:
    return category in AVISO_OU_ATA


def _strip(value: str) -> str:
    return _TAG.sub("", value or "").strip()


def _parse_when(text: str) -> tuple[datetime, bool]:
    match = _DATE.search(_strip(text))
    if not match:
        raise ValueError(f"sem data em {text!r}")
    day, month, year, hour, minute = match.groups()
    if hour is None:
        moment = datetime(int(year), int(month), int(day), 23, 59, tzinfo=TZ)
        return moment, False
    moment = datetime(int(year), int(month), int(day), int(hour), int(minute), tzinfo=TZ)
    return moment, True


def download_url(sequence: str, version: str, protocol: str, kind: str) -> str:
    return (
        "https://www.rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx?"
        f"Tela=ext&numSequencia={sequence}&numVersao={version}"
        f"&numProtocolo={protocol}&descTipo={kind}&CodigoInstituicao=1"
    )


def parse_enet_dados(dados: str) -> list[Filing]:
    filings: list[Filing] = []
    if not dados:
        return filings
    for chunk in dados.split("&*"):
        if "$&" not in chunk:
            continue
        fields = chunk.split("$&")
        if fields and fields[-1] == "":
            fields = fields[:-1]
        if len(fields) < 12:
            continue
        link = _DOWNLOAD.search(fields[10])
        if not link:
            continue
        sequence, version, protocol, kind = link.groups()
        if not protocol:
            continue
        kind = kind or "IPE"
        protocol_key = str(int(protocol)) if protocol.isdigit() else protocol
        try:
            published, time_known = _parse_when(fields[6])
            reference, _ = _parse_when(fields[5])
        except ValueError:
            continue
        status = _strip(fields[7]).lower()
        filings.append(
            Filing(
                protocol=protocol_key,
                version=int(version),
                cvm_code=norm_cvm(fields[0]),
                company_name=_strip(fields[1]),
                category=_strip(fields[2]),
                subject=_strip(fields[11]),
                species=_strip(fields[4]),
                published_at=published,
                time_known=time_known,
                reference_day=reference,
                url=download_url(sequence, version, protocol, kind),
                source="enet",
                active=status == "ativo",
            )
        )
    return filings


def _protocol_from_link(link: str, fallback: str) -> str:
    match = re.search(r"numProtocolo=(\d+)", link or "")
    if match:
        return str(int(match.group(1)))
    digits = re.sub(r"\D", "", fallback or "")
    return digits or fallback


def parse_ipe_csv(text: str) -> list[Filing]:
    """IPE de dados abertos. A data de entrega não tem hora."""
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    filings: list[Filing] = []
    for row in reader:
        category = (row.get("Categoria") or "").strip()
        if category not in TRIGGER_CATEGORIES:
            continue
        subject = (row.get("Assunto") or "").strip()
        species = (row.get("Especie") or "").strip()
        if not PROVENTO_RE.search(f"{subject} {species}"):
            continue
        delivered = (row.get("Data_Entrega") or "").strip()
        reference = (row.get("Data_Referencia") or "").strip() or delivered
        try:
            published = datetime.strptime(delivered[:10], "%Y-%m-%d").replace(
                hour=23, minute=59, tzinfo=TZ
            )
            ref = datetime.strptime(reference[:10], "%Y-%m-%d").replace(tzinfo=TZ)
        except ValueError:
            continue
        link = (row.get("Link_Download") or "").strip()
        protocol = _protocol_from_link(link, row.get("Protocolo_Entrega") or "")
        if not protocol:
            continue
        try:
            version = int(row.get("Versao") or 1)
        except ValueError:
            version = 1
        tipo = (row.get("Tipo_Apresentacao") or "")
        filings.append(
            Filing(
                protocol=protocol,
                version=version,
                cvm_code=norm_cvm(row.get("Codigo_CVM") or ""),
                company_name=(row.get("Nome_Companhia") or "").strip(),
                category=category,
                subject=subject,
                species=species,
                published_at=published,
                time_known=False,
                reference_day=ref,
                url=link,
                source="ipe",
                active=not tipo.upper().startswith("IN"),
            )
        )
    return filings
