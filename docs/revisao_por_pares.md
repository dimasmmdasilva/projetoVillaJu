# VillaJu — Roteiro da revisão por pares

Este roteiro é para os integrantes do grupo que **não escreveram** o
código do VillaJu. A revisão por pares é item obrigatório do Relatório 1
(análise estática e revisão de código): as ferramentas automáticas não
pegam tudo, e um olhar de fora completa o que elas veem.

**O que se pede:** ler um módulo, responder às perguntas da lista dele e
devolver o formulário do fim deste documento ao Dimas.

| | |
|---|---|
| Tempo estimado | 40 a 60 minutos por pessoa |
| Onde | no próprio GitHub, pelo navegador — **não precisa instalar nada** |
| Prazo | **até 23/11, 18h** |
| Devolução | o formulário preenchido, por WhatsApp ou e-mail, ao Dimas |

---

## Como fazer

1. Pegue o módulo combinado com o Dimas (A, B, C ou D).
2. Abra os arquivos pelos links da tabela do seu módulo. O GitHub mostra o
   código com números de linha: **anote o arquivo e a linha** de tudo que
   apontar.
3. Responda a cada pergunta da lista do módulo. Para cada uma, uma de três:
   - **Conferido, está correto** — e, em uma linha, onde você viu isso.
   - **Achado** — o que viu, arquivo, linha e a gravidade (abaixo).
   - **Não consegui avaliar** — vale tanto quanto as outras: diz onde o
     código não se explica sozinho.
4. Achou algo que pareça **grave** (um dado pessoal exposto, uma senha no
   código, uma rota sem controle de acesso)? Avise o Dimas na hora, sem
   esperar o formulário.

**Gravidade:**

| Nível | Quando usar |
|---|---|
| **Alta** | alguém de fora consegue ler dado de outra pessoa, entrar sem senha, alterar o que não devia ou derrubar o sistema |
| **Média** | uma defesa existe mas tem brecha, ou depende de algo frágil |
| **Baixa** | boa prática não seguida, sem caminho claro de ataque |
| **Observação** | clareza, nome confuso, comentário desatualizado, sugestão |

**Não é preciso** rodar o sistema, testar ataques nem corrigir nada: só
ler e apontar. "Conferi estes cinco pontos e estão corretos" também é
resultado — e entra no relatório.

---

## Contexto em um minuto

O VillaJu é o sistema de reservas da pousada (Flask + MySQL). Três
perfis: **hóspede**, **recepcionista** e **administrador**. As regras de
segurança que o código promete cumprir:

- **Nenhum texto montado chega a uma consulta SQL**: todo valor do
  usuário vai como parâmetro (`%s`), nunca colado no texto da consulta.
- **Dado pessoal é cifrado** (AES-256-GCM) antes de ir ao banco: nome,
  documento, telefone, e-mail, mensagens.
- **Nenhum nome ou documento vai para o log.**
- **Cada rota confere o perfil** de quem chama, no servidor — esconder o
  link do menu não conta como controle.
- **Formulários têm token CSRF**; senhas são guardadas com Argon2.
- **A regra de não sobrepor reservas mora no banco** (gatilho), não só na
  aplicação.

---

## Módulo A — Segurança e configuração

O núcleo: quem é quem, cifragem, senhas e a configuração de segurança da
aplicação.

| Arquivo | Linhas | Para quê |
|---|---|---|
| [`app/seguranca/autorizacao.py`](../app/seguranca/autorizacao.py) | 157 | quem está logado e que perfil tem |
| [`app/seguranca/cripto.py`](../app/seguranca/cripto.py) | 334 | cifragem AES-256-GCM e índice de busca |
| [`app/seguranca/senhas.py`](../app/seguranca/senhas.py) | 237 | hash de senha com Argon2 |
| [`app/config.py`](../app/config.py) | 350 | leitura das chaves e da configuração |
| [`app/__init__.py`](../app/__init__.py) | 398 | cabeçalhos de segurança (CSP), sessão, erros |

**Perguntas:**

- [ ] A1. Alguma chave, senha ou segredo está escrito no código, em vez de
      vir do ambiente (`.env`)?
- [ ] A2. O `nonce` da cifragem é sorteado a cada uso, ou pode se repetir?
      (Repetir nonce com a mesma chave quebra o AES-GCM.)
- [ ] A3. Se um dado cifrado for adulterado no banco, a decifragem percebe
      e recusa, ou devolve lixo em silêncio?
- [ ] A4. A senha é comparada de forma segura (pela biblioteca de hash), ou
      por `==` em texto?
- [ ] A5. Existe algum caminho em que `requer_perfil` deixe passar quem não
      tem o perfil — por exemplo, um usuário desativado?
- [ ] A6. O cookie de sessão é marcado como `Secure` e `HttpOnly`? A
      política de conteúdo (CSP) permite script de fora?
- [ ] A7. Em produção (`DEBUG=0`), um erro mostra detalhe técnico ao
      usuário?

---

## Módulo B — Acesso ao banco de dados

Onde o código conversa com o MySQL, e o que o banco permite à aplicação.

| Arquivo | Linhas | Para quê |
|---|---|---|
| [`app/dados/conexao.py`](../app/dados/conexao.py) | 193 | conexões e identificação de quem age |
| [`app/dados/erros.py`](../app/dados/erros.py) | 126 | tradução de erros do banco |
| [`app/dados/usuario_repo.py`](../app/dados/usuario_repo.py) | 526 | contas e login |
| [`app/dados/hospede_repo.py`](../app/dados/hospede_repo.py) | 442 | dados pessoais (cifrados) |
| [`app/dados/token_repo.py`](../app/dados/token_repo.py) | 287 | links de confirmação e de senha |
| [`app/dados/auditoria_repo.py`](../app/dados/auditoria_repo.py) | 148 | o registro de auditoria |
| [`banco/03_usuario_app.sql`](../banco/03_usuario_app.sql) | — | o que a aplicação pode fazer no banco |

