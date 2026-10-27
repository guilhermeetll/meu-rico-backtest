"""GET e POST com timeout e User-Agent. Sem estado."""

from __future__ import annotations

import logging

import requests

UA = (
    "Mozilla/5.0 (compatible; alertas-dividendo/1.0; "
    "+https://dados.cvm.gov.br) paper-trading"
)


def get(url: str, *, timeout: int = 60, headers: dict | None = None) -> bytes:
    merged = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        merged.update(headers)
    response = requests.get(url, headers=merged, timeout=timeout)
    response.raise_for_status()
    return response.content


def post_raw(url: str, body: str, *, timeout: int = 180, headers: dict | None = None) -> dict:
    """POST do corpo exatamente como a consulta pública do ENET espera.

    O formulário manda um objeto no estilo JavaScript, com chaves sem aspas.
    Não passar por `json=`, que reescreveria o corpo.
    """
    merged = {
        "Content-Type": "application/json; charset=utf-8",
        "Referer": "https://www.rad.cvm.gov.br/ENETWeb/frmConsultaExternaCVM.aspx",
        "User-Agent": UA,
    }
    if headers:
        merged.update(headers)
    response = requests.post(url, data=body.encode("utf-8"), headers=merged, timeout=timeout)
    if response.status_code >= 400:
        logging.getLogger(__name__).warning(
            "ENET HTTP %s: %s", response.status_code, response.text[:400].replace("\n", " ")
        )
    response.raise_for_status()
    return response.json()


def post_json(url: str, payload: dict, *, timeout: int = 90, headers: dict | None = None) -> dict:
    merged = {
        "User-Agent": UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json; charset=utf-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.rad.cvm.gov.br/ENETWeb/frmConsultaExternaCVM.aspx",
    }
    if headers:
        merged.update(headers)
    response = requests.post(url, json=payload, headers=merged, timeout=timeout)
    response.raise_for_status()
    return response.json()
