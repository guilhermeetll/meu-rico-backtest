# Pré-registro das grades

Este arquivo fixa as configurações antes de olhar qualquer resultado. O que está aqui não é reescolhido depois do backtest. Custos, slippage, IR, calendário e a regra de pregão incompleto são os que o motor já usa.

O catálogo do projeto tem **23** configurações: 8 de momentum, 9 de reversão do gap e 6 de rompimento da faixa de abertura. Gap e ORB se dividem em núcleo e extras. Os extras também entram no N do Sharpe deflacionado.

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
- Abertura, para o gap: o open do primeiro negócio da sessão regular. O leilão pode imprimir depois da abertura do calendário. A tolerância padrão é 30 minutos (`open_tolerance_minutes`): o primeiro negócio até 10:30 na ação e até 09:30 no WIN ainda é a abertura. Sem negócio até lá, o pregão é pulado (`window=signal`) e o motivo fica registrado. Nas barras agregadas não há coluna de leilão; esse open ocupa o lugar do leilão.
- Fechamento anterior: o da mesma série no pregão anterior. No WIN, nunca o de outro vencimento. Sem esse fechamento, o sinal é pulado com aviso. Isso não é um pregão incompleto.
- Se gap ≤ −x, compra. Se gap ≥ +x, vende. No meio, não opera.
- x ∈ {0,5%, 1%, 1,5%}, os três iguais para WIN e ações. `BOVA11` usa os mesmos três.
- Entrada: open da primeira barra que começa pelo menos 1 minuto depois do primeiro negócio. O atraso conta desse negócio, não da abertura do calendário. A barra de abertura nunca é a entrada. Se o leilão imprime às 10:03, a entrada é a primeira barra às 10:04 ou depois. No WIN, se o primeiro negócio é a barra das 09:02, a entrada é a primeira barra às 09:03 ou depois. Sem essa barra, o pregão é pulado (`window=trade`).
- No timeframe mais grosso vale a mesma regra. No 5 minutos, a barra das 10:00 é a abertura e a entrada é a das 10:05. Se a primeira barra já é a das 10:05, ela é a abertura e a entrada é a das 10:10.
- Saída do núcleo: 15 minutos depois da entrada, no open da barra que começa nesse instante. Entrada às 10:04 sai no open das 10:19. No WIN, entrada às 09:03 sai no open das 09:18. No 5 minutos, entrada às 10:05 sai no open das 10:20.

### ORB

Uma variante no núcleo: faixa de 5 minutos, execução `stop` (ordem stop na borda).

- Faixa: máxima e mínima de `[primeiro negócio, primeiro negócio + 5 min)`. A cobertura do PR #4 mede essa janela a partir do primeiro negócio, não de 10:00–10:05. Um leilão às 10:08 produz a faixa 10:08–10:13.
- As duas janelas são verificadas antes de procurar rompimento. A faixa é `window=signal`. A operação vai do fim da faixa até a saída forçada e é `window=trade`. Sem primeiro negócio dentro da tolerância de 30 minutos, o pregão é pulado (`window=signal`).
- Entrada: o primeiro negócio, depois do fim da faixa, que sai da faixa. Compra só com preço acima da máxima. Venda só com preço abaixo da mínima. Encostar na borda não dispara. O stop fica no outro extremo e sai no primeiro negócio que encosta ou atravessa esse extremo. No máximo uma operação por dia. Sem alvo.
- Nas barras OHLC de 1 minuto, a compra dispara se a máxima da barra é maior que a máxima da faixa. O preço cru é o maior entre a abertura da barra e a borda mais 1 tick. O modelo de custos soma mais 1 tick de slippage em cima disso. A venda é o espelho: a mínima da barra menor que a mínima da faixa, o preço cru é o menor entre a abertura e a borda menos 1 tick, e o slippage tira mais 1 tick. Se a barra abre além da borda mais esse tick, o preço cru é a abertura. Um candle mais grosso, como o de 5 minutos do Yahoo, segue a mesma conta no candle inteiro.
- O stop continua no extremo quando a barra não abriu além dele, e na abertura quando já abriu além. Esse preço só leva o 1 tick de slippage. Se a barra da entrada também encosta no stop, a saída é nessa mesma barra: a ordem dos negócios não aparece no OHLC, e esse é o caso pessimista. Abertura dentro da faixa com a máxima acima e a mínima no stop ou além entra na borda mais 1 tick e para na mínima. Corretagem e emolumentos não mudam. A saída forçada também leva o 1 tick de slippage.
- Se o ZIP do tickercsv já está na base, a entrada é o preço do primeiro negócio estritamente fora da faixa, mais 1 tick de slippage. Um negócio exatamente na borda não entra. O stop é o primeiro negócio que encosta ou atravessa o outro extremo, mais 1 tick de slippage. O motor não baixa o ZIP para obter esse preço. Sem o arquivo, o pregão cai na aproximação OHLC. O relatório diz qual dos dois métodos rodou.
- Saída forçada: fim do contínuo, quando começa o leilão de fechamento do à vista. O relógio é o do calendário, o mesmo para ações e WIN. No pregão ordinário vigente isso é 16:55. Na Quarta-feira de Cinzas o call começa às 17:55, e a saída vai para lá. Não é o fechamento do WIN às 18:25. O preço é o close da barra que termina nesse instante (1 minuto: 16:54; 5 minutos: 16:50), ou o último negócio real até esse instante quando o tickercsv está disponível. Também leva o 1 tick do modelo de custos.
- Se essa barra não existe e a posição está aberta, a saída é o close do último negócio regular antes do call, dentro de `close_tolerance_minutes` (padrão 15). Um contínuo que para às 16:49 sai no close dessa barra, com aviso, e o pregão não é pulado. Um negócio às 17:05 já é leilão e não serve de saída. Sem negócio dentro da tolerância, aí sim a janela da operação é pulada.

