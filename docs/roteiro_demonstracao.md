# VillaJu — Roteiro da demonstração

Demonstração ao vivo para a banca, em cerca de **15 minutos**. Cada passo
diz **com que conta**, **o que fazer** e **o que deve aparecer** — e, ao
lado, o que o passo prova.

O sistema roda na máquina do apresentador (`https://localhost:5000`),
instalada pelo [guia de instalação](instalacao.md). As contas são as da
massa de demonstração: `admin`, `recepcao` e `joao` (senhas na seção 6
do guia).

---

## Antes de começar (no dia, 30 minutos antes)

- [ ] Banco com a massa de demonstração e os dados cifrados com a chave
      da máquina (guia, seções 5.2 e 5.4). **Nenhum dado real** na tela:
      nome, documento, telefone e chave Pix devem ser fictícios.
- [ ] `VILLAJU_EMAIL_MODO=arquivo`: a demonstração não depende de rede.
- [ ] `python run.py` no ar; certificado já aceito no navegador.
- [ ] Três janelas, cada uma com uma conta: anônima (visitante e novo
      hóspede), `recepcao` e `admin`. Use janelas privadas separadas,
      para as sessões não se misturarem.
- [ ] MySQL Workbench conectado ao `villaju`, com as consultas da seção
      "Workbench" já coladas numa aba.
- [ ] O explorador de arquivos aberto em `instance/emails`.
- [ ] Último backup de hoje (o Painel do `admin` mostra).
- [ ] A **gravação** da demonstração no computador, para o plano B.

---

## O roteiro

| # | Conta | Fazer | Deve aparecer | O que prova |
|---|---|---|---|---|
| 1 | visitante | Abrir `/`. Mostrar o logo, as fotos passando e a caixa "Pausar fotos". Abrir um chalé. | Fotos trocando a cada 4 s; param só com a caixa "Pausar fotos" marcada | Face pública sem login; acessível (WCAG 2.2.2); sem JavaScript |
| 2 | visitante | Em `/disponibilidade`, consultar um período. | "Ocupado nesta data." ou os chalés livres no período — **sem preço e sem dado de ninguém** | A face pública não revela reservas alheias |
| 3 | novo hóspede | Em `/cadastro`, criar uma conta. Abrir o e-mail em `instance/emails` e clicar no link. Entrar. | Conta confirmada; a tela de entrada | O e-mail prova a posse do endereço |
| 4 | novo hóspede | Em `/reservas/pedir`, pedir uma reserva com um acompanhante, revisar e confirmar. | O card com o valor, a chave Pix e o prazo de 90 minutos | Regras do regulamento aplicadas na hora; dado pessoal cifrado ao gravar |
| 5 | novo hóspede | No card, "Fiz o Pix". Depois, mandar uma mensagem à pousada. | "aguardando a conferência da equipe"; a mensagem na conversa | O hóspede declara, a equipe confere |
| 6 | `recepcao` | Em `/pagamentos`, conferir o Pix ("Pagou ... Pix"). Responder a mensagem na reserva. | A reserva vira confirmada; o hóspede vê o aviso de resposta no Painel | O pagamento só vale depois de conferido no extrato |
| 7 | `recepcao` | Em `/reservas/nova`, tentar registrar outra reserva **no mesmo chalé e nas mesmas datas**. | Recusa: "O chalé ... já está reservado de ... a .... Escolha outro período ou outro chalé." | A aplicação bloqueia o conflito, com uma frase que diz o que fazer |
| 8 | Workbench | Rodar a consulta **Conflito direto no banco** (abaixo). | "Já existe reserva ativa para este chalé no período informado." — agora do gatilho | **Momento forte:** a regra mora no banco; nem quem passa por fora da aplicação consegue |
| 9 | Workbench | Rodar **Dados cifrados** (abaixo). | Bytes ilegíveis em nome, documento, telefone, e-mail e no texto das mensagens | Dado pessoal nasce cifrado (AES-256-GCM) |
| 10 | Workbench | Rodar **Auditoria e privilégios** (abaixo). | As ações da demonstração no log, sem nome nem documento; o `villaju_app` sem `UPDATE` nem `DELETE` no log | Rastro completo e que a aplicação não consegue apagar |
| 11 | `recepcao` | Abrir `/auditoria` digitando o endereço. | Acesso negado (403) | Cada perfil só vê o que é seu, na rota — não só no menu |
| 12 | `admin` | Abrir a reserva do passo 4 e "Cancelar pela pousada". | Antes do clique, quanto volta; depois, a devolução em "Precisa de ação"; na reserva, "cancelada — pela pousada", a data e "por admin (administrador)" | Decisões e responsáveis ficam registrados e visíveis |
| 13 | `admin` | Registrar a devolução. Na janela do hóspede, abrir "Minhas reservas". | "Cancelada pela pousada" e "Devolução de R$ ... feita pela pousada" | O hóspede acompanha o próprio dinheiro |
| 14 | `admin` | Abrir `/relatorios` e `/como-usar`; mostrar o aviso de backup no Painel. | Ocupação e reservas do mês; o manual por tarefa; "Último backup: ..." | Operação: relatório, manual e backup diário (RNF06) |