**Perguntas:**

- [ ] B1. Alguma consulta monta o SQL juntando texto (`+`, f-string,
      `%` fora do `execute`) com valor que veio do usuário?
- [ ] B2. O usuário do banco da aplicação consegue apagar ou alterar o log
      de auditoria? (Leia os `GRANT` do `03_usuario_app.sql`.)
- [ ] B3. Algum dado pessoal (nome, documento, e-mail) é gravado no log ou
      numa mensagem de erro?
- [ ] B4. O link de confirmação e o de troca de senha podem ser usados
      duas vezes, ou depois de vencidos?
- [ ] B5. A busca de hóspede por documento ou e-mail funciona sem guardar
      o documento em texto claro? (Procure os campos `_busca`.)
- [ ] B6. Quando o banco recusa algo, a mensagem que chega à tela revela
      detalhe interno (nome de tabela, de coluna, SQL)?

---

## Módulo C — Regras de negócio e pagamentos

Prazos, devoluções e a regra de não sobrepor reservas — onde um erro vira
dinheiro errado ou reserva dupla.

| Arquivo | Linhas | Para quê |
|---|---|---|
| [`app/servicos/regulamento.py`](../app/servicos/regulamento.py) | 238 | prazos e regras de devolução |
| [`app/servicos/pagamentos.py`](../app/servicos/pagamentos.py) | 440 | conferência de Pix, devoluções, cancelamento pela equipe |
| [`app/dados/pagamento_repo.py`](../app/dados/pagamento_repo.py) | 403 | gravação dos pagamentos |
| [`app/dados/periodo.py`](../app/dados/periodo.py) | 92 | conferência de conflito de datas |
| [`banco/02_triggers.sql`](../banco/02_triggers.sql) | — | gatilhos da tabela `reserva` (início do arquivo) |

**Perguntas:**

- [ ] C1. Existe algum caminho para gravar duas reservas ativas do mesmo
      chalé no mesmo período? (Olhe o gatilho e a aplicação.)
- [ ] C2. O valor de uma reserva ou de um pagamento pode vir do formulário,
      em vez de ser calculado no servidor?
- [ ] C3. A mesma devolução pode ser registrada duas vezes (dois cliques
      seguidos)?
- [ ] C4. A regra de devolução (7 dias antes da entrada; pela pousada,
      sempre) está escrita num lugar só, ou repetida em vários?
- [ ] C5. Um hóspede consegue confirmar a própria reserva sem a equipe
      conferir o Pix?
- [ ] C6. Datas: entrada depois da saída, ou datas no passado, são
      recusadas?

---

## Módulo D — Rotas e telas

A porta de entrada: o que cada endereço aceita e quem pode usá-lo.

| Arquivo | Linhas | Para quê |
|---|---|---|
| [`app/rotas/auth.py`](../app/rotas/auth.py) | 324 | login, sair, troca e recuperação de senha |
| [`app/rotas/reservas.py`](../app/rotas/reservas.py) | 1012 | reservas — foque nas funções com `@requer_perfil` e `request.form` |
| [`app/rotas/conversa.py`](../app/rotas/conversa.py) | 95 | mensagens entre hóspede e pousada |
| [`app/rotas/publico.py`](../app/rotas/publico.py) | 655 | páginas públicas e cadastro — foque em `cadastro` |
| [`app/templates/`](../app/templates/) | — | as telas: confira 2 ou 3 formulários |

**Perguntas:**

- [ ] D1. Alguma rota da equipe funciona sem `@requer_perfil`?
- [ ] D2. Um hóspede consegue ver ou alterar a reserva de outro hóspede
      trocando o número no endereço (`/reservas/15` → `/reservas/14`)?
- [ ] D3. Todo formulário que altera algo é `POST` e leva `csrf_token`?
      Alguma alteração acontece por `GET`?
- [ ] D4. Algum template usa `|safe` ou `Markup` em texto que veio do
      usuário (risco de XSS)?
- [ ] D5. O login responde igual para "usuário não existe" e "senha
      errada"? (Respostas diferentes permitem descobrir contas.)
- [ ] D6. Depois de um login, um redirecionamento pode levar a um site de
      fora (parâmetro `next` ou parecido)?

---

## O que ficou de fora, de propósito

Para caber no tempo, a revisão não cobre: os relatórios gerenciais, a
página "Como usar", o backup (`scripts/`), os testes (`testes/`) e o
CSS. Esses arquivos passam pelas ferramentas automáticas (Bandit,
Semgrep, pip-audit, gitleaks), cujo resultado também vai para o
Relatório 1.

---

## Formulário de devolução

Copie o bloco abaixo, preencha e mande ao Dimas.

```
REVISÃO POR PARES — VillaJu
Revisor(a):
Módulo: (A, B, C ou D)
Data:
Tempo gasto:

PERGUNTAS
(uma linha por pergunta: número — Conferido / Achado / Não consegui avaliar — comentário)
A1 —
A2 —
...

ACHADOS
(um bloco por achado)
- Arquivo e linha:
  O que vi:
  Gravidade: (Alta / Média / Baixa / Observação)
  Sugestão:

OUTRAS OBSERVAÇÕES
(opcional)
```

Cada resposta vira uma linha no Relatório 1: quem revisou, o que apontou e
o que foi feito — corrigido, com o commit da correção, ou justificado por
escrito.
