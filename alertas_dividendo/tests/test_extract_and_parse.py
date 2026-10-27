"""Extração de PDF (texto) e os parsers de ENET e COTAHIST."""

from datetime import date, datetime

from alertas_dividendo.cotahist import build_quote_line, parse_cotahist
from alertas_dividendo.cvm_parse import norm_cvm, parse_enet_dados, parse_ipe_csv
from alertas_dividendo.extract import Distribution, combine_same_record_date, extract_distributions
from alertas_dividendo.messages import DISCLAIMER, buy_message, result_message

SAUD = """
Comunicamos aos Senhores Acionistas que, em Reunião do Conselho de Administração
realizada em 21 de outubro de 2026, os Conselheiros aprovaram o pagamento de dividendos.
1. Farão jus aos dividendos os detentores de ações de emissão da Companhia em 30 de outubro de 2026.
2. Montante total bruto dos dividendos: R$1.000.000.000,00 correspondentes a R$0,3420595420 por ação.
4. As ações da Companhia serão negociadas EX-direito a dividendos a partir de 03 de novembro de 2026.
"""

AGRO = """
dividendos intermediários declarados em 21 de outubro de 202 6, no montante
de R$ 30 .000.000,00 (trinta milhões de reais), equivalente a R$ 0,30115808232 por
ação ordinária, com base na posição acionária ao final do pregão do dia 21 de outubro de 202 6.
A partir do pregão de 22 de outubro de 202 6, inclusive, as ações serão negociadas ex-dividendos.
"""

IGTI = """
Data de Corte
15/10/2026
16/10/2026
29/10/2026
0,02412606561
0,07237819684
0,16888245929
"""

JCP = """
Aprovar o pagamento de juros sobre o capital próprio.
Valor bruto de R$ 1,00 por ação.
Valor líquido de R$ 0,85 por ação.
Farão jus os detentores de ações em 30 de outubro de 2026.
"""


def test_saud_aviso_extracts_value_dates_and_ignores_the_billion():
    found = extract_distributions(SAUD, ticker="SAUD3", published=date(2026, 10, 21))
    assert len(found) == 1
    item = found[0]
    assert item.kind == "dividendo"
    assert abs(item.gross - 0.3420595420) < 1e-12
    assert item.gross < 1
    assert item.data_com == date(2026, 10, 30)
    assert item.data_ex == date(2026, 11, 3)


ATA_EX = """
Farão jus os detentores de ações em 30 de outubro de 2026.
Fica consignado que a partir de 03 de novembro de 2026, inclusive, as ações serão negociadas ex-direito a dividendos.
Os procedimentos relativos ao pagamento dos dividendos, que ocorrerá em 30 de março de 2027, serão informados por aviso.
Valor de R$ 0,3420595420 por ação.
"""


def test_ata_ex_ignores_the_payment_date_months_later():
    found = extract_distributions(ATA_EX, ticker="SAUD3", published=date(2026, 10, 21))
    assert found
    assert found[0].data_com == date(2026, 10, 30)
    assert found[0].data_ex == date(2026, 11, 3)


def test_agro_eleven_decimals_and_split_year():
    found = extract_distributions(AGRO, ticker="AGRO3", published=date(2026, 10, 21))
    assert found
    item = found[0]
    assert abs(item.gross - 0.30115808232) < 1e-12
    assert item.data_com == date(2026, 10, 21)
    assert item.data_ex == date(2026, 10, 22)


def test_igti_table_picks_the_unit_column():
    found = extract_distributions(IGTI, ticker="IGTI11", published=date(2026, 10, 20))
    assert found
    item = found[0]
    assert abs(item.gross - 0.16888245929) < 1e-12
    assert item.data_com == date(2026, 10, 15)
    assert item.data_ex == date(2026, 10, 16)


def test_jcp_gross_and_stated_net():
    found = extract_distributions(JCP, ticker="SAUD3", published=date(2026, 10, 21))
    assert found
    item = found[0]
    assert item.kind == "jcp"
    assert item.gross == 1.0
    assert item.net == 0.85


MTRE = """
aprovou dividendos intermediários no montante de R$ 12.000.000,00,
equivalente a R$ 0,11344855041 por ação ordinária.
O pagamento será efetuado em 3 (três) parcelas de R$ 4.000.000,00.
   01ª      4.000.000,00    0,03781618347       15/10/2026   20/10/2026   21/10/2026   30/10/2026
   02ª      4.000.000,00    0,03781618347       15/10/2026   13/11/2026   16/11/2026   27/11/2026
   03ª      4.000.000,00    0,03781618347       15/10/2026   11/12/2026   14/12/2026   23/12/2026
  Total    12.000.000,00    0,11344855041
"""


