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
55 16 * * 1-5 cd /opt/alertas_dividendo && /opt/alertas_dividendo/.venv/bin/python -m alertas_dividendo exit-reminder
```

O `run` das 8h às 22h cobre a entrega do documento e a compra no pregão
seguinte. O lembrete de venda sai na hora do call daquele pregão: desde as
16:00 num dia comum (call 16:55) e desde as 17:00 na Quarta-feira de Cinzas
(call 17:55, fechamento 18:00). O crontab das 16:55 não pega a Cinzas; o
`run` das 17:00 pega. Um sinal cujo fechamento de compra já passou é ignorado
(`prazo de compra já passou`) e não abre posição. O comando não vende de
verdade: só avisa.

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

A hora decide a regra de compra:

- entrega durante o pregão: comprar no fechamento do pregão seguinte, e o
  rendimento usa o fechamento anterior;
- entrega depois do call (ou em dia sem pregão): comprar no fechamento do
  pregão seguinte, e o rendimento usa o fechamento do dia, se o pregão existiu.

Se a CVM responder `SolicitarCaptcha = S`, o comando para, grava o dia, manda
um aviso no Telegram e não consulta o ENET de novo até o dia seguinte. O aviso
sai no máximo uma vez por dia. Não tentamos contornar o captcha.

Apoio, com atraso de 1 a 3 dias: o ZIP anual de IPE dos dados abertos. A B3
lista o provento em geral só depois da data-com; serve para conferir valor e
datas, não para disparar a compra. Preço e volume vêm do COTAHIST. Se o arquivo
do dia ainda não saiu, o fechamento do dia pode vir do Yahoo Finance, e a
mensagem diz a origem de cada número.

## Regras (não alterar)

1. Gatilho: dividendo ou JCP tornado público na CVM (aviso ou ata).
2. Rendimento entre 2% e 30% sobre o último fechamento. No JCP o rendimento usa
   o valor bruto; o alerta mostra também o líquido (15% retido).
3. Volume financeiro médio de 60 pregões de pelo menos R$ 1 milhão.
4. Compra no fechamento do primeiro pregão depois do anúncio. O alerta tem de
   chegar antes desse call.
5. Venda no fechamento do 20º pregão depois da data-com. A posição precisa
   estar aberta na data-com.
6. No máximo 10 posições, peso igual. A 11ª é ignorada e registrada.
7. O texto pede ordem limitada perto do fechamento e corretora sem corretagem.

Se o PDF não entregar o valor por ação, o alerta sai mesmo assim, marcado
**conferir valor**.
