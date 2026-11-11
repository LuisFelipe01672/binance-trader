---
name: binance-trader
description: >-
  Day trade de cripto na Binance (spot, só compra) com freios: clima do mercado,
  scanner de setups nos pares em reais (BTCBRL, ETHBRL, SOLBRL...), plano com stop e
  alvo, máquina do tempo (10.000 replays + histórico real, já descontando as taxas),
  tamanho da posição pela perda máxima, verificador de risco com circuit breaker,
  ordem OTOCO (entrada + stop + alvo juntos), envio só depois de um "sim" explícito,
  testnet por padrão, e diário com exportação CSV para o IR. Use quando o usuário
  falar em day trade, cripto, Binance, bitcoin, "tem algum trade bom agora", "monta
  um plano", "qual o stop", "quanto eu compro", "simula esse trade", "manda a ordem",
  "sair da posição", "como estou no mês", ou pedir /binance-trader.
argument-hint: "[par ou pedido, ex.: BTC, scan, status]"
---

# binance-trader

Uma linha de montagem para um trade, em que cada etapa grava em `./bt/` o que a
próxima lê:

```
regime -> scan -> plan -> sim -> size -> risk -> ticket -> (sim do usuário) -> approve -> place
depois: status, close / cancel, journal close, journal show
```

`${CLAUDE_SKILL_DIR}` é a pasta deste SKILL.md. Rode tudo a partir da pasta do projeto do usuário.

```bash
B="python3 ${CLAUDE_SKILL_DIR}/scripts/bt.py"
$B regime --write                         # clima: risco-ligado / neutro / risco-desligado
$B scan                                   # setups agora, em todos os pares em BRL com liquidez
$B scan --symbols BTC,ETH --setup rompimento --interval 15m
$B plan --symbol SOL --setup pullback     # entrada, stop, alvo (padrão 2R), taxas
$B plan --symbol BTC --entry 428000 --stop pct:1 --target 2R    # plano manual
$B sim --write                            # máquina do tempo
$B size                                   # quantidade + ordem OTOCO exata -> bt/orders.json
$B risk                                   # PASS / BLOCK, com o motivo
$B ticket                                 # o boleto em português + parâmetros exatos
$B approve --index 1 --reply "<resposta do usuário, palavra por palavra>"
$B place --index 1                        # envia (env da config: testnet por padrão)
$B status                                 # saldos e ordens abertas
$B cancel --symbol SOL --reply "sim"      # cancela ordens abertas do par (remove o stop!)
$B close --symbol SOL --qty 0.992         # monta venda a mercado; passa por ticket/approve/place
$B journal close --id 1 --exit 509.5 --reason alvo
$B journal show                           # resultado do dia, da semana e do mês
$B journal csv                            # bt/diario.csv para o contador
$B price BTC ETH
```

Dados de mercado vêm de `data-api.binance.vision` (público, sem chave). Só `place`,
`status` e `cancel` usam a conta, com as chaves nas variáveis de ambiente.

## Regras para o Claude (não negociáveis)

1. **Nunca rode `place` sem um "sim" explícito do usuário para aquela ordem específica,
   dado nesta conversa depois de ele ver o `ticket`.** Passe a resposta dele
   *literalmente* em `approve --reply`. "Acho que sim", "ok", "pode ser?" e silêncio não
   são sim. O script também recusa respostas ambíguas.
2. Um sim vale para **uma** ordem e expira em 15 minutos. Ordem nova, ou preço mudou:
   mostre o ticket de novo e pergunte de novo.
3. **Nunca peça, leia, imprima ou grave as chaves da API.** Elas ficam em
   `BINANCE_API_KEY` e `BINANCE_SECRET_KEY`, que o usuário configura sozinho.
   Não rode `env`, `printenv` nem abra arquivos de segredo.
4. **Nunca mude por conta própria** `env`, `allow_live`, os limites de perda ou
   `require_positive_edge` na config, nem defina `BT_ALLOW_LIVE`. Mudar para dinheiro
   real é decisão do usuário, tomada por ele mesmo. Se ele pedir, explique as três
   travas e deixe que ele as configure.