def test_mtre_installments_are_three_events():
    found = extract_distributions(MTRE, ticker="MTRE3", published=date(2026, 10, 15))
    assert len(found) == 3
    assert [item.data_com for item in found] == [
        date(2026, 10, 20),
        date(2026, 11, 13),
        date(2026, 12, 11),
    ]
    assert [item.data_ex for item in found] == [
        date(2026, 10, 21),
        date(2026, 11, 16),
        date(2026, 12, 14),
    ]
    for item in found:
        assert abs(item.gross - 0.03781618347) < 1e-12
        assert item.gross < 0.05
    assert abs(sum(item.gross for item in found) - 0.11344855041) < 1e-9
    headline = Distribution(
        "dividendo", 0.11344855041, None, date(2026, 10, 20), None, "on"
    )
    merged = combine_same_record_date([headline, *found])
    assert len(merged) == 3
    assert all(abs(item.gross - 0.03781618347) < 1e-9 for item in merged)


SAME_RECORD = """
Aprovaram dividendos de R$ 0,12 por ação e juros sobre o capital próprio de R$ 0,08 por ação.
Farão jus os detentores de ações em 30 de outubro de 2026.
"""


def test_same_record_date_dividend_and_jcp_are_summed():
    found = extract_distributions(SAME_RECORD, ticker="ZZZZ3", published=date(2026, 10, 21))
    assert len(found) == 1
    assert found[0].kind == "misto"
    assert abs(found[0].gross - 0.20) < 1e-12
    assert abs(found[0].net - (0.12 + 0.08 * 0.85)) < 1e-12
    assert found[0].data_com == date(2026, 10, 30)


CLASSES = """
Aprovaram dividendos de R$ 0,50 por ação ordinária, R$ 0,40 por ação preferencial
e R$ 0,90 por unit.
Farão jus os detentores de ações em 30 de outubro de 2026.
"""

CLASS_TABLE = """
Data de Corte
30/10/2026
03/11/2026
30/11/2026
0,50000000000
0,40000000000
0,90000000000
"""


def test_on_pn_and_units_keep_their_own_values():
    prose = {
        "PETR3": 0.50,
        "PETR4": 0.40,
        "TAEE11": 0.90,
        "SANB11": 0.90,
    }
    for ticker, gross in prose.items():
        found = extract_distributions(CLASSES, ticker=ticker, published=date(2026, 10, 21))
        assert len(found) == 1, ticker
        assert abs(found[0].gross - gross) < 1e-12, ticker
        assert found[0].data_com == date(2026, 10, 30)
    table = {"PETR3": 0.50, "PETR4": 0.40, "TAEE11": 0.90, "SANB11": 0.90}
    for ticker, gross in table.items():
        found = extract_distributions(CLASS_TABLE, ticker=ticker, published=date(2026, 10, 21))
        assert len(found) == 1, ticker
        assert abs(found[0].gross - gross) < 1e-12, ticker


GARBLED_INSTALLMENTS = """
equivalente a R$ 0,11344855041 por ação ordinária.
O pagamento será efetuado em 3 (três) parcelas de R$ 4.000.000,00.
"""


def test_unparsed_installments_stay_empty():
    assert extract_distributions(GARBLED_INSTALLMENTS, ticker="MTRE3", published=date(2026, 10, 15)) == []


def test_empty_text_means_conferir_valor():
    assert extract_distributions("A companhia aprova dividendos, sem informar o valor.") == []


def test_enet_row_and_cvm_code():
    raw = (
        "02012-5$&BRADSAUDE S.A.$&Aviso aos Acionistas$&Outros avisos$&"
        "<spanOrder>Dividendos</spanOrder> - $&"
        "<spanOrder>20261021</spanOrder> 21/10/2026$&"
        "<spanOrder>20261021</spanOrder> 21/10/2026 17:44$&Ativo$&1$&AP$&"
        "<i onclick=OpenDownloadDocumentos('1103821','1','1579115','IPE')></i>$&"
        "Dividendos/Juros sobre Capital Próprio$&&*"
    )
    filings = parse_enet_dados(raw)
    assert len(filings) == 1
    filing = filings[0]
    assert filing.protocol == "1579115"
    assert filing.cvm_code == "20125"
    assert norm_cvm("00867-2") == "8672"
    assert filing.time_known is True
    assert filing.published_at == datetime(2026, 10, 21, 17, 44, tzinfo=filing.published_at.tzinfo)
    assert "numProtocolo=1579115" in filing.url
    assert filing.active is True


