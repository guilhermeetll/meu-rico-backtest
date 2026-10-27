# Alertas de dividendo (simulação)

Monitor de paper trading da estratégia congelada de swing de dividendo da B3
(candidata 3 do estudo). Roda fora do Docker deste repositório, num Linux com
cron. Cada hora, em dia de pregão, consulta a CVM, decide se cabe uma compra e
manda o alerta no Telegram antes do fechamento.

Toda mensagem leva o aviso **SIMULAÇÃO**, a menos que `ALERTAS_SIMULACAO=0`.

## Instalar

```bash
cd alertas_dividendo
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
sudo apt-get install -y poppler-utils   # pdftotext; sem ele o código usa pypdf
```

Os testes:

```bash
.venv/bin/pytest
```

## Variáveis

Copie `.env.example`. O cron precisa enxergá-las (`set -a && . /caminho/.env`).

| Variável | Função |
| --- | --- |
| `ALERTAS_DATA_DIR` | Pasta do SQLite, dos PDFs e do log. Também `--data-dir`. |
| `TELEGRAM_BOT_TOKEN` | Token do BotFather. |
| `TELEGRAM_CHAT_ID` | Conversa que recebe os alertas. |
| `ALERTAS_SIMULACAO` | `1` (padrão) coloca o aviso de simulação. `0` tira. |
| `ALERTAS_LOOKBACK_DAYS` | Dias de CVM para trás. Padrão `12`. `--hoje` força `0`. |

O chat id:

1. Abra o bot no Telegram e mande uma mensagem.
2. `python -m alertas_dividendo chat-id`

## Comandos

Na pasta `alertas_dividendo`, com o venv ativo (ou `python -m` a partir dela,
porque o pacote está neste diretório):

```bash
cd alertas_dividendo
python -m alertas_dividendo run --dry-run          # varre e imprime, não envia
python -m alertas_dividendo run --hoje --dry-run   # só o dia de hoje no ENET
python -m alertas_dividendo run                    # envia o que estiver no prazo
python -m alertas_dividendo exit-reminder          # lembrete de venda perto do call
python -m alertas_dividendo entry-reminder         # lembrete de compra na abertura, antes das 10h
python -m alertas_dividendo status
```

`run` é idempotente. O mesmo protocolo não gera dois alertas de compra. Estado
e log ficam em `$ALERTAS_DATA_DIR`:

- `alertas.sqlite` — documentos, sinais, posições, alertas já enviados, eventos ignorados
- `logs/alertas.log`
- `pdfs/` — PDFs já baixados

## Crontab

Horário de São Paulo. `CRON_TZ` vale no cron do Vixie; se o da máquina não
entender, deixe o sistema em `America/Sao_Paulo`.

```cron
CRON_TZ=America/Sao_Paulo
ALERTAS_DATA_DIR=/var/lib/alertas_dividendo
TELEGRAM_BOT_TOKEN=coloque_o_token
TELEGRAM_CHAT_ID=coloque_o_chat
0 8-22 * * 1-5 cd /opt/alertas_dividendo && /opt/alertas_dividendo/.venv/bin/python -m alertas_dividendo run
0 9 * * 1-5 cd /opt/alertas_dividendo && /opt/alertas_dividendo/.venv/bin/python -m alertas_dividendo entry-reminder
25 16 * * 1-5 cd /opt/alertas_dividendo && /opt/alertas_dividendo/.venv/bin/python -m alertas_dividendo run
55 16 * * 1-5 cd /opt/alertas_dividendo && /opt/alertas_dividendo/.venv/bin/python -m alertas_dividendo exit-reminder
```

O `scheduler.py` do servidor fica fora deste repositório. Ele precisa do job
das 9h com `python -m alertas_dividendo entry-reminder`, ao lado do
`exit-reminder` das 16:55, e de um `run` extra às 16:25 nos dias úteis. O
`run` das 8h também cai na janela do lembrete de entrada (8:00 inclusive até
10:00) e o aviso sai uma vez só.

