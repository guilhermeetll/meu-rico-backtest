# Meu Rico Backtest

Simulação de estratégias de day trade na B3. Nesta fase o sistema só faz backtest: nenhuma ordem é enviada à bolsa ou à corretora.

## Como rodar

Pré-requisito: Docker com Compose v2.

```bash
cp .env.example .env
# se o histórico local já existir, aponte os caminhos do host:
# B3_DATA_HOST=/workspace/quant/dados/b3_ticks
# B3_META_HOST=/workspace/quant/dados/b3_ticks_meta
docker compose up --build
```

A interface fica em [http://localhost:8080](http://localhost:8080). A API fica em [http://localhost:8000/api/health](http://localhost:8000/api/health).

Para ver a estratégia de momentum sem o histórico completo, deixe a fonte em **CSV** e a opção **Usar as barras reais de setembro/2026**. O arquivo é `backend/sample_data/win_set2026_1min.csv` (cerca de 1,5 MB): barras de 1 minuto de WINV26 e WINZ26, de 01 a 30/09/2026, agregadas dos ZIPs originais com `TipoSessaoPregao=1` e `AcaoAtualizacao=0`. O símbolo `WIN` fica só com a série de `backend/sample_data/win_ativo.csv` em cada pregão. O recorte curto só de WINV26, de 17 a 30/09, continua em `win_exemplo_1min.csv`, marcado como exemplo. Nenhum dos dois substitui o ZIP original.

Testes do motor, sem Docker:

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest
```

Ou, com a stack no ar: `docker compose run --rm backend pytest`.

## Histórico local da B3

A raiz dos negócios é a variável `B3_DATA_ROOT`. No Compose ela aponta para `/data/b3_ticks`, montada a partir de `B3_DATA_HOST`. Na máquina do time o caminho usual é `/workspace/quant/dados/b3_ticks`. Esse diretório não vem no repositório.

Layout, um ZIP original por ativo e por pregão:

```text
{B3_DATA_ROOT}/{TICKER}/{AAAA-MM-DD}.zip
```

Exemplo: `/workspace/quant/dados/b3_ticks/WINV26/2026-09-30.zip`.

O ZIP é o corpo de `https://arquivos.b3.com.br/rapinegocios/tickercsv/{TICKER}/{AAAA-MM-DD}`, sem conversão. Dentro há um único `.txt`. O nome desse membro varia e às vezes termina em `_HHMM` (snapshot da B3). O leitor abre sempre o primeiro membro.

Texto: UTF-8 sem BOM, fim de linha LF, separador `;`, decimal com vírgula. Colunas, nesta ordem:

`DataReferencia; CodigoInstrumento; AcaoAtualizacao; PrecoNegocio; QuantidadeNegociada; HoraFechamento; CodigoIdentificadorNegocio; TipoSessaoPregao; DataNegocio; CodigoParticipanteComprador; CodigoParticipanteVendedor`.

`HoraFechamento` é lida como texto `HHMMSSmmm`, com zero à esquerda (exemplo: `090039906` = 09:00:39.906, horário de Brasília).

`TipoSessaoPregao`: `1` é o pregão regular, inclusive leilões; `6` é o after-market. Por padrão as barras excluem o after-market. A opção fica no pedido (`include_after_hours`) e na tela.

`AcaoAtualizacao`: só o valor `0` (negócio novo) foi observado nos arquivos. Qualquer outro código é tratado como cancelamento ou correção e fica de fora das barras.

Download e arquivo local são o mesmo caminho. Se `{TICKER}/{AAAA-MM-DD}.zip` já existe, ele é lido e nada é baixado. Se não existe, o adaptador baixa da B3 e grava o ZIP nesse caminho. A B3 publica cerca de 21 pregões nessa URL. Barras agregadas (OHLCV) ficam num cache à parte, `B3_BAR_CACHE_DIR`, e não entram na pasta do histórico.

Feriado com zero negócios vem só com o cabeçalho. O dia é ignorado.

Há uma amostra truncada em `backend/tests/fixtures/amostra_WINV26_2026-09-01.zip`: ZIP real do WINV26 em 01/09/2026, cortado nas primeiras 2000 linhas. Não é um pregão completo. Serve para o teste do leitor.

### WIN contínuo

Cada vencimento fica na própria pasta (`WINV26`, `WINZ26`, …). A série ativa de cada pregão está em `{B3_META_ROOT}/win_ativo.csv` (`date`, `serie_ativa`, `trades`). A lista de arquivos está em `{B3_META_ROOT}/manifest.csv` (`date`, `ticker`, `trades`, `bytes`, `inner_name`, `fetched_on`). Um arquivo é definitivo quando `fetched_on` é posterior à data do pregão.

No Compose, `B3_META_ROOT` é `/data/b3_ticks_meta`, montado de `B3_META_HOST`. Na máquina do time: `/workspace/quant/dados/b3_ticks_meta`.

O símbolo `WIN` não mistura contratos. Em cada dia o backtest lê só o ZIP da série indicada em `win_ativo.csv`. No dia da rolagem, o preço do vencimento que deixou de ser o ativo não entra na série: o salto entre contratos não vira retorno. Um sinal que precise do fechamento anterior usa o fechamento do pregão anterior **daquele mesmo contrato** (`contract_prior_close`). Se esse ZIP não existir, o sinal é pulado. A estratégia de momentum nem precisa desse fechamento: o retorno do sinal é a primeira janela do próprio pregão, e um pregão com mais de um contrato nas barras é descartado.

`trades = 0` em `win_ativo.csv` (feriado) não gera download.

Pedir um contrato explícito, por exemplo `WINV26`, lê só a pasta daquele vencimento e não consulta `win_ativo.csv`.

## Premissas de custo do WIN

Especificação do minicontrato futuro de Ibovespa:

- 1 ponto = R$ 0,20 por contrato.
- Tick mínimo = 5 pontos.
- 1 tick = 5 × 0,20 = R$ 1,00 por contrato.

Custo padrão: R$ 0,50 por contrato **por execução** (cada lado). Entrada e zeragem custam R$ 1,00 de corretagem/emolumentos, mais o slippage. Não é a tabela de uma corretora específica. A taxa do WIN é valor fixo por contrato, não um percentual do nocional.

Slippage padrão: 1 tick adverso por execução (dá para usar 2). Na compra, a entrada fica mais cara e a saída mais barata. Na venda, o contrário. Com 1 tick, a operação completa perde R$ 2,00 por contrato além da taxa.

O capital inicial padrão (R$ 10.000) só serve de denominador do retorno e do Sharpe. Não é a margem exigida pela B3.

## Premissas de custo de ações

Ação à vista (e o que não é WIN): ponto R$ 1,00. O tick é R$ 0,01 no lote-padrão e no fracionário, conforme a tabela pública de tick size da B3 de janeiro de 2021, que continua sendo a referência do mercado à vista. 1 tick = R$ 0,01 por ação.

Custo padrão, também uma premissa e não a tabela de uma corretora: **0,023% por lado** sobre o valor negociado, mais **1 tick** de slippage. O percentual incide em cada execução, sobre o preço efetivo (já com o slippage) vezes a quantidade vezes o valor do ponto. Uma compra e uma venda pagam a taxa duas vezes. O WIN não usa esse percentual; continua no valor fixo por contrato. Dá para zerar ou trocar os dois campos no formulário.

O Yahoo não tem o WIN.

## Imposto de renda

Day trade: 20% sobre o resultado, configurável.

O resultado de cada operação já está líquido de custos e slippage. A apuração é mensal:

- soma-se o mês;
- prejuízo acumulado de meses anteriores, dentro da amostra, compensa lucro;
- base positiva gera imposto e zera o saldo;
- base negativa ou zero não gera imposto e o prejuízo segue.

Não há compensação com swing trade, retenção na fonte nem DARF mínimo. Prejuízo anterior ao início da amostra começa em zero. Dentro da amostra, fora da amostra e no walk-forward, o imposto de cada fatia é recalculado só com os pregões daquela fatia.

## Horário do WIN

Abertura 09:00, exceto na Quarta-feira de Cinzas, quando abre às 13:00. O fechamento automático:

- desde 11/03/2024, 18:25 o ano inteiro ([Ofício Circular 013/2024-PRE](https://www.b3.com.br/data/files/81/36/04/4D/F333D8103152D4C8AC094EA8/OC%20013-2024%20PRE%20Novos%20horarios%20de%20negociacao%20(PT).pdf));
- antes disso, 17:55 no horário de verão dos EUA (segundo domingo de março até o dia anterior ao primeiro domingo de novembro) e 18:25 fora desse intervalo. Essa regra anterior é premissa: o ofício que fixa 18:25 o ano inteiro é o 013/2024-PRE, e as trocas de cada ano não têm um PDF neste calendário;
- no vencimento do contrato específico (quarta-feira mais próxima do dia 15 do mês do código): 18:00 a partir de 04/11/2024 ([Ofício Circular 132/2024-PRE](https://www.b3.com.br/data/files/50/55/44/FF/83D629106EEC8429AC094EA8/OC%20132-2024%20PRE%20%20Novos%20Horarios%20de%20negociacao%20(PT).pdf), mantido pelo [153/2024-PRE](https://www.b3.com.br/data/files/74/76/EA/97/36C239106EEC8429AC094EA8/OC%20153-2024%20PRE%20%20Novos%20Horarios%20de%20Negociacao%20(EN).pdf)) e 17:00 antes disso.

Na Quarta-feira de Cinzas os ofícios mudam só a abertura dos derivativos (pré-abertura 12:55–13:00, sessão a partir das 13:00). O fechamento do WIN continua o da grade ordinária, inclusive no vencimento.

Dá para fixar abertura e fechamento em `HH:MM` no lugar de `auto`. Isso só move a janela `session_close`. A janela de antes do leilão do à vista ignora esses dois campos e usa o calendário abaixo. Leilões do pregão regular (sessão 1) permanecem nas barras.

## Horário do mercado à vista

A janela `before_cash_auction` é a meia hora contínua que termina quando começa o leilão de fechamento. O leilão são os últimos 5 minutos do pregão regular. O fim do sinal `cash_open` é a abertura do à vista mais `signal_minutes` (30 minutos → 10:30 na grade atual, 13:30 na Quarta-feira de Cinzas, 11:30 quando a abertura era 11:00). Os dois seguem o mesmo calendário, também quando o ativo operado é o WIN.

| Pregões ordinários | Abertura | Contínuo até | Leilão | Janela |
| --- | --- | --- | --- | --- |
| Até 09/03/2012 | 11:00 | 17:55 | 17:55–18:00 | 17:25–17:55 |
| 12/03/2012 a 18/12/2015 | 10:00 | 16:55 | 16:55–17:00 | 16:25–16:55 |
| 21/12/2015 a 29/09/2023, horário de verão nos EUA e sem horário de verão no Brasil | 10:00 | 16:55 | 16:55–17:00 | 16:25–16:55 |
| 21/12/2015 a 29/09/2023, nos demais pregões | 10:00 | 17:55 | 17:55–18:00 | 17:25–17:55 |
| Desde 02/10/2023, o ano inteiro, inclusive novembro–março | 10:00 | 16:55 | 16:55–17:00 | 16:25–16:55 |

Setembro/2026 está na última linha: fechamento às 17:00 e janela 16:25–16:55. Dezembro/2023, janeiro/2024, novembro/2024, janeiro/2025, dezembro/2025 e um pregão comum de fevereiro/2026 também. A regra antiga, que levava a janela para 17:25–17:55 sempre que os EUA estavam fora do horário de verão, não vale nesses dados: 17:25–17:55 é o after-market da grade de verão (cancelamento 17:25–17:30 e negociação 17:30–18:00).

A Quarta-feira de Cinzas não usa essa linha. Desde 2012 o à vista e o WIN abrem às 13:00. Nos ofícios recuperados o à vista negocia das 13:00 às 17:55 e o call vai até as 18:00, então a janela, se o dia não for pulado, é 17:25–17:55. O sinal `cash_open` termina 30 minutos depois da abertura real (13:30). O sinal pela abertura do WIN também mede a partir das 13:00, não das 09:00. O parâmetro `skip_ash_wednesday` pula esses pregões e o padrão é pular.

Ofícios e avisos da grade ordinária:

- [Ofício Circular 125/2021-PRE](https://www.b3.com.br/data/files/D6/02/0D/3C/BF08C710BD0885C7AC094EA8/OC%20125-2021%20PRE%20Novos%20Hor%C3%A1rios%20de%20Negocia%C3%A7%C3%A3o%20(PT).pdf), a partir de 08/11/2021: à vista 10:00–17:55, call 17:55–18:00.
- [Ofício Circular 020/2022-PRE](https://www.b3.com.br/data/files/40/B4/53/5E/BC4EE710301EBDE7AC094EA8/OC%20020-2022%20PRE%20Novos%20Hor%C3%A1rios%20de%20Negocia%C3%A7%C3%A3o%20(PT).pdf), a partir de 14/03/2022: à vista 10:00–16:55, call 16:55–17:00, after-market 17:30–18:00.
- [Ofício Circular 125/2022-PRE](https://www.b3.com.br/data/files/43/65/64/03/D1B838101E311E28AC094EA8/OC%20125-2022-PRE%20Novos%20Hor%C3%A1rios%20de%20Negocia%C3%A7%C3%A3o%20(PT).pdf), a partir de 07/11/2022: à vista 10:00–17:55, call 17:55–18:00.
- [Bora Investir, 03/11/2023](https://borainvestir.b3.com.br/noticias/bolsa-vai-fechar-mais-tarde-a-partir-de-segunda-feira/): a partir de 06/11/2023 o fechamento do à vista vai para as 18:00. O PDF do ofício dessa segunda não foi recuperado.
- [Ofício Circular 013/2024-PRE](https://www.b3.com.br/data/files/81/36/04/4D/F333D8103152D4C8AC094EA8/OC%20013-2024%20PRE%20Novos%20horarios%20de%20negociacao%20(PT).pdf), a partir de 11/03/2024: à vista 10:00–16:55, call 16:55–17:00, after-market 17:30–18:00. WIN até 18:25 o ano inteiro.
- [Ofício Circular 132/2024-PRE](https://www.b3.com.br/data/files/50/55/44/FF/83D629106EEC8429AC094EA8/OC%20132-2024%20PRE%20%20Novos%20Horarios%20de%20negociacao%20(PT).pdf), a partir de 04/11/2024, mantido pelo [153/2024-PRE](https://www.b3.com.br/data/files/74/76/EA/97/36C239106EEC8429AC094EA8/OC%20153-2024%20PRE%20%20Novos%20Horarios%20de%20Negociacao%20(EN).pdf): à vista 10:00–17:55, call 17:55–18:00, sem after-market. No vencimento, o WIN encerra às 18:00.
- [Ofício Circular 014/2025-PRE](https://www.b3.com.br/data/files/63/C3/B3/53/16A1591029BEEC39AC094EA8/OC%20014-2025%20PRE%20Novos%20Horarios%20de%20Negociacao%202025%20vf%20(PT).pdf), a partir de 10/03/2025: à vista 10:00–16:55, call 16:55–17:00, com after-market.
- [Ofício Circular 043/2025-VNC](https://www.b3.com.br/data/files/36/22/17/A0/0131A910F51990A9AC094EA8/OC%20043-2025-VNC%20NOVOS%20HORARIOS%20DE%20NEGOCIACAO_PT.pdf), a partir de 03/11/2025, no lugar do 040/2025-VNC: à vista 10:00–17:55, call 17:55–18:00, sem after-market.
- [Ofício Circular 005/2026-PRE](https://www.b3.com.br/data/files/E3/B2/C2/12/BC09C910F37907C9AC094EA8/OC%20005-2026%20PRE%20NOVOS%20HORARIOS%20DE%20NEGOCIACAO_PT.pdf), a partir de 09/03/2026: à vista 10:00–16:55, call 16:55–17:00, cancelamento 17:25–17:30 e after-market 17:30–18:00. É a grade da [página de horário de negociação](https://www.b3.com.br/pt_br/solucoes/plataformas/puma-trading-system/para-participantes-e-traders/horario-de-negociacao/). Não há ofício posterior mudando novembro/2026, então essa grade segue.

Quarta-feira de Cinzas:

- [Ofício Circular 166/2023-PRE](https://www.b3.com.br/data/files/92/94/5A/0A/80E3B810DDBC40B8DC0D8AA8/OC%20166-2023%20PRE%20Calend%C3%A1rio%20de%20Feriados%202024%20e%20Funcionamento%20da%20B3%20em%2014.02.2024%20(Quarta-Feira%20de%20Cinzas)%20(PT).pdf), 14/02/2024: à vista das 13:00 às 17:55, call 17:55–18:00; derivativos a partir das 13:00, com o fechamento ordinário inalterado.
- [Ofício Circular 149/2024-PRE](https://www.b3.com.br/data/files/58/42/2C/24/0FDF29106EEC8429AC094EA8/OC%20149-2024%20PRE%20Calendario%20de%20Feriados%20em%202025%20e%20Funcionamento%20da%20B3%20em%2005032025%20(Quarta-Feira%20de%20Cinzas)%20(PT).pdf), 05/03/2025, e o [comunicado da B3](https://www.b3.com.br/pt_br/noticias/comunicado-8AA8D0CD94F633D2019528A0851F6085.htm): o mesmo desenho.
- [Ofício Circular 054/2025-VNC](https://www.b3.com.br/data/files/C2/E3/28/AD/4FAEA9105B12E5A9AC094EA8/OC%20054-2025-VNC%20CALENDARIO%20DE%20FERIADOS%20EM%202026%20E%20FUNCIONAMENTO%20DA%20B3%20EM%2018022026%20QUARTAFEIRA%20DE%20CINZAS_PT.pdf) e a errata [003/2026-VNC](https://www.b3.com.br/data/files/FC/55/3B/12/7FE9B9109B5E99B9AC094EA8/OC%20003-2026-VNC%20ERRATA_CALENDARIO%20DE%20FERIADOS%20EM%202026%20E%20FUNCIONAMENTO%20DA%20B3%20EM%2018022026%20QUARTAFEIRA%20DE%20CINZAS_PT.pdf), 18/02/2026, e o [comunicado de Carnaval](https://www.b3.com.br/pt_br/noticias/confira-o-funcionamento-da-b3-no-carnaval.htm): à vista 13:00–17:55, call 17:55–18:00; derivativos a partir das 13:00.

Checagem nas barras de 60 minutos do Yahoo Finance (`^BVSP` e `PETR4.SA`, vela das 60 minutos, de 27/10/2023 a 30/09/2026, 730 pregões; o relógio da barra é a abertura do candle). Em 726 pregões a última barra é a das 16:00, ou seja, o horário regular termina às 17:00. As exceções com barra das 17:00 são 14/02/2024, 05/03/2025 e 18/02/2026 — as três Quartas-feiras de Cinzas, e na PETR4 essa hora tem volume — e 30/09/2026, cuja barra das 17:00 tem volume zero. Dezembro/2023, janeiro/2024, novembro/2024, janeiro/2025, dezembro/2025 e fevereiro/2026 fora do dia 18 abrem às 10:00 e param na barra das 16:00.

Premissas, repetidas num aviso da API quando o período do backtest cai nelas:

- Até 09/03/2012 o à vista fica em 11:00–18:00. A fonte é o Estado de Minas de 13/10/2011 e a Exame de 12/03/2012, não um ofício recuperado. O fim do horário de verão americano em 07/11/2011 não devolveu o fechamento para as 17:00, e o fim do horário brasileiro em 26/02/2012 também não.
- De 12/03/2012 a 18/12/2015 o à vista fica em 10:00–17:00 o ano inteiro, inclusive no inverno americano. A CBN de 08/10/2012 registra que o horário de verão de 2012 não voltou para 11:00–18:00. Não apareceu fechamento às 18:00 nesse intervalo. A UOL/Reuters de 21/12/2015 descreve a extensão que começa na segunda seguinte como mudança em relação ao fechamento das 17:00.
- De 21/12/2015 a 29/09/2023 o fechamento acompanha o horário de verão dos EUA (17:00 dentro, 18:00 fora), com abertura fixa às 10:00. O horário de verão brasileiro, enquanto existiu, não empurra o fechamento para as 19:00. Os ofícios de 2021 e 2022 acima sustentam as pontas; as segundas intermediárias (2016–2020) não têm um PDF por ano neste calendário. O Ofício Circular 005/2020-VOP trata de flexibilização e circuit breaker, não de um pregão encerrado às 13:00.
- De 06/11/2023 a 08/03/2024, de 04/11/2024 a 07/03/2025 e de 03/11/2025 a 06/03/2026 os ofícios publicam call até as 18:00. A janela da estratégia não usa essa extensão: a última barra regular continua sendo a das 16:00, e 17:25–17:55 cairia no after-market da grade que as barras mostram. Fora desses três intervalos, o fechamento às 17:00 é o do próprio ofício (013/2024-PRE, 014/2025-PRE e 005/2026-PRE).
- Na Quarta-feira de Cinzas de 2012 a 2023 a abertura às 13:00 e o call 17:55–18:00 repetem o desenho dos ofícios de 2024, 2025 e 2026. O PDF de cada um desses anos anteriores não foi recuperado.
- Antes de 11/03/2024 o fechamento do WIN em 17:55/18:25 conforme o relógio americano é premissa, como dito acima.
- Feriados não mudam o relógio; simplesmente não há barra. Véspera de Natal e 31/12 não têm grade própria.

Para uma ação, `session_bounds` devolve a abertura e o fim do leilão. Num pregão ordinário desde outubro/2023 isso é 10:00–17:00. Na Quarta-feira de Cinzas, 13:00–18:00.

## Estratégia de momentum

Há duas referências de sinal, as duas na API (`signal_anchor`) e na tela:

- `session_open` (padrão): preço no fim do sinal dividido pela abertura do pregão, menos um.
- `prior_close`: o mesmo preço dividido pelo fechamento anterior **do mesmo contrato**, menos um. Inclui o gap noturno, como em Gao, Han, Li e Zhou (2018), *Market intraday momentum*. Usa `return_versus_prior_close`. Se esse fechamento não está na série, o sinal daquele pregão é pulado — não se empresta o fechamento de outro vencimento.

O instante desse preço também é uma opção (`signal_end`), na API e na tela, e funciona com as duas referências:

- `session_open` (padrão): `signal_minutes` depois da abertura do ativo. No WIN de hoje, 30 minutos terminam às 09:30, antes de as ações abrirem.
- `cash_open`: `signal_minutes` depois da abertura do à vista. É o corte do artigo, meia hora após a abertura do mercado de ações. Hoje isso é 10:30; na Quarta-feira de Cinzas, 13:30; em 2012, enquanto a abertura era 11:00, era 11:30.

Compra se passar do limiar, vende se ficar abaixo do limiar negativo, e fica de fora no meio.

A janela da operação também tem duas opções (`trade_window`):

- `session_close` (padrão): entra nos últimos `trade_minutes` (30) e zera no fechamento do pregão. No WIN automático isso é 17:55–18:25. Abertura, fechamento e duração continuam editáveis (`HH:MM` ou `auto`).
- `before_cash_auction`: a meia hora contínua anterior ao leilão do à vista, no calendário da seção acima. Em setembro/2026, e nos invernos de 2023 a 2026, é 16:25–16:55. Na Quarta-feira de Cinzas, se o dia não for pulado, é 17:25–17:55. O parâmetro `skip_ash_wednesday` (padrão ligado) descarta essas quartas.

Com negócios do tickercsv, o preço de um horário é o último negócio até esse instante. O sinal, a entrada e a saída usam esse preço. Às 16:25 entra no último negócio até 16:25, não no open da barra. Às 16:55 sai no último negócio até 16:55, não no close da barra das 16:54. Sem o ZIP, fica a aproximação por barra.

Não há posição overnight. Um timeframe mais grosso que a janela não olha o miolo de uma barra ainda aberta: o pregão é pulado.

Barra faltando também pula o pregão, em vez de calcular o sinal com o que sobrou. Cada janela — a do sinal e a da operação — precisa da barra de início, da barra que fecha exatamente no fim da janela e de uma cobertura mínima. A barra de início pode atrasar `edge_tolerance_minutes` (padrão 5): o primeiro negócio do WIN costuma sair às 09:02 ou 09:03, e esses minutos iniciais contam como cobertos. Uma primeira barra às 11:00, 12:00, 13:00 ou 15:00 fica fora dessa tolerância. A cobertura padrão é 90% (`min_bar_coverage`); zero desliga só a fração e mantém as duas pontas. O motivo entra em `skipped` na resposta da API e na lista «Dias pulados» da tela, com o campo `window`: `signal` para a janela do sinal e `trade` para a janela da operação. Os avisos desses pregões não são cortados no limite de 30.

Pular o dia porque a janela da operação está incompleta só acontece no backtest. Ao vivo o sinal da manhã já teria aberto a posição; se o pregão para no meio da tarde, por exemplo num circuit breaker, essa operação de estresse continua aberta. No backtest o dia some e o resultado fica mais limpo do que teria sido ao vivo. Quando há pregões assim, o resumo conta quantos foram pulados por janela de operação incompleta.

## Reversão do gap e rompimento da faixa

As duas grades seguintes estão pré-registradas em `docs/preregistro.md`, junto com as oito do momentum. O arquivo fixa as regras antes do resultado; a tela e a API só escolhem o que está nessa grade.

A reversão do gap de abertura (Ceretta e Da Costa, 2017, *Economics Bulletin* 37(4)) usa gap = abertura / fechamento anterior − 1. A abertura é o primeiro negócio da sessão regular, até 30 minutos depois do horário do calendário (`open_tolerance_minutes`). No WIN o fechamento anterior é o do mesmo vencimento. Gap no limiar ou além vende; no limiar negativo ou além, compra. Os limiares são 0,5%, 1% e 1,5%, os mesmos para WIN e ações. Com negócios do tickercsv, t0 é o timestamp exato desse primeiro negócio: a entrada é o último negócio até t0 + 1 minuto, e a saída de 15 ou 30 minutos é o último negócio até o horário da entrada mais esse prazo. Sem esses negócios, a entrada é o open da primeira barra que começa pelo menos 1 minuto depois do primeiro negócio, e nunca a própria barra de abertura. Se o leilão imprime às 10:03, a entrada é às 10:04 ou na primeira barra seguinte. No WIN, o primeiro negócio das 09:02 não é a entrada: a entrada é a barra das 09:03 ou a primeira depois dela.

O núcleo sai 15 minutos depois da entrada, no open da barra que começa nesse instante. Os extras saem em 30 minutos ou no fim do dia. Fim do dia, nas ações e no WIN, é o fim do contínuo antes do leilão do à vista: 16:55 no pregão ordinário vigente. Se a barra que fecha nesse instante não existe, a posição aberta sai no último negócio regular dentro de 15 minutos antes do call (`close_tolerance_minutes`), com aviso, e o dia não é pulado.

O rompimento da faixa de abertura é o controle. A faixa vai de t0 até t0 mais N minutos. Com o ZIP do tickercsv, t0 é o timestamp exato do primeiro negócio e o rompimento só vale depois, num negócio em t0 + N ou mais tarde. Sem o ZIP, t0 é o minuto da primeira barra. A cobertura das barras continua medida nesse minuto. O núcleo é uma ordem stop na faixa de 5 minutos: a compra só dispara acima da máxima e a venda só abaixo da mínima. Encostar na borda não entra. O stop é o outro extremo e dispara ao encostar. Nas barras OHLC, o preço cru da compra é o maior entre a abertura e a borda mais 1 tick, e o slippage soma outro tick. A venda é o espelho. Com o ZIP, a entrada é o primeiro negócio estritamente fora da faixa, mais 1 tick de slippage. Corretagem e emolumentos não mudam. A variante `orb_confirm` guarda a regra anterior, o fechamento fora da faixa com entrada na barra seguinte, nas faixas de 5, 15 e 30 minutos. Os extras são o stop de 15 e 30 minutos mais as três de `orb_confirm`. Sem alvo: a saída forçada é a mesma do fim do dia do gap. No máximo uma operação por dia. O estudo lê cada pregão do tickercsv uma vez e reparte esse pregão entre as variantes.

As duas rodam no WIN e em ações, com B3, Yahoo ou CSV. Na tela, o seletor de estratégia troca os campos e o botão da grade. O id na API é `gap_reversal` ou `opening_range_breakout`.

O comando abaixo grava CSV e Markdown. O relatório lista o núcleo e depois os extras, e diz se o gap e o ORB usaram a aproximação por barra ou o negócio do tickercsv. Por instrumento, o núcleo tem 4 linhas e os extras 11. N do Sharpe deflacionado é a soma das duas, vezes ativos e fontes, ou `--n-trials` se for maior (o catálogo do projeto soma 23). Um valor menor não reduz N. Cada linha traz o t-stat por trade e o t-stat da média diária dos trades.

```bash
cd backend
python -m app.study --start 2026-09-01 --end 2026-09-30 --symbols WIN \
  --sources csv --strategies gap_reversal,opening_range_breakout --timeframe 1min \
  --out /tmp/estudo-win
```

Sem `--timeframe`, CSV e B3 ficam em 1 minuto e o Yahoo em 5 minutos. O Yahoo de 5 minutos não passa de 59 dias corridos.

## Métricas

Sobre o capital de referência, com pregões sem operação contando retorno zero:

- retorno total e resultado líquido de custos;
- Sharpe anualizado com √252 e taxa livre de risco zero;
- Sharpe deflacionado de Bailey e López de Prado (2014): probabilidade de o Sharpe diário, não o anualizado, superar o máximo esperado entre N configurações, com assimetria e curtose. N vem do formulário. No walk-forward com grade, N é pelo menos o tamanho da grade. Num estudo com várias variantes (`POST /api/studies`), N de cada variante é pelo menos o número de variantes rodadas juntas, para o melhor resultado não parecer um teste único. Com N = 1 o referencial é zero;
- drawdown máximo em reais e em percentual da curva de capital;
- número de operações, taxa de acerto (pnl positivo sobre o total; empate não é acerto) e payoff (média do ganho sobre o módulo da média da perda);
- resultado líquido de IR e a curva de capital antes e depois do imposto, cobrado no fim de cada mês.

## Amostra e walk-forward

A divisão dentro/fora da amostra usa uma fração dos pregões, na ordem, ou uma data de corte (dentro da amostra até essa data, inclusive).

O walk-forward percorre janelas de treino e teste. Sem grade, os mesmos parâmetros são reavaliados em cada teste. Com uma grade (na tela, uma lista de limiares), o treino escolhe a configuração pela métrica pedida e o teste usa só essa. O passo padrão é o tamanho do teste. Pregão que cair em duas janelas entra uma vez.

## Outras fontes

**Yahoo Finance.** Ações, com sufixo `.SA` se você não informar (`PETR4` vira `PETR4.SA`). 5 minutos até cerca de 60 dias corridos; 60 minutos até cerca de 730. O relógio da barra é o de abertura do candle. Se o yfinance voltar vazio, a mesma série pública é lida no endpoint de gráfico.

**CSV de barras.** Colunas `datetime` ou `ts` (ou `data` e `hora`), `open`/`abertura`, `high`/`máxima`, `low`/`mínima`, `close`/`fechamento`, `volume`/`quantidade` e, se houver mais de um contrato, `ticker`. Separador vírgula ou ponto e vírgula. Decimal com ponto ou vírgula. Horário sem fuso é `America/Sao_Paulo`. O mapeamento pode ser `{"Abertura": "open"}` ou `{"open": "Abertura"}`. Linhas que começam com `#` são comentário. Com o símbolo `WIN` e vários vencimentos no arquivo, o loader usa o `win_ativo.csv` da mesma pasta e deixa um vencimento por pregão. Um ticker explícito, como `WINV26`, fica só com aquela série. Sem o `win_ativo.csv`, o pregão misto é pulado pela estratégia.

## Como acrescentar uma estratégia

Crie uma classe em `backend/engine/strategies/` herdando de `Strategy`, com `id`, `label`, `description`, `param_schema` e `generate`. `generate` devolve operações cruas (preço de mercado, sem custo), avisos e a lista de pregões pulados, cada um com `window` `signal` ou `trade`. O motor aplica custo, slippage, IR e métricas. Registre a classe em `STRATEGIES`, em `backend/engine/strategies/registry.py`. Se o sinal usar fechamento anterior, chame `previous_close` (ou `return_versus_prior_close` / `contract_prior_close`) e pule o pregão quando o retorno vier vazio. Não misture dois vencimentos no mesmo dia: `contracts_of` com mais de um contrato deve descartar o sinal. Uma grade nova entra em `docs/preregistro.md` antes de olhar o resultado.

## Como acrescentar uma fonte de dados

Implemente `load(self, request: DataRequest) -> LoadResult` devolvendo barras com `timestamp` (abertura da barra, fuso `America/Sao_Paulo`), `open`, `high`, `low`, `close`, `volume` e, se for contrato, `contract`. Registre a fonte em `backend/app/service.py` (`_load`) e em `backend/app/routes.py` (`data_sources`). O motor não importa FastAPI; a fonte também não precisa importar.

## O que fica gravado

O Postgres guarda cada execução (pedido, métricas, curva e operações) para o histórico da tela. O banco sobe no Compose com usuário e senha `backtest`, só para desenvolvimento local.
