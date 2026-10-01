# Pré-registro das grades

Este arquivo fixa as configurações antes de olhar qualquer resultado. O que está aqui não é reescolhido depois do backtest. Custos, slippage, IR, calendário e a regra de pregão incompleto são os que o motor já usa.

O catálogo do projeto tem **20** configurações: 8 de momentum, 9 de reversão do gap e 3 de rompimento da faixa de abertura. Gap e ORB se dividem em núcleo e extras. Os extras também entram no N do Sharpe deflacionado.

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

## Núcleo

Quatro variantes por instrumento. É a grade usada para comparar motores. Num comando com um ativo e uma fonte, N do núcleo = 4. Com vários ativos, uma linha por ativo.

### Reversão do gap

Ceretta e Da Costa (2017), *Economics Bulletin* 37(4). Três limiares, a mesma saída.

- Gap = ln(abertura / fechamento anterior). A comparação é nesse logaritmo. ln(1,005) fica abaixo de 0,5%, então um retorno simples de 0,5% não dispara o limiar de 0,5%.
- Abertura, para o gap: o open da primeira barra da sessão regular dentro da tolerância de 5 minutos a partir da abertura oficial. Nas barras agregadas não há coluna de leilão; esse open ocupa o lugar do leilão.
- Fechamento anterior: o da mesma série no pregão anterior. No WIN, nunca o de outro vencimento. Sem esse fechamento, o sinal é pulado com aviso. Isso não é um pregão incompleto.
- Se gap ≤ −x, compra. Se gap ≥ +x, vende. No meio, não opera.
- x ∈ {0,5%, 1%, 1,5%}, os três iguais para WIN e ações. `BOVA11` usa os mesmos três.
- Entrada: open da barra que começa 1 minuto depois do início da sessão regular. No 1 minuto isso é 09:01 no WIN e 10:01 na ação (13:01 na Quarta-feira de Cinzas). Se essa barra não existe, o pregão é pulado (`window=trade`). Não se substitui pela das 09:02.
- No timeframe mais grosso que 1 minuto essa barra não cabe no relógio. A entrada é então o open da primeira barra cujo início é maior ou igual a esse instante e não passa da tolerância de 5 minutos. No 5 minutos, a barra das 10:00 começa antes do minuto 1 e não é a entrada; a das 10:05 começa quatro minutos depois e ainda cabe na tolerância. A das 10:10 não cabe.
- Saída do núcleo: 15 minutos depois da entrada, no open da barra que começa nesse instante. No 1 minuto, entrada às 09:01 sai no open das 09:16. No 5 minutos, entrada às 10:05 sai no open das 10:20.

### ORB

Uma variante: faixa de 5 minutos.

- Faixa: máxima e mínima de `[abertura, abertura + 5 min)`.
- As duas janelas são verificadas antes de procurar rompimento. A faixa é `window=signal`. A operação vai do fim da faixa até a saída forçada e é `window=trade`.
- O primeiro fechamento estritamente fora da faixa define o lado. A entrada é o open da barra seguinte. No máximo uma operação por dia. Se o rompimento é a última barra, não há entrada e o dia não é pulado.
- Stop no outro extremo. Abertura além do stop sai nesse open. Abertura exatamente no stop sai no stop nesse instante. Encosto no stop sai no preço do stop no fechamento da barra. Sem alvo.
- Saída forçada: fim do contínuo, quando começa o leilão de fechamento do à vista. O relógio é o do calendário, o mesmo para ações e WIN. No pregão ordinário vigente isso é 16:55. Na Quarta-feira de Cinzas o call começa às 17:55, e a saída vai para lá. Não é o fechamento do WIN às 18:25. O preço é o close da barra que termina nesse instante (1 minuto: 16:54; 5 minutos: 16:50).

## Extras

Oito variantes por instrumento. Entram no mesmo comando e no mesmo N do Sharpe deflacionado. Não substituem o núcleo.

- Gap com os mesmos três limiares e saída em 30 minutos, no mesmo desenho dos 15: open da barra que começa 30 minutos depois da entrada.
- Gap com os mesmos três limiares e saída no fim do dia. Fim do dia é o mesmo instante da saída forçada do ORB: 16:55 nas ações e no WIN no pregão ordinário vigente, ou o início do call quando o calendário muda.
- ORB com faixas de 15 e 30 minutos. Stop e saída forçada iguais aos do núcleo.

Se a saída de 15 ou 30 minutos cair depois do fim do contínuo, ela encosta nesse fim e usa o preço de fim de dia. Isso é o limite do pregão, não uma escolha a mais.

## O que estas grades não variam

Custos padrão do instrumento (WIN: R$ 0,50 por contrato por lado e 1 tick; ação: 0,023% por lado e 1 tick de R$ 0,01), IR de 20% com compensação dentro da amostra, capital de referência de R$ 10.000, quantidade 1, cobertura mínima 0,9 e tolerância de 5 minutos. Cobertura 0, nos testes sintéticos, só desliga a fração. Pregão que mistura dois vencimentos é pulado. A Quarta-feira de Cinzas não é descartada nestas duas estratégias: a abertura das 13:00 entra no calendário.

## N de cada estudo

Por instrumento, o núcleo tem 4 linhas e os extras têm 8. O Sharpe deflacionado de todas as linhas do comando, núcleo e extras, usa N = linhas do núcleo + linhas dos extras, vezes ativos e fontes. Os extras contam nesse N. `--n-trials` maior substitui esse número, para informar o N acumulado do projeto (o catálogo completo é 20). Um valor menor não reduz N.

O relatório mostra o núcleo primeiro e os extras depois. A média líquida, a taxa de acerto e os dois t-stats são por operação, depois dos custos e antes do IR mensal. O retorno da operação é o pnl dividido pelo nocional (`preço de entrada × valor do ponto × quantidade`). O t-stat por trade é a média desses retornos dividida pelo erro padrão, com desvio amostral. O t-stat diário faz antes a média dos trades de cada pregão, inclusive quando vários ativos operam no mesmo dia, e só então calcula o t-stat dessa série. Trades do mesmo dia deixam de contar como observações independentes. Dia sem trade não entra nessa série.

O Yahoo, em 5 minutos, não passa de 59 dias corridos. Uma janela de 60 pregões não cabe; o comando usa no máximo essa janela e o relatório fica com o número de pregões que voltou. A base local de ZIPs da B3 não está nesta máquina.