O `run` das 16:25 usa o mesmo comando. Não há subcomando novo. 16:25 ainda é
antes do corte das 16:30, então um aviso entregue mais cedo e visto nessa
passada compra o fechamento do dia. O que já foi avisado não sai de novo: a
chave do alerta é única e a reserva é gravada antes do envio. O fechamento
oficial ainda não saiu (17:00; 18:00 na Cinzas), então essa passada não
preenche preço nem manda resultado. O lembrete de entrada só vale até as
10:00. O lembrete de saída, se o `run` das 16:00 já mandou, não repete. O
programa não toma flock; o agendador do host deve usar o mesmo lock em todo
`run`, `entry-reminder` e `exit-reminder`, para a passada das 16:00 e a das
16:25 não disputarem o SQLite. Se as duas coincidirem mesmo assim, a chave
única impede o segundo Telegram.

O `run` das 8h às 22h cobre a entrega do documento e manda o alerta na hora
em que a varredura vê o aviso. Compra no fechamento do mesmo dia tem de sair
nessa passada, antes do call. Compra na abertura do pregão seguinte leva o
lembrete antes das 10:00 do dia da entrada. O lembrete de venda sai na hora
do call daquele pregão: desde as 16:00 num dia comum (call 16:55) e desde as
17:00 na Quarta-feira de Cinzas (call 17:55, fechamento 18:00). O crontab das
16:55 não pega a Cinzas; o `run` das 17:00 pega. Cada lembrete sai uma vez
por posição por dia. Um sinal cujo prazo de compra já passou — fechamento às
16:30 ou abertura às 10:00 (13:00 na Cinzas), com valor lido ou em “conferir
valor” — é ignorado (`prazo de compra já passou`) e não abre posição nem
manda alerta. O comando não compra nem vende de verdade: só avisa.

## De onde vêm os documentos

Fonte principal, a cada hora: a consulta pública do ENET
(`frmConsultaExternaCVM.aspx/ListarDocumentos`), a mesma do coletor do Trader.
O pedido pede só categorias de provento (Aviso aos Acionistas, Fato Relevante,
Comunicado ao Mercado, Reunião da Administração, Assembleia e Relatório
Proventos) e devolve a **hora de entrega**. O corpo é o de
`frmConsultaExternaCVM.js`. Um dia (`--hoje`) é um único pedido. Uma janela
que cruza o mês vira um pedido por mês, com pausa de 5 segundos entre eles.
Se o serviço responder 500, há uma pausa e uma segunda tentativa. Se pedir
captcha, para na hora, sem repetir.

A hora de entrega no ENET, em `America/Sao_Paulo`, escolhe a entrada:

- Antes das 16:30 num pregão, e a varredura vê o aviso nesse mesmo dia antes
  das 16:30: compra no fechamento desse dia. O alerta sai na hora da
  detecção, para a ordem entrar antes do call (16:55; na Cinzas, 17:55).
- Às 16:30 ou depois, em dia sem pregão (fim de semana ou feriado) ou sem
  hora na fonte: compra na abertura do pregão seguinte. O alerta sai na
  detecção. No dia da entrada sai um lembrete antes das 10:00 (na Cinzas a
  abertura é às 13:00; o lembrete continua antes das 10:00).
- Aviso entregue antes das 16:30 mas só visto às 16:30 ou depois: já não dá
  para comprar o fechamento daquele dia. A compra passa para a abertura do
  pregão seguinte e o log grava `deteccao_tardia`.

16:30 em ponto já é tarde para o fechamento do dia. O call começa às 16:55
(17:55 na Cinzas), então 16:30 ainda deixa tempo de colocar a ordem. O corte
não acompanha o call da Cinzas.