def test_ipe_csv_has_no_clock():
    text = (
        "CNPJ_Companhia;Nome_Companhia;Codigo_CVM;Data_Referencia;Categoria;Tipo;Especie;"
        "Assunto;Data_Entrega;Tipo_Apresentacao;Protocolo_Entrega;Versao;Link_Download\n"
        "00;BRADSAUDE;20125;2026-10-21;Aviso aos Acionistas;Outros;Dividendos;"
        "Pagamento de Dividendos;2026-10-21;AP;x;1;"
        "https://www.rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx?numProtocolo=1579115\n"
    )
    filings = parse_ipe_csv(text)
    assert len(filings) == 1
    assert filings[0].time_known is False
    assert filings[0].protocol == "1579115"
    assert filings[0].cvm_code == "20125"


def test_cotahist_roundtrip():
    line = build_quote_line("20261021", "SAUD3", 15.42, 70_354_057.0)
    bars = parse_cotahist(line + "\n")
    assert len(bars) == 1
    assert bars[0].ticker == "SAUD3"
    assert bars[0].close == 15.42
    assert bars[0].volume == 70_354_057.0
    assert bars[0].session_date == date(2026, 10, 21)


def test_messages_carry_simulacao_and_jcp_net():
    from alertas_dividendo.calendar import TZ

    body = buy_message(
        ticker="SAUD3",
        company="BRADSAUDE S.A.",
        kind="jcp",
        gross=1.0,
        net=0.85,
        yld=0.025,
        last_close=40,
        last_close_date=date(2026, 10, 21),
        last_close_source="COTAHIST",
        approval=date(2026, 10, 21),
        published_at=datetime(2026, 10, 21, 17, 37, tzinfo=TZ),
        time_known=True,
        timing="apos_fechamento",
        reference_close_date=date(2026, 10, 22),
        reference_close_used=False,
        data_com=date(2026, 10, 30),
        data_ex=date(2026, 11, 3),
        buy_date=date(2026, 10, 22),
        exit_date=date(2026, 12, 1),
        url="https://www.rad.cvm.gov.br/exemplo",
        protocol="1579113",
        slot_number=1,
        late=False,
        entry_price=None,
        entry_source=None,
        simulacao=True,
    )
    assert body.startswith("SIMULAÇÃO")
    assert "líquido" in body
    assert "último fechamento disponível" in body
    assert "22/10/2026" in body
    assert "depois do fechamento" in body
    assert "0,850000" in body
    assert "ordem limitada" in body
    assert DISCLAIMER.split("—")[0].strip() in body
    hidden = result_message(
        ticker="SAUD3",
        entry_price=10,
        entry_date=date(2026, 10, 22),
        entry_source="COTAHIST",
        exit_price=11,
        exit_date=date(2026, 12, 1),
        exit_source="COTAHIST",
        dividend=0.85,
        kind="jcp",
        gross=1,
        trade=0.185,
        ibov=0.01,
        ibov_source="Yahoo Finance ^BVSP",
        cumulative=0.0185,
        closed_trades=1,
        simulacao=False,
    )
    assert "SIMULAÇÃO" not in hidden
    assert "JCP líquido" in hidden
    assert "ignora custos, impostos e o CDI" in hidden
    missing_com = buy_message(
        ticker="SAUD3",
        company="BRADSAUDE S.A.",
        kind="dividendo",
        gross=0.34,
        net=None,
        yld=0.022,
        last_close=15.42,
        last_close_date=date(2026, 10, 21),
        last_close_source="COTAHIST",
        approval=date(2026, 10, 21),
        published_at=datetime(2026, 10, 21, 17, 37, tzinfo=TZ),
        time_known=True,
        timing="apos_fechamento",
        reference_close_date=date(2026, 10, 22),
        reference_close_used=False,
        data_com=None,
        data_ex=None,
        buy_date=date(2026, 10, 22),
        exit_date=None,
        url="https://www.rad.cvm.gov.br/exemplo",
        protocol="1579113",
        slot_number=1,
        late=False,
        entry_price=None,
        entry_source=None,
        simulacao=True,
    )
    assert "ATENÇÃO: a data-com não é conhecida antes desta compra" in missing_com