## Extras

Onze variantes por instrumento. Entram no mesmo comando e no mesmo N do Sharpe deflacionado. Não substituem o núcleo.

- Gap com os mesmos três limiares e saída em 30 minutos, no mesmo desenho dos 15: open da barra que começa 30 minutos depois da entrada.
- Gap com os mesmos três limiares e saída no fim do dia. Fim do dia é o mesmo instante da saída forçada do ORB: 16:55 nas ações e no WIN no pregão ordinário vigente, ou o início do call quando o calendário muda.
- ORB `stop` com faixas de 15 e 30 minutos. A ordem na borda, o stop e a saída forçada são os do núcleo.
- `orb_confirm` com faixas de 5, 15 e 30 minutos. É a regra anterior: o primeiro fechamento estritamente fora da faixa define o lado, e a entrada é o open da barra seguinte. Se o rompimento é a última barra, não há entrada e o dia não é pulado. O stop e a saída forçada são os do núcleo, inclusive o 1 tick do modelo de custos. A faixa de 5 minutos nesta variante é extra, não núcleo. As três contam no N.

Se a saída de 15 ou 30 minutos cair depois do fim do contínuo, ela encosta nesse fim e usa o preço de fim de dia. Isso é o limite do pregão, não uma escolha a mais.

## O que estas grades não variam

Custos padrão do instrumento (WIN: R$ 0,50 por contrato por lado e 1 tick; ação: 0,023% por lado e 1 tick de R$ 0,01), IR de 20% com compensação dentro da amostra, capital de referência de R$ 10.000, quantidade 1, cobertura mínima 0,9 e tolerância de 5 minutos na barra de início da janela já ancorada. A abertura pode atrasar 30 minutos e o fechamento pode adiantar 15. Cobertura 0, nos testes sintéticos, só desliga a fração. Pregão que mistura dois vencimentos é pulado. A Quarta-feira de Cinzas não é descartada nestas duas estratégias: a abertura das 13:00 entra no calendário.

## N de cada estudo

Por instrumento, o núcleo tem 4 linhas e os extras têm 11. O Sharpe deflacionado de todas as linhas do comando, núcleo e extras, usa N = linhas do núcleo + linhas dos extras, vezes ativos e fontes. Os extras contam nesse N, inclusive as três de `orb_confirm`. `--n-trials` maior substitui esse número, para informar o N acumulado do projeto (o catálogo completo é 23). Um valor menor não reduz N. Um comando com WIN e uma fonte, nas duas estratégias, tem N = 15. O mesmo comando nas 9 ações tem N = 135.

O relatório mostra o núcleo primeiro e os extras depois. A média líquida, a taxa de acerto e os dois t-stats são por operação, depois dos custos e antes do IR mensal. O retorno da operação é o pnl dividido pelo nocional (`preço de entrada × valor do ponto × quantidade`). O t-stat por trade é a média desses retornos dividida pelo erro padrão, com desvio amostral. O t-stat diário faz antes a média dos trades de cada pregão, inclusive quando vários ativos operam no mesmo dia, e só então calcula o t-stat dessa série. Trades do mesmo dia deixam de contar como observações independentes. Dia sem trade não entra nessa série.

O Yahoo, em 5 minutos, não passa de 59 dias corridos. Uma janela de 60 pregões não cabe; o comando usa no máximo essa janela e o relatório fica com o número de pregões que voltou. A base local de ZIPs da B3 não está nesta máquina.
