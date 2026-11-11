# binance-trader

Skill do Claude para **day trade de cripto na Binance**, em reais (BTCBRL, ETHBRL, SOLBRL…),
com freios de verdade: nenhuma ordem sai sem o seu "sim", o padrão é a **testnet**
(dinheiro de mentira), e antes de arriscar um centavo a máquina do tempo mostra se o
setup tem vantagem **depois das taxas**.

Python puro (3.8+), sem nada para instalar. Dados públicos da Binance, sem chave.

## Instalar

No Claude Code, copie a pasta da skill:

```bash
cp -r binance-trader ~/.claude/skills/
```

Ou só para um projeto: copie `binance-trader/` para `.claude/skills/` dentro do projeto.
Depois é só pedir, por exemplo: "/binance-trader tem algum trade bom agora?"

## O que ela faz

| comando | o que faz |
|---|---|
| `regime` | Clima do mercado pelo BTC diário: risco-ligado, neutro ou risco-desligado |
| `scan` | Procura 3 setups (rompimento, pullback, repique) em todos os pares em BRL com liquidez |
| `plan` | Entrada, stop e alvo, arredondados ao tick, com ganho:risco líquido de taxas |
| `sim` | Máquina do tempo: 10.000 replays + o setup no histórico real + walk-forward |
| `size` | Quantidade pela sua perda máxima e monta a ordem **OTOCO** (entrada + stop + alvo) |
| `risk` | PASS/BLOCK: perda máxima, limite diário/semanal, pausa depois de perdas, anti-vingança, clima |
| `ticket` | O boleto em português, os parâmetros exatos e o comando equivalente no `binance-cli` oficial |
| `approve` / `place` | Registra o seu "sim" e envia, só se tudo bater |
| `status` / `cancel` / `close` | Saldos e ordens abertas, cancelar, sair a mercado |
| `journal` | Diário: resultado do dia, semana e mês, e CSV para o contador |

## Exemplo real (08/11/2026, dados ao vivo)

```
scan  -> SOLBRL PULLBACK: tocou a MME20 e fechou acima, IFR 52
plan  -> entrada R$ 503,40, stop R$ 500,30, alvo R$ 509,50 (2R bruto, 1,24R líquido)
sim   -> 10.000 replays: alvo 31,6 %, stop 68,2 %, média -0,29R
         histórico: 39 sinais, 7 alvo, 27 stop, média -0,44R
         SEM VANTAGEM: depois das taxas, o resultado médio é negativo. Melhor não entrar.
```

A ferramenta serve justamente para isso: dizer **não** na maioria das vezes.

## Bot do Telegram (aprovar pelo celular)

`binance-trader/scripts/telegram_bot.py` roda a mesma linha de montagem sozinho a cada vela
de 15 min. Quando um trade passa em tudo, te manda o boleto com **✅ Aprovar** e
**❌ Recusar**. Ao aprovar, ele confere o risco de novo com o preço do momento e envia a
ordem com stop e alvo para a Binance. Depois te avisa quando a compra executar e quando
bater o alvo ou o stop. Se a compra não executar em 60 min, ele cancela. Se o prazo vencer,
pergunta se você quer sair.

```bash
python3 binance-trader/scripts/telegram_bot.py setup   # token do bot e chaves (digitados escondidos)
python3 binance-trader/scripts/telegram_bot.py check   # confere tudo sem operar
python3 binance-trader/scripts/telegram_bot.py run     # liga
```

Na primeira vez, o terminal mostra um código. Envie `/start CODIGO` para o bot: só esse
chat passa a poder aprovar. Comandos: `/status`, `/scan`, `/clima`, `/diario`, `/pausar`, `/retomar`.

Ele precisa rodar num aparelho fora dos EUA, porque a Binance bloqueia IPs americanos.
Por exemplo: o próprio celular Android (Termux), um PC em casa, ou um servidor em São Paulo.
Os segredos ficam em `~/.binance-trader/secrets.env`, com permissão 600. A config padrão
do bot (`config.bot.example.json`) é testnet, em USDT.

## Segurança

- **Três portões por ordem**: o `risk` aprovou essa ordem exata hoje, você digitou "sim"
  para ela há menos de 15 min, e (só para dinheiro real) três travas: `"env": "prod"` e
  `"allow_live": true` na config, `BT_ALLOW_LIVE=1` no ambiente, e `--live` no comando.
- **Chaves só em variáveis de ambiente** (`BINANCE_API_KEY`, `BINANCE_SECRET_KEY`), nunca
  no chat nem em arquivo. Crie a chave **sem permissão de saque**. Se puder, restrinja
  por IP.
- O stop já nasce com a ordem: na OTOCO, a própria Binance coloca stop e alvo quando a
  compra executa.
- **Testnet**: entre em https://testnet.binance.vision com o GitHub e gere chaves de
  teste. A testnet tem poucos pares em BRL; se faltar, use `"quote": "USDT"`.

## Testes

```bash
python3 -m unittest discover -s tests -v
```

São 40 testes offline (29 da skill e 11 do bot, com Telegram e Binance falsos), com dados reais da Binance salvos em `tests/fixtures`. Cobrem:
- a assinatura HMAC, conferida com o exemplo da documentação da Binance;
- o envio para uma Binance simulada;
- as três travas da conta real;
- a expiração do "sim";
- o circuit breaker;
- os arredondamentos de quantidade e preço.

## Letras miúdas

**Não é recomendação de investimento.** É software que lê dados públicos e faz contas.
A maioria de quem faz day trade perde dinheiro. Cripto pode cair 20% num dia.

- A simulação não é previsão: hacks, listagens e notícias fazem coisas que os últimos 30 dias nunca fizeram.
- O stop é uma ordem, não uma garantia: num tombo rápido ele pode executar pior.
- Imposto de renda sobre cripto tem regras próprias (limite mensal de vendas, apuração,
  declaração). O `journal csv` ajuda, mas confira a regra vigente com um contador.

## Créditos

Ideias e estrutura adaptadas de projetos MIT:
- [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill): a linha de montagem, a máquina do tempo e o "nada sai sem o seu sim".
- [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills): circuit breaker, pausa anti-vingança e clima do mercado cripto.
- [AnalyticAce/binance-mcp-server](https://github.com/AnalyticAce/binance-mcp-server) e [danishashko/binance-mcp-server](https://github.com/danishashko/binance-mcp-server): a API pública sem chave e as ferramentas de conta.
- [binance/binance-skills-hub](https://github.com/binance/binance-skills-hub): os nomes das variáveis de chave, as regras de segurança e o comando `binance-cli` equivalente.

MIT.
