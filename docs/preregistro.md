# Pré-registro das grades

Este arquivo fixa as configurações antes de olhar qualquer resultado. O que está aqui não é reescolhido depois do backtest. Custos, slippage, IR, calendário, regra de pregão incompleto e o Sharpe deflacionado são os que o motor já usa.

O catálogo do projeto tem **14** configurações: 8 de momentum, 3 de reversão do gap e 3 de rompimento da faixa de abertura.

## Momentum intraday

Já publicada. A referência, o fim do sinal e a janela da operação formam as 8 variantes. `signal_minutes` = 30, `trade_minutes` = 30, `threshold` = 0 e `quantity` = 1.

| Referência (`signal_anchor`) | Fim do sinal (`signal_end`) | Janela (`trade_window`) |
| --- | --- | --- |
| `session_open` | `session_open` | `session_close` |
| `session_open` | `session_open` | `before_cash_auction` |
| `session_open` | `cash_open` | `session_close` |
| `session_open` | `cash_open` | `before_cash_auction` |
| `prior_close` | `session_open` | `session_close` |
| `prior_close` | `session_open` | `before_cash_auction` |
| `prior_close` | `cash_open` | `session_close` |
| `prior_close` | `cash_open` | `before_cash_auction` |

`prior_close` é o fechamento do pregão anterior do mesmo contrato (Gao, Han, Li e Zhou, 2018). Sem esse fechamento, o sinal daquele pregão é pulado. A Quarta-feira de Cinzas continua fora quando `skip_ash_wednesday` está ligado, que é o padrão desta estratégia.

## Reversão do gap de abertura

Ceretta e Da Costa (2017), *Economics Bulletin* 37(4). Três variantes por instrumento, uma para cada saída. O limiar não é eixo da grade.

- Gap = ln(abertura / fechamento anterior). A comparação é nesse logaritmo. ln(1,005) fica abaixo de 0,5%, então um retorno simples de 0,5% não dispara o limiar de 0,5%.
- Abertura: o preço de abertura da primeira barra da sessão regular dentro da tolerância da primeira barra (padrão 5 minutos a partir da abertura oficial). Nas barras agregadas não há coluna de leilão; o primeiro negócio da sessão regular ocupa esse lugar, e a entrada nunca é nessa mesma barra.
- Fechamento anterior: o da mesma série no pregão anterior. No WIN, nunca o de outro vencimento (`previous_close` / `prior_close_same_contract`). Sem esse fechamento, o sinal é pulado com aviso. Isso não é um pregão incompleto.
- Se gap ≤ −x, compra. Se gap ≥ +x, vende. No meio, não opera.
- x = 0,5% para a família WIN e para os símbolos `BVSP`, `IBOV`, `IBOVESPA` e `^BVSP`. x = 1% para o resto, inclusive `BOVA11`.
- Entrada: abertura da primeira barra cujo início é ao menos um timeframe depois da barra de abertura, até a tolerância de 5 minutos. O relógio de 15 ou 30 minutos conta dessa entrada efetiva. Se a saída programada passaria do fim do pregão regular, ela encosta nesse fim.
- Fim do dia: última barra regular antes do leilão de fechamento. No à vista, o leilão começa em `cash_session` (fechamento oficial menos 5 minutos): a última barra de 1 minuto num pregão ordinário de 2026 fecha às 16:55. No WIN o calendário não separa um call; o fim é o `session_bounds` (18:25 desde 11/03/2024, mais cedo no vencimento). A saída é o fechamento da barra que termina nesse instante.
- Saídas da grade: `15`, `30` e `eod`.
- Sem preço de abertura na tolerância, o pregão entra em `skipped` com `window=signal`. Sem barra de entrada, ou com a janela da operação incompleta, `window=trade`.
- Pregão que mistura dois vencimentos é pulado, como no momentum. A Quarta-feira de Cinzas não é descartada: a abertura das 13:00 entra no calendário.

## Rompimento da faixa de abertura

Estratégia de controle. Três variantes por instrumento, N ∈ {5, 15, 30}.

- Faixa: máxima e mínima dos primeiros N minutos da sessão regular, janela `[abertura, abertura + N)`.
- As duas janelas são verificadas antes de procurar rompimento, com a mesma regra de cobertura do momentum. A faixa é `window=signal`. A operação vai de `abertura + N` até o fim do pregão regular definido acima e é `window=trade`.
- Depois da faixa, o primeiro fechamento estritamente acima da máxima compra e o primeiro estritamente abaixo da mínima vende. A entrada é a abertura da barra seguinte. No máximo uma operação por dia. Se a barra do rompimento é a última da sessão, não há entrada e o dia não é pulado.
- Stop no outro extremo da faixa. Se a barra abre além do stop, a saída é nessa abertura, no instante de abertura da barra. Se abre exatamente no stop, sai no stop nesse mesmo instante. Se a barra só encosta no stop, sai no preço do stop no fechamento da barra. Sem alvo.
- O que não aciona o stop sai no fim do dia, no mesmo relógio da reversão do gap.
- `signal_return` é o afastamento do fechamento que rompeu em relação ao extremo rompido (`fechamento / extremo − 1`), com sinal.

## O que estas grades não variam

Custos padrão do instrumento (WIN: R$ 0,50 por contrato por lado e 1 tick; ação: 0,023% por lado e 1 tick de R$ 0,01), IR de 20% com compensação dentro da amostra, capital de referência de R$ 10.000, quantidade 1, cobertura mínima 0,9 e tolerância de 5 minutos. Cobertura 0, nos testes sintéticos, só desliga a fração.

## N do Sharpe deflacionado

N de cada linha é o total de variantes testadas naquele estudo. No comando abaixo, o padrão é o número de linhas da grade pedida (estratégias × variantes × ativos × fontes). `--n-trials` maior substitui esse número, para informar o N acumulado do projeto. Um valor menor não reduz N abaixo da grade. O catálogo de 14 é a soma das três grades; um estudo que repete as mesmas saídas em vários ativos conta uma linha por ativo.

O Yahoo, em 5 minutos, não passa de 59 dias corridos. Uma janela de cerca de 60 pregões não cabe; o comando usa no máximo essa janela e o relatório fica com o número de pregões que voltou.
