"""PDFs reais da CVM de 27/10/2026: Gerdau (GGBR) e Metalúrgica Gerdau (GOAU).

1580325 e 1580330 são o mesmo aviso conjunto. 1580329 e 1580333 são atas da
Gerdau S.A. 1580335 e 1580336 são atas da Metalúrgica. O valor único de
"ordinária e preferencial" tem de sair para as duas espécies, e a linha de
cada companhia não pode ir para o outro código.
"""

from datetime import date, datetime
from pathlib import Path

from alertas_dividendo.calendar import TZ, Calendar
from alertas_dividendo.decide import Prior, Snapshot, decide
from alertas_dividendo.extract import combine_same_record_date, extract_distributions
from alertas_dividendo.pdftext import pdf_variants

FIXTURES = Path(__file__).parent / "fixtures" / "cvm"
PUBLISHED = date(2026, 10, 27)
RECORD = date(2026, 11, 10)
EX_DATE = date(2026, 11, 11)
CAL = Calendar()

GGBR_DOCS = ("1580325.pdf", "1580330.pdf", "1580329.pdf", "1580333.pdf")
GOAU_DOCS = ("1580325.pdf", "1580330.pdf", "1580335.pdf", "1580336.pdf")
JOINT = ("1580325.pdf", "1580330.pdf")
GERDAU_MINUTES = ("1580329.pdf", "1580333.pdf")
METALURGICA_MINUTES = ("1580335.pdf", "1580336.pdf")


def _engine_text(name: str) -> str:
    """O mesmo critério do scanner: o texto do PDF que acha valor e data-com."""
    best = ""
    best_score = -1
    for variant in pdf_variants((FIXTURES / name).read_bytes()):
        found = extract_distributions(variant, published=PUBLISHED)
        score = (2 if found else 0) + (1 if any(item.data_com for item in found) else 0)
        if score > best_score:
            best = variant
            best_score = score
    assert best_score >= 3, name
    return best


def _one(name: str, ticker: str):
    found = extract_distributions(_engine_text(name), ticker=ticker, published=PUBLISHED)
    assert len(found) == 1, (name, ticker, found)
    return found[0]


def test_joint_notice_splits_companies_and_ignores_adr():
    for name in JOINT:
        variants = pdf_variants((FIXTURES / name).read_bytes())
        assert len(variants) >= 1
        for variant in variants:
            for ticker in ("GGBR3", "GGBR4"):
                found = extract_distributions(variant, ticker=ticker, published=PUBLISHED)
                assert len(found) == 1, (name, ticker)
                assert abs(found[0].gross - 0.34) < 1e-9
                assert found[0].data_com == RECORD
                assert found[0].data_ex == EX_DATE
                assert found[0].kind == "dividendo"
                assert found[0].share_class == "any"
            for ticker in ("GOAU3", "GOAU4"):
                found = extract_distributions(variant, ticker=ticker, published=PUBLISHED)
                assert len(found) == 1, (name, ticker)
                assert abs(found[0].gross - 0.16) < 1e-9
                assert found[0].data_com == RECORD
                assert found[0].data_ex == EX_DATE
                assert found[0].kind == "dividendo"
            both = extract_distributions(variant, published=PUBLISHED)
            assert {round(item.gross, 2) for item in both} == {0.16, 0.34}
            assert all(item.data_com == RECORD for item in both)
            assert date(2026, 11, 12) not in {item.data_com for item in both}


def test_board_minutes_apply_one_value_to_both_classes():
    for name in GERDAU_MINUTES:
        for ticker in ("GGBR3", "GGBR4"):
            item = _one(name, ticker)
            assert abs(item.gross - 0.34) < 1e-9, name
            assert item.gross < 1
            assert item.data_com == RECORD
            assert item.data_ex != date(2026, 12, 15)
            assert item.kind == "dividendo"
            assert item.share_class == "any"
    for name in METALURGICA_MINUTES:
        for ticker in ("GOAU3", "GOAU4"):
            item = _one(name, ticker)
            assert abs(item.gross - 0.16) < 1e-9, name
            assert item.gross < 1
            assert item.data_com == RECORD
            assert item.data_ex != date(2026, 12, 16)
            assert item.kind == "dividendo"
            assert item.share_class == "any"


def test_duplicate_protocols_collapse_to_one_event_per_ticker():
    """Aviso conjunto repetido e atas do mesmo crédito são um evento por papel."""
    published = datetime(2026, 10, 27, 18, 0, tzinfo=TZ)
    snap = Snapshot(5.0, date(2026, 10, 26), "COTAHIST", 2_000_000, 60)
    expected = {"GGBR3": 0.34, "GGBR4": 0.34, "GOAU3": 0.16, "GOAU4": 0.16}
    docs = {"GGBR3": GGBR_DOCS, "GGBR4": GGBR_DOCS, "GOAU3": GOAU_DOCS, "GOAU4": GOAU_DOCS}
    for ticker, gross in expected.items():
        items = []
        for name in docs[ticker]:
            items.extend(
                extract_distributions(_engine_text(name), ticker=ticker, published=PUBLISHED)
            )
        merged = combine_same_record_date(items)
        assert len(merged) == 1, (ticker, merged)
        assert abs(merged[0].gross - gross) < 1e-9
        assert merged[0].data_com == RECORD
        assert merged[0].kind == "dividendo"
        first = decide(
            ticker=ticker,
            kind="dividendo",
            gross=merged[0].gross,
            data_com=RECORD,
            approval=date(2026, 10, 26),
            published_at=published,
            time_known=True,
            category_is_aviso_ou_ata=True,
            snapshot=snap,
            open_positions=0,
            priors=[],
            calendar=CAL,
        )
        assert first.action == "comprar", (ticker, first.reason)
        again = decide(
            ticker=ticker,
            kind="dividendo",
            gross=merged[0].gross,
            data_com=RECORD,
            approval=date(2026, 10, 27),
            published_at=published,
            time_known=True,
            category_is_aviso_ou_ata=True,
            snapshot=snap,
            open_positions=1,
            priors=[Prior(merged[0].gross, RECORD, "dividendo", date(2026, 10, 26))],
            calendar=CAL,
        )
        assert again.action == "ignorar" and again.reason == "duplicado"