5. Se o `risk` der BLOCK, a resposta é não. Não procure um jeito de contornar
   (mudar alvo ou stop só para passar). Explique o motivo.
6. Se a `sim` disser **SEM VANTAGEM** ou **CARA OU COROA**, diga isso com essas palavras
   antes de qualquer coisa. Não suavize. Não incentive o trade.
7. Nunca persiga preço: se o `risk` avisar que o preço já fugiu da entrada, o certo é
   deixar passar.
8. Prazo: todo plano tem `max_hold_hours` (padrão 12h). Ao vencer, ofereça a saída
   (`cancel` do OCO + `close`), que também passa pelo sim.
9. Depois que um trade terminar, registre com `journal close`. O circuit breaker
   depende disso.
10. Sempre lembre: é ferramenta, não recomendação. A maioria de quem faz day trade perde dinheiro.

## Como cada etapa funciona

- **regime**: BTC/USDT diário contra as MME50/200 e a variação de 7 dias. Placar de -4 a +4.
  Em `risco-desligado`, o `risk` bloqueia compras novas (`block_on_risk_off`).
- **scan**: três setups de compra, na vela que acabou de fechar:
  - `rompimento`: primeiro fechamento acima da máxima das últimas 24 velas, com volume ≥ 1,5× a média. Stop no meio da faixa.
  - `pullback`: MME20 > MME50 subindo; a vela toca a MME20 e fecha acima, IFR 40–65. Stop a 1,5 ATR.
  - `repique`: IFR sai de baixo de 30, com vela verde. Stop abaixo da mínima das últimas 5 velas.

  Stops mais curtos que `min_stop_pct` (0,6%) são alargados, porque com 0,1% de taxa por lado um stop curto vira só taxa. Pares com menos de R$ 500 mil de volume em 24h ficam de fora (spread largo).
- **plan**: arredonda os preços ao tick da Binance e mostra o ganho:risco bruto e líquido.
- **sim**: (1) 10.000 replays do prazo do trade, cada vela sorteada das velas reais do próprio par no mesmo horário do dia (cripto não fecha); (2) o mesmo setup, com as mesmas regras, em cada vez que disparou nos últimos 30 dias, com walk-forward (1ª metade × 2ª metade). Tudo em R líquido de taxas (1R = a perda no stop). Se a mesma vela toca stop e alvo, conta stop.
- **size**: a menor de três quantidades: perda máxima ÷ risco por unidade, caixa disponível (menos 10% de reserva) e limite por posição (50% do saldo). Respeita stepSize, minQty e minNotional.
  - A taxa da compra sai na moeda comprada, então a venda é um pouco menor. Se o usuário paga taxa em BNB, `fee_in_bnb: true`.
  - A ordem é **OTOCO**: compra limite e, quando executa, a própria Binance coloca alvo (LIMIT_MAKER) e stop (STOP_LOSS) que se cancelam.
- **risk**: perda máxima, ganho:risco mínimo, trades por dia, limite diário (contando o risco das posições abertas), limite semanal, pausa depois de N perdas seguidas, pausa de 30 min depois de uma perda (anti-vingança), tamanho da posição, caixa, clima, simulação e preço atual.
- **place**: exige PASS do risk de hoje para a mesma ordem (impressão digital), o sim registrado há menos de 15 min, e, para dinheiro real, três travas: `"env": "prod"` e `"allow_live": true` na config, `BT_ALLOW_LIVE=1` no ambiente, e `--live` no comando.

## Configuração (bt/config.json)

Copie `${CLAUDE_SKILL_DIR}/config.example.json` para `bt/config.json` e ajuste com o
usuário: `budget`, `max_loss_per_trade`, `daily_loss_limit` etc. O padrão é testnet.

Conta de teste: em https://testnet.binance.vision o usuário entra com GitHub e gera
chaves de teste, que são diferentes das chaves reais. A testnet tem poucos pares em
BRL. Se faltar o par, use `"quote": "USDT"` na config.

Erro 451 = a Binance bloqueia o país de onde a conexão sai (por exemplo, servidores nos
EUA). No computador do usuário, no Brasil, funciona.