A tela de yield (2% inclusive, menos de 30%) usa o último fechamento já
publicado. O preço de entrada ainda não existe quando o alerta tem de sair:
o fechamento do dia imprime às 17:00, depois das 16:30, e a abertura imprime
na manhã seguinte. A varredura não espera esse preço. O retorno simulado,
quando o pregão imprime, usa o fechamento ou a abertura do dia da entrada,
conforme o tipo. A saída não muda: fechamento do 20º pregão depois da
data-com. O Ibovespa da comparação continua no fechamento do pregão.

Se a CVM responder `SolicitarCaptcha = S`, o comando para, grava o dia, manda
um aviso no Telegram e não consulta o ENET de novo até o dia seguinte. O aviso
sai no máximo uma vez por dia. Não tentamos contornar o captcha.

Apoio, com atraso de 1 a 3 dias: o ZIP anual de IPE dos dados abertos. A B3
lista o provento em geral só depois da data-com. Num aviso em “conferir valor”,
depois da data-com o monitor grava o R$/ação da API no log e na tabela
`b3_checks`. Isso não gera alerta, posição nem sinal atrasado. Preço e volume
vêm do COTAHIST. Se o arquivo do dia ainda não saiu, o fechamento do dia pode
vir do Yahoo Finance, e a mensagem diz a origem de cada número. Uma linha do
Yahoo, mesmo com volume zero, não substitui o arquivo do dia e não entra na
média de 60 pregões. Quando o COTAHIST chega, ele grava o volume oficial por
cima. Desdobramento ou grupamento no meio da posição é registrado no log e na
mensagem de saída; o preço não é ajustado.

Parcelas com data-com diferente são eventos separados: cada uma enfrenta o
piso de 2% sozinha, com a própria saída (20 pregões depois da sua data-com).
A entrada segue a hora de entrega do aviso, não a data-com da parcela. Valores só
se somam quando caem na mesma data-com (dividendo e JCP pagos juntos). O
yield desse total usa o bruto; o caixa recebido retém 15% só na parte de JCP.
Um mesmo R$/ação só é duplicata se a data-com for a mesma, ou se o aviso
anterior ainda não tinha data-com e for o mesmo anúncio. JCP trimestral de
valor fixo e parcelas iguais com data-com diferente continuam eventos novos.
Uma retificação do mesmo papel, da mesma aprovação e da mesma data-com, com
outro valor, atualiza o sinal que já existe e manda um único aviso de valor
corrigido. Se o yield corrigido sai da faixa e a compra ainda não foi feita,
a ordem é cancelada e o motivo fica no log.
Se o PDF fala em várias parcelas e a tabela não fecha, o aviso fica em conferir valor.
Se a data-com não saiu do PDF, o alerta de compra avisa que a saída e o
direito ainda não estão confirmados.

## Regras (não alterar)

1. Gatilho: dividendo ou JCP tornado público na CVM (aviso ou ata).
2. Rendimento de 2% inclusive até menos de 30% sobre o último fechamento
   disponível na hora do alerta. 30% exato não passa (`y < 0,30`). No JCP o
   rendimento usa o valor bruto; o alerta mostra também o líquido (15% retido).
   O retorno simulado usa o fechamento ou a abertura do dia da entrada, quando
   esse preço sai. O resultado acumulado ignora custos, impostos e o CDI do
   caixa parado.
3. Volume financeiro médio de 60 pregões de pelo menos R$ 1 milhão.
4. Entrada pela hora de entrega. Antes das 16:30 num pregão, vista a tempo:
   fechamento desse dia. Às 16:30 ou depois, dia sem pregão, fonte sem hora
   ou detecção tardia: abertura do pregão seguinte, com lembrete antes das
   10:00. A detecção tardia fica no log.
5. Venda no fechamento do 20º pregão depois da data-com. A posição precisa
   estar aberta na data-com.
6. No máximo 10 posições, peso igual. A 11ª é ignorada e registrada.
7. No fechamento, ordem limitada perto do call. Na abertura, ordem no leilão
   de abertura. Corretora sem corretagem.

Se o PDF não entregar o valor por ação, o alerta sai mesmo assim, marcado
**conferir valor**.
