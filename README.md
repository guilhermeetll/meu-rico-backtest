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

Para ver a estratégia de momentum sem o histórico completo, deixe a fonte em **CSV** e a opção **Usar o CSV de exemplo do WINV26**. Esse arquivo está em `backend/sample_data/win_exemplo_1min.csv`, marcado no cabeçalho como exemplo. São barras de 1 minuto agregadas de negócios reais do WINV26 (17 a 30/09/2026), não o ZIP original.

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

Abertura 09:00. O fechamento automático:

- desde 11/03/2024, 18:25 o ano inteiro (comunicado da B3 de 23/01/2024 e Ofício Circular 013/2024-PRE);
- antes disso, 17:55 enquanto durava o horário de verão dos EUA (segundo domingo de março até o dia anterior ao primeiro domingo de novembro) e 18:25 fora desse intervalo;
- no vencimento do contrato específico (quarta-feira mais próxima do dia 15 do mês do código): 18:00 a partir de 04/11/2024 (Ofício Circular 132/2024-PRE) e 17:00 antes disso.

Dá para fixar abertura e fechamento em `HH:MM` no lugar de `auto`. Para ações o padrão é 10:00–17:00, uma aproximação do mercado à vista, também editável. Leilões do pregão regular (sessão 1) permanecem nas barras.

## Estratégia de momentum

Há duas referências de sinal, as duas na API (`signal_anchor`) e na tela:

- `session_open` (padrão): preço no fim da primeira janela (30 minutos) dividido pela abertura do pregão, menos um. É o movimento só da manhã.
- `prior_close`: o mesmo preço no fim da primeira janela dividido pelo fechamento anterior **do mesmo contrato**, menos um. Inclui o gap noturno, como em Gao, Han, Li e Zhou (2018), *Market intraday momentum*. Usa `return_versus_prior_close`. Se esse fechamento não está na série, o sinal daquele pregão é pulado — não se empresta o fechamento de outro vencimento.

Compra se passar do limiar, vende se ficar abaixo do limiar negativo, e fica de fora no meio.

A janela da operação também tem duas opções (`trade_window`):

- `session_close` (padrão): entra nos últimos `trade_minutes` (30) e zera no fechamento do pregão. No WIN automático isso é 17:55–18:25. Abertura, fechamento e duração continuam editáveis (`HH:MM` ou `auto`).
- `before_cash_auction`: entra às 16:25 e zera às 16:55, antes do leilão de fechamento do mercado à vista. Os horários da outra opção continuam configuráveis; esta é a alternativa pronta para não operar depois que o à vista já fechou.

Não há posição overnight. Um timeframe mais grosso que a janela do sinal não olha o miolo de uma barra ainda aberta: o pregão é pulado.

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

**Yahoo Finance.** Ações, com sufixo `.SA` se você não informar (`PETR4` vira `PETR4.SA`). 5 minutos até cerca de 60 dias corridos; 60 minutos até cerca de 730. O relógio da barra é o de abertura do candle.

**CSV de barras.** Colunas `datetime` (ou `data` e `hora`), `open`/`abertura`, `high`/`máxima`, `low`/`mínima`, `close`/`fechamento` e `volume`/`quantidade`. Separador vírgula ou ponto e vírgula. Decimal com ponto ou vírgula. Horário sem fuso é `America/Sao_Paulo`. O mapeamento pode ser `{"Abertura": "open"}` ou `{"open": "Abertura"}`. Linhas que começam com `#` são comentário.

## Como acrescentar uma estratégia

Crie uma classe em `backend/engine/strategies/` herdando de `Strategy`, com `id`, `label`, `description`, `param_schema` e `generate`. `generate` devolve operações cruas (preço de mercado, sem custo) e avisos. O motor aplica custo, slippage, IR e métricas. Registre a classe em `STRATEGIES`, em `backend/engine/strategies/registry.py`. Se o sinal usar fechamento anterior, chame `return_versus_prior_close` ou `contract_prior_close` e pule o pregão quando o retorno vier vazio. Não misture dois vencimentos no mesmo dia: `contracts_of` com mais de um contrato deve descartar o sinal.

## Como acrescentar uma fonte de dados

Implemente `load(self, request: DataRequest) -> LoadResult` devolvendo barras com `timestamp` (abertura da barra, fuso `America/Sao_Paulo`), `open`, `high`, `low`, `close`, `volume` e, se for contrato, `contract`. Registre a fonte em `backend/app/service.py` (`_load`) e em `backend/app/routes.py` (`data_sources`). O motor não importa FastAPI; a fonte também não precisa importar.

## O que fica gravado

O Postgres guarda cada execução (pedido, métricas, curva e operações) para o histórico da tela. O banco sobe no Compose com usuário e senha `backtest`, só para desenvolvimento local.