---

## Workbench

Cole as três consultas numa aba antes de começar. Elas usam só leitura,
exceto o `INSERT` do conflito — que o banco recusa.

### Conflito direto no banco (passo 8)

Pega a primeira reserva confirmada e tenta gravar outra igual, por cima:

<!-- sql:conflito -->
```sql
USE villaju;
SELECT chale_id, hospede_id, data_entrada, data_saida
  INTO @chale, @hospede, @entrada, @saida
  FROM reserva WHERE situacao = 'confirmada' ORDER BY id LIMIT 1;
INSERT INTO reserva (chale_id, hospede_id, data_entrada, data_saida, num_pessoas, situacao)
VALUES (@chale, @hospede, @entrada, @saida, 1, 'confirmada');
```

Resposta esperada: `Error Code: 1644. Já existe reserva ativa para este
chalé no período informado.`

### Dados cifrados (passo 9)

<!-- sql:leitura -->
```sql
USE villaju;
SELECT id, nome_cif, documento_cif, email_cif, email_busca FROM hospede ORDER BY id DESC LIMIT 3;
SELECT reserva_id, ordem, nome_cif, documento_cif FROM acompanhante ORDER BY id DESC LIMIT 3;
SELECT reserva_id, ordem, da_equipe, texto_cif FROM mensagem ORDER BY id DESC LIMIT 3;
SELECT id, usuario_id, acao, tabela_afetada, registro_id, data_hora, detalhe FROM log_auditoria ORDER BY id DESC LIMIT 15;
```

### Auditoria e privilégios (passo 10)

A última consulta do bloco acima mostra o log. Depois:

```sql
SHOW GRANTS FOR 'villaju_app'@'localhost';
```

Na lista, `log_auditoria` aparece só com `SELECT, INSERT`.

---

## Se algo der errado

| Situação | O que fazer |
|---|---|
| A aplicação não sobe | Mostrar a gravação; explicar o passo que falhou pelo [guia](instalacao.md), seção 12 |
| O MySQL parou | A página inicial continua de pé, sem a lista de chalés ("Não foi possível carregar..."): mostrar isso é mostrar a degradação planejada. Reiniciar o serviço MySQL80 |
| O e-mail de confirmação não aparece | Conferir `VILLAJU_EMAIL_MODO=arquivo` e a pasta; ou entrar com `joao` e seguir do passo 4 |
| O prazo do Pix venceu durante a fala | É o sistema funcionando: o pedido é cancelado ("o Pix não foi informado no prazo") e o chalé volta a ficar livre. Pedir outra reserva |
| Pergunta sobre restauração | Mostrar a evidência de 21/11 (11 tabelas iguais, 24 gatilhos, decifragem 7/7, 5/5, 7/7) e o comando do guia, seção 9 |
