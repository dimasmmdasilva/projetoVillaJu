# Relatório Técnico Final — VillaJu

**Projeto:** VillaJu — Sistema de Reservas e Divulgação
**Disciplina:** Projeto Integrador 4 — Bacharelado em Cibersegurança — PUC-Campinas
**Equipe:** Dimas Marcondes · Eduardo Malta · Gabriel Fuzinatto · Guilherme Leal · Murilo Dias Bartkevicius
**Data:** 23/11/2026 · **Versão descrita:** `main` depois do commit `637bd8c`

---

## Como ler este documento

Este relatório explica **todo o sistema** nas três áreas cobradas pela
disciplina — **Banco de Dados**, **Criptografia** e **Desenvolvimento
Seguro de Software** —, uma parte para cada. Ele foi escrito para estudo:
cada tópico segue a mesma ordem.

1. **O conceito**, em termos gerais — o que a banca espera que se saiba.
2. **Como o VillaJu aplica**, com os números reais.
3. **Onde está**, com o arquivo — para mostrar na tela se perguntarem.
4. Ao fim de cada parte, **perguntas prováveis da banca**, com a resposta.

Os relatórios de análise ficam à parte e são citados aqui:
`docs/relatorio_1_analise_estatica.md` (código sem executar) e
`docs/relatorio_2_analise_dinamica.md` (sistema em funcionamento).

---

## 0. Visão geral do sistema

### 0.1. O que é

O VillaJu é o sistema de reservas de uma pousada de cinco chalés na Praia
da Juréia (Iguape, SP). Tem duas faces:

| Face | Quem usa | O que faz |
|---|---|---|
| **Pública** (sem login) | qualquer visitante | vê os chalés e fotos, consulta disponibilidade (sem preço), lê o regulamento, cria conta com confirmação por e-mail, recupera a senha |
| **Hóspede** (com login) | quem criou conta | pede reserva em quatro passos, informa o Pix, acompanha a reserva, conversa com a pousada, cancela, troca a senha |
| **Equipe** (com login) | recepção e administrador | Painel com o que precisa de ação, confere o Pix, registra reservas, cancela, conversa, relatórios; o administrador também cuida de chalés, tarifas, auditoria e controle anual de pagamentos |

Três ações acontecem **sozinhas**: cancelar o pedido cujo Pix não foi
informado no prazo, concluir a reserva quitada quando a saída chega, e
descartar o cadastro não confirmado.

**O pagamento não passa pelo sistema.** O Pix acontece entre o banco do
hóspede e o da pousada; o sistema registra que a equipe **conferiu** o Pix,
com data e autor. Não há intermediário de pagamento, cobrança automática
nem dado de cartão — o que tira do escopo toda uma classe de riscos.

### 0.2. As regras de negócio

Todas numa constante só, em `app/servicos/regulamento.py`; telas e
regulamento leem dali.

| Regra | Valor |
|---|---|
| Pagamento com sinal | 50%, se faltam 7 dias ou mais para a entrada; senão, integral |
| Prazo para informar o Pix | 90 minutos (pedido pelo site); 24 horas (reserva pela recepção) |
| Restante do sinal | até 23h59 de 2 dias antes da entrada |
| Devolução a pedido do hóspede | tudo com 7 dias ou mais de antecedência; nada com menos |
| Devolução quando a pousada cancela | tudo, sempre |
| Pedidos em aberto por conta | 1 |
| Conclusão | automática: pagamento quitado **e** data de saída alcançada |

### 0.3. Arquitetura

```
 Navegador ──HTTPS (TLS 1.2/1.3)──▶  run.py  (servidor + contexto TLS)
                                       │
                                       ▼
                         app/__init__.py  (fábrica da aplicação:
                          configuração, cabeçalhos de segurança,
                          CSRF, limites de tentativa, erros)
                                       │
            ┌──────────────────────────┼──────────────────────────┐
            ▼                          ▼                          ▼
     app/rotas/  (12 blueprints)   app/seguranca/             app/templates/
     recebe o pedido, confere      autorizacao.py (perfis)    40 modelos Jinja2,
     perfil, chama o serviço       senhas.py (Argon2id)       escape automático,
            │                      cripto.py (AES-GCM, HMAC)  zero JavaScript
            ▼
     app/servicos/  (regras de negócio: pedido, pagamentos, cadastro...)
            │
            ▼
     app/dados/  (repositórios: único lugar com SQL, sempre parametrizado)
            │  pool de 10 conexões, READ COMMITTED
            ▼
     MySQL 8 / InnoDB — banco `villaju`: 11 tabelas, 24 gatilhos,
     conta `villaju_app` com privilégio mínimo
```

| Componente | Tecnologia |
|---|---|
| Linguagem | Python 3.12 |
| Web | Flask 3.1, Jinja2 |
| Banco | MySQL 8, motor InnoDB, `utf8mb4` |
| Driver | mysql-connector-python 26.10 |
| Criptografia | `cryptography` 50.0 (AES-GCM), `argon2-cffi` 25.1 (senhas), `hmac`/`hashlib`/`secrets` (biblioteca padrão) |
| Proteções web | Flask-WTF 1.3 (CSRF), Flask-Limiter 4.1 (limite de tentativas) |
| Testes | pytest 9.1 — **2.618 testes** na última suíte completa |

O projeto tem só **sete dependências de execução**. Cada pacote a mais é
código de terceiros a confiar e a manter atualizado (`pip-audit`: zero
vulnerabilidades conhecidas).

---

# PARTE I — BANCO DE DADOS

## 1.1. Conceitos

- **SGBD relacional:** organiza os dados em tabelas ligadas por chaves. O
  VillaJu usa **MySQL 8** com o motor **InnoDB**, que dá transações,
  chaves estrangeiras e travas por linha.
- **Transação e ACID:** um conjunto de operações que acontece inteiro ou
  não acontece. *Atomicidade* (tudo ou nada), *Consistência* (as regras
  valem antes e depois), *Isolamento* (transações simultâneas não se
  atrapalham), *Durabilidade* (confirmado é gravado de vez).
- **Chave primária / estrangeira:** a primária identifica a linha (`id`);
  a estrangeira aponta para a primária de outra tabela e impede referência
  a algo que não existe.
- **Índice:** estrutura que acelera a busca, como o índice de um livro.
- **Gatilho (trigger):** um trecho de código **guardado no próprio banco**
  que roda automaticamente quando uma linha é inserida, alterada ou
  apagada. Pode **recusar** a operação ou **complementá-la**.
- **Privilégio:** o que cada conta do MySQL pode fazer em cada tabela.
- **Nível de isolamento:** o quanto uma transação enxerga do que outras
  fazem ao mesmo tempo.

## 1.2. Os bancos e as contas

| Banco | Para quê |
|---|---|
| `villaju` | o banco de uso |
| `villaju_teste` | o banco dos testes automatizados, recriado a cada caso; a suíte nunca toca o `villaju` |

Criados por `banco/00_bancos.sql`. Os scripts seguem a ordem:
`00_bancos` → `01_schema` (tabelas) → `02_triggers` (gatilhos) →
`03_usuario_app` (conta da aplicação) → `04_dados_ficticios` (demonstração).

**Três contas, cada uma com o mínimo necessário — o princípio do menor
privilégio:**

| Conta | Pode | Usada por |
|---|---|---|
| `root` | tudo | só a pessoa responsável, para instalar, migrar e fazer backup; a senha não está em arquivo nenhum do projeto |
| `villaju_app` | só o que a aplicação precisa, tabela a tabela (abaixo) | a aplicação em funcionamento |
| `villaju_teste` | tudo, mas **só** no `villaju_teste` | a suíte de testes |

**Privilégios da `villaju_app`** (`banco/03_usuario_app.sql`):

| Tabela | SELECT | INSERT | UPDATE | DELETE | Por quê |
|---|:-:|:-:|:-:|:-:|---|
| `chale`, `tarifa`, `hospede`, `reserva`, `usuario`, `contato`, `token_acesso`, `acompanhante` | ✔ | ✔ | ✔ | ✔ | operação normal |
| `log_auditoria` | ✔ | ✔ | — | — | **só acréscimo**: nem a aplicação invadida apaga o próprio rastro |
| `pagamento` | ✔ | ✔ | — | — | **livro-caixa**: correção é lançamento novo, nunca rasura |
| `mensagem` | ✔ | ✔ | só `lida_pela_equipe` e `lida_pelo_hospede` | — | **privilégio por coluna**: a aplicação marca como lida, mas não reescreve texto, autor nem data |

A conta **não pode** criar, alterar ou apagar tabelas, conceder
privilégios, nem ler outro banco.

*Como os gatilhos gravam a auditoria se a conta não pode alterar o log?*
Gatilhos rodam com o privilégio de quem os **criou** (o `root`, chamado
*DEFINER*), não de quem disparou a operação.

## 1.3. O modelo de dados — 11 tabelas

| Tabela | Guarda | Destaques |
|---|---|---|
| `chale` | os 5 chalés | capacidade, comodidades, ativo |
| `tarifa` | preço da diária por chalé e período | gatilho impede período invertido e valor negativo |
| `hospede` | quem se hospeda | **nome, documento, telefone e e-mail cifrados**; índices cegos para busca |
| `usuario` | as contas de acesso | login, `senha_hash` (Argon2id), perfil, `versao_sessao` |
| `reserva` | as reservas | chalé, hóspede, datas, pessoas, situação, valor, modalidade, prazos do Pix |
| `pagamento` | os lançamentos de Pix conferidos | tipo (sinal, restante, integral, devolução), valor, quem registrou |
| `acompanhante` | quem vai junto | **nome e documento cifrados**; a ordem na lista |
| `mensagem` | a conversa de cada reserva | **texto cifrado**; autor; lida pela equipe / pelo hóspede |
| `contato` | pedidos de contato (formulário desativado desde o Bloco 17) | cifrado |
| `token_acesso` | links de confirmação e de nova senha | só o **resumo SHA-256** do link, tipo, validade |
| `log_auditoria` | quem fez o quê, quando | **nunca o conteúdo** dos campos |

**Relações (chaves estrangeiras) e o que acontece ao apagar:**

| De → para | Ao apagar o "pai" | Por quê |
|---|---|---|
| `reserva` → `chale`, `hospede` | **RESTRICT** (proíbe) | não se apaga chalé ou hóspede com reserva |
| `usuario` → `hospede` | RESTRICT | a conta precisa ser removida antes, de propósito |
| `pagamento`, `acompanhante`, `mensagem` → `reserva` | RESTRICT | o histórico financeiro e a conversa não somem |
| `reserva.criado_por`, `log_auditoria.usuario_id` → `usuario` | **SET NULL** | a conta pode sair; o registro fica, sem autor |
| `token_acesso` → `usuario` | **CASCADE** | apagar a conta apaga os links dela |

**Outras restrições no próprio banco:**

- **ENUM** para valores fechados: `situacao` só pode ser `pendente`,
  `confirmada`, `cancelada` ou `concluida`; `perfil` só `administrador`,
  `recepcionista` ou `hospede`.
- **9 restrições CHECK**, por exemplo: se há `modalidade`, há
  `prazo_pagamento` (e vice-versa); prazo do restante só existe com sinal;
  acompanhante sem documento só se for menor de idade.
- **Unicidade:** login único; e-mail e documento de hóspede únicos —
  garantido sobre o **índice cego** (o banco compara resumos, nunca vê o
  e-mail).
- **Índice de disponibilidade** `(chale_id, data_entrada, data_saida,
  situacao)`: a consulta mais frequente do sistema não varre a tabela.
- **`utf8mb4`**: acentos e emoji gravados corretamente.

## 1.4. Gatilhos (triggers)

### O que são, em detalhe

Um gatilho é um procedimento guardado no banco, ligado a uma tabela e a
um evento:

- **Quando:** `BEFORE` (antes — pode corrigir ou recusar) ou `AFTER`
  (depois — usado para registrar);
- **Em quê:** `INSERT`, `UPDATE` ou `DELETE`;
- **Acesso aos valores:** `NEW` (a linha como vai ficar) e `OLD` (como
  estava);
- **Para recusar:** `SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '...'` —
  a operação inteira é desfeita e a aplicação recebe o erro **1644** com a
  mensagem, que mostra à pessoa.

**Por que regras no banco, se a aplicação já confere?** Defesa em
profundidade. A aplicação confere para dar uma mensagem boa; o banco
confere para que a regra valha **para qualquer caminho**: uma falha na
aplicação, um script esquecido, ou alguém com o Workbench aberto. A regra
mais importante do sistema — **um chalé não tem duas reservas no mesmo
período** — vale mesmo para quem entra como `root`.

### Os 24 gatilhos, em 7 grupos (`banco/02_triggers.sql`)

| Grupo | Gatilhos | O que garantem |
|---|---|---|
| **1. Regra de reserva** | `trg_reserva_antes_inserir`, `trg_reserva_antes_alterar` | saída depois da entrada; chalé existe; pessoas entre 1 e a capacidade; pessoas ≥ titular + acompanhantes; **nenhuma sobreposição** com reserva ativa do mesmo chalé; carimbos de tempo |
| **2. Auditoria de reserva** | `trg_reserva_depois_inserir`, `_depois_alterar`, `_depois_excluir` | registram em `log_auditoria` **o que** aconteceu e por **quem** — nunca os valores |
| **3. Auditoria de hóspede** | `trg_hospede_depois_inserir`, `_depois_alterar`, `_depois_excluir` | idem, para dado pessoal |
| **4. Carimbo de tempo** | `chale`, `tarifa`, `hospede`, `token`, `contato` (antes de inserir/alterar) | `criado_em` e `alterado_em` pelo relógio do banco; `criado_em` imutável; tarifa sem período invertido nem valor negativo; link não nasce vencido |
| **5. Integridade de perfil** | `trg_usuario_antes_inserir`, `_antes_alterar` | conta de hóspede **precisa** estar ligada a um hóspede; conta da equipe **não pode** |
| **6. Livro de pagamentos** | `trg_pagamento_antes_inserir`, `_antes_alterar`, `_antes_excluir` | **pagamento não se altera nem se apaga** — "registre a correção como lançamento novo" |
| **7. Acompanhantes e mensagens** | `trg_acompanhante_antes_inserir`, `_antes_alterar`, `trg_mensagem_antes_alterar` | não passar do número de pessoas; reserva e posição do acompanhante não mudam (fazem parte da etiqueta da cifra, seção 2.3); **mensagem enviada não muda**, só pode ser marcada como lida, e lida não volta a não lida |

### Exemplo comentado: a regra de não-sobreposição

```sql
-- Intervalo SEMIABERTO: ocupa da entrada (inclusive) até a saída (exclusive).
SELECT COUNT(*) INTO v_conflito
FROM reserva
WHERE chale_id = NEW.chale_id
  AND situacao IN ('pendente', 'confirmada')   -- só reserva ativa ocupa
  AND NEW.data_entrada < data_saida
  AND NEW.data_saida  > data_entrada;
IF v_conflito > 0 THEN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'Já existe reserva ativa para este chalé no período informado.';
END IF;
```

Duas reservas se sobrepõem quando **cada uma começa antes de a outra
terminar**. Com a comparação estrita (`<` e `>`), quem sai no dia 10
libera o chalé para quem entra no dia 10 — que é como a pousada opera.
Reservas canceladas e concluídas não ocupam o chalé.

### Como a auditoria sabe quem fez

A cada uso, a aplicação declara ao banco quem está operando:
`SET @app_usuario_id = %s` (`app/dados/conexao.py`). O gatilho de
auditoria lê essa variável de sessão. Antes de devolver a conexão ao pool,
a aplicação a apaga — para a próxima pessoa não herdar a identidade da
anterior. Se a variável apontar para uma conta inexistente, o gatilho
grava sem autor em vez de falhar: **a auditoria nunca impede o trabalho que
observa**.

## 1.5. Transações, travas e concorrência

**Transações.** A aplicação trabalha com `autocommit` desligado: cada
operação de negócio abre uma transação; no fim, **confirma** (`commit`)
ou, se algo falhou, **desfaz** (`rollback`) — `obter_conexao` em
`app/dados/conexao.py`. Reservar e lançar o pagamento, por exemplo,
acontecem juntos ou não acontecem.

**Travas explícitas** (`SELECT ... FOR UPDATE`) onde duas pessoas podem
mexer na mesma coisa ao mesmo tempo:

| Onde | O que trava | Contra o quê |
|---|---|---|
| pagamento | a linha da reserva | dois lançamentos do mesmo Pix |
| conversa | a linha da reserva | duas mensagens com a mesma posição |
| acompanhantes | a linha da reserva | passar do número de pessoas com dois envios simultâneos |
| pedido do hóspede | a linha do hóspede | dois pedidos em aberto pela mesma conta |
| **gatilho de reserva** | **a linha do chalé** | **reserva dupla** (abaixo) |

### O caso real do R2-06: a reserva dupla

Em 23/11, ao revisar os gatilhos para este relatório, um experimento com
**duas conexões ao mesmo tempo** mostrou que duas reservas do mesmo chalé
e período, gravadas simultaneamente, **passavam as duas**. A contagem do
gatilho não via a reserva da outra transação, ainda não confirmada. Uma
depois da outra, a segunda era recusada; ao mesmo tempo, não.

A correção tem duas metades:

1. **A fila:** o gatilho trava a linha do chalé (`FOR UPDATE`) antes de
   contar. Reservas do mesmo chalé passam uma de cada vez; de chalés
   diferentes, não se esperam.
2. **A leitura atualizada:** a aplicação passou a usar o isolamento
   **READ COMMITTED**.

**Os níveis de isolamento, para explicar a segunda metade:**

| Nível | O que cada leitura enxerga |
|---|---|
| `READ UNCOMMITTED` | até o que outras transações ainda não confirmaram (perigoso) |
| **`READ COMMITTED`** | **tudo o que já foi confirmado até aquele instante** — usado pelo VillaJu desde 23/11 (é o padrão do PostgreSQL e do Oracle) |
| `REPEATABLE READ` | um "retrato" do banco tirado na primeira leitura da transação — o padrão do MySQL |
| `SERIALIZABLE` | como se as transações rodassem em fila |

No padrão do MySQL, como a aplicação **lê antes de gravar** (tarifas, o
próprio hóspede), o retrato ficaria velho: depois de esperar a fila do
chalé, a contagem ainda não veria a reserva que passou na frente. Com
`READ COMMITTED`, vê. Há um teste para exatamente esse caso, com duas
conexões reais (`testes/test_reserva_concorrente.py`, 11 testes).

Uma primeira tentativa de correção, com a contagem travada (`FOR SHARE`),
impedia a reserva dupla — mas travava também reservas de outros chalés.
Foi descartada porque o teste de efeito colateral acusou. Detalhes no
Relatório 2, seção 5.5.

## 1.6. Como a aplicação conversa com o banco

- **Repositórios** (`app/dados/*_repo.py`): o **único** lugar com SQL.
  Rotas e serviços nunca escrevem consulta.
- **Consultas sempre parametrizadas:** o comando tem marcadores `%s` e os
  valores vão **separados**, pelo driver. Um texto como
  `' OR '1'='1` vira um valor a comparar, nunca um pedaço de comando. É a
  defesa contra **injeção de SQL**, e é verificada por testes que leem o
  código e reprovam qualquer f-string ou concatenação montando SQL
  (`testes/test_repositorio.py`). O sqlmap confirmou: nenhum dos 10 pontos
  de entrada é injetável.
- **Pool de 10 conexões** reaproveitadas. Se todas estão em uso, o pedido
  **espera até 2 segundos**; se nenhuma se liberar, a pessoa vê "sistema
  ocupado" (503) em vez de um erro — achado R2-03 da análise dinâmica.
- **Tradução de erros** (`app/dados/erros.py`): o erro do banco vira uma
  mensagem compreensível, sem detalhe técnico. Mensagem de gatilho (1644)
  passa à pessoa; duplicidade (1062), referência (1451/1452), impasse
  (1213) e espera de trava (1205) têm texto próprio; o resto vira "Não foi
  possível concluir a operação" — o original vai só para o log.

## 1.7. Backup e restauração

| Item | Como |
|---|---|
| Ferramenta | `mysqldump --single-transaction` — cópia consistente sem parar o sistema |
| Frequência | diária, às 3h, por tarefa agendada do Windows; se o computador estava desligado, roda quando ligar |
| Retenção | as **14** cópias mais recentes |
| Cópia válida | só conta se terminar com `-- Dump completed`; cópia truncada é detectada |
| Senha do backup | em `instance/backup.cnf`, **com permissão só para o dono** (aplicada com `icacls`, cujo caminho é perguntado ao Windows — achados R1-01 e R1-06); a senha nunca aparece na linha de comando |
| Restauração | `scripts/restaurar.py` restaura num banco separado e **confere a contagem de cada tabela**; foi feita e conferida em 21/11 |
| Alerta | o Painel do administrador mostra se o último backup é recente |

**Backup que nunca foi restaurado não é backup** — por isso a restauração
foi testada de verdade, não só documentada.

## 1.8. Mudanças no banco e manutenção

Toda mudança de estrutura segue o mesmo rito: alterar `banco/01` a `03`
com teste; no banco local, **`mysqldump` antes**, `ALTER` por script e
**conferência coluna a coluna** contra o banco de testes. Os scripts com
acento rodam pelo `cmd`, porque o PowerShell 5.1 estraga os acentos.

**A limpeza de 23/11.** Antes de gerar dados novos com o sistema completo,
os dados de uso (contas, hóspedes, reservas, pagamentos, conversas,
auditoria) foram apagados com `TRUNCATE`, mantendo chalés, tarifas e toda
a estrutura (gatilhos e privilégios intactos). Apagar auditoria é o que um
sistema em uso **não** deve permitir; aqui foi decisão consciente sobre
dados de homologação, antes do uso real, com cópia completa guardada.

## 1.9. Perguntas prováveis — Banco de Dados

**"O que é um gatilho e por que usar, se a aplicação já valida?"**
Um procedimento no banco que roda sozinho em inserções, alterações ou
exclusões e pode recusá-las. A aplicação valida para dar mensagem boa; o
gatilho garante a regra para qualquer caminho — inclusive alguém com
acesso direto. É defesa em profundidade.

**"Como o sistema impede duas reservas no mesmo período?"**
Três camadas: a aplicação confere antes; o gatilho recusa no banco
(erro 1644); e, desde o R2-06, a trava na linha do chalé mais o isolamento
`READ COMMITTED` garantem isso mesmo com dois pedidos no mesmo instante —
provado por teste com duas conexões.

**"Por que a aplicação não usa o root?"**
Menor privilégio. Se a aplicação for invadida, o atacante herda só o que
ela pode: não apaga a auditoria, não altera pagamentos, não reescreve
mensagens, não cria tabelas, não lê outros bancos.

**"Como a auditoria não pode ser apagada?"**
A conta da aplicação só tem SELECT e INSERT no log. O gatilho grava com o
privilégio do criador (DEFINER). E o log registra o que aconteceu, nunca
os valores — não vira uma segunda cópia dos dados pessoais.

**"Como o sistema se protege de injeção de SQL?"**
Consultas sempre parametrizadas, só nos repositórios, com testes que
reprovam SQL montado com texto. O sqlmap testou os 10 pontos de entrada
e não achou injeção; os alarmes que deu foram provados falsos (Relatório
2, seção 6).

**"E se o disco queimar?"**
Backup diário, 14 cópias, restauração testada. A recomendação é guardar
cópias fora do computador.

---

# PARTE II — CRIPTOGRAFIA

## 2.1. Conceitos

| Ferramenta | O que faz | Volta ao original? | Exemplo no VillaJu |
|---|---|---|---|
| **Cifra simétrica** | embaralha com uma chave; a mesma chave desembaralha | **sim**, com a chave | dados pessoais (AES-256-GCM) |
| **Hash** | resumo de tamanho fixo; mudar um bit muda tudo | **não** | resumo dos links (SHA-256) |
| **HMAC** | hash com uma chave secreta | não | índice de busca do e-mail e do documento |
| **Hash de senha (KDF)** | hash **lento e caro de propósito**, com sal | não | senhas (Argon2id) |
| **TLS** | cifra o caminho entre navegador e servidor | — | todo o tráfego (HTTPS) |

- **AES** (*Advanced Encryption Standard*): a cifra simétrica padrão
  mundial. **AES-256** usa chave de 256 bits.
- **GCM** (*Galois/Counter Mode*): um **modo de operação** que, além de
  cifrar, **autentica** — produz uma **etiqueta (tag)** que denuncia
  qualquer alteração no texto cifrado. É uma cifra **AEAD**
  (*Authenticated Encryption with Associated Data*).
- **Nonce** ("número usado uma vez"): valor que torna cada cifragem única.
  No GCM, repetir o nonce com a mesma chave é catastrófico; por isso ele é
  sorteado a cada cifragem.
- **AAD** (*dado adicional autenticado*): uma informação que **não é
  cifrada**, mas fica **amarrada** ao texto cifrado — se não for a mesma
  na decifragem, a decifragem falha.
- **Sal:** valor aleatório misturado à senha antes do hash, para que duas
  senhas iguais deem resumos diferentes e tabelas pré-calculadas não
  sirvam.

## 2.2. O mapa: o que é protegido, e como

| O quê | Como | Onde |
|---|---|---|
| Senha das contas | **Argon2id** (hash lento, com sal) | `app/seguranca/senhas.py` |
| Nome, documento, telefone, e-mail do hóspede; acompanhantes; mensagens; contatos | **AES-256-GCM** | `app/seguranca/cripto.py` |
| Busca por e-mail e documento; login do hóspede | **HMAC-SHA256** (índice cego) | `cripto.indice` |
| Links de confirmação e de nova senha | aleatório de 256 bits; banco guarda só o **SHA-256** | `app/dados/token_repo.py` |
| Sessão | cookie **assinado** com a chave secreta (HMAC), `HttpOnly`, `Secure`, `SameSite=Lax`, 8 horas, versão no banco | `app/config.py`, `app/rotas/auth.py` |
| Tráfego | **TLS 1.2/1.3**, só cifras com troca de chave efêmera e autenticadas | `run.py` |
| As próprias chaves e senhas do sistema | arquivo `.env` fora do Git; `backup.cnf` só para o dono | `scripts/gerar_chaves.py` |

## 2.3. AES-256-GCM em detalhe

**As chaves.** `scripts/gerar_chaves.py` gera **três chaves de 256 bits**
com `secrets.token_bytes` (o gerador criptograficamente seguro do sistema
operacional) e as escreve em base64 no `.env`:

| Variável | Para quê |
|---|---|
| `VILLAJU_CHAVE_CIFRAGEM` | cifrar e decifrar os dados pessoais |
| `VILLAJU_CHAVE_INDICE` | o HMAC dos índices de busca |
| `VILLAJU_SECRET_KEY` | assinar o cookie de sessão e os tokens CSRF |

**Uma chave para cada fim**: o comprometimento de uma não entrega as
outras, e cada uma pode ser trocada separadamente. Se uma chave faltar ou
tiver tamanho errado, o sistema **se recusa a subir** — nunca recorre a um
valor padrão, que seria uma chave pública.

**Cifrar** (`cripto.cifrar(texto, aad)`):

1. sorteia um **nonce de 12 bytes** (96 bits) com `secrets.token_bytes` —
   novo a cada cifragem;
2. `AESGCM(chave).encrypt(nonce, texto, aad)` devolve o texto cifrado com
   a **etiqueta de 16 bytes** (128 bits) no fim;
3. o banco guarda três colunas por campo: `<campo>_cif`, `<campo>_nonce`,
   `<campo>_tag` (`VARBINARY`).

**Decifrar** recebe as três partes e a mesma AAD. Se qualquer coisa
mudou — um byte do cifrado, a etiqueta, ou a AAD —, a biblioteca **recusa**
e o sistema mostra "registro não legível" em vez de um dado errado.

**A etiqueta de contexto (AAD) — o detalhe que a banca pode explorar.**
Sem AAD, quem tivesse acesso de escrita ao banco poderia **copiar o bloco
cifrado** do nome de um hóspede para a linha de outro: a decifragem
funcionaria, e o sistema mostraria o dado da pessoa errada sem que nada
falhasse. Com a AAD, o bloco só decifra no lugar de onde veio:

| Tabela | AAD | Impede |
|---|---|---|
| `hospede` | `hospede:<id>` | mover dado entre hóspedes |
| `contato` | `contato:<id>` | mover entre tabelas (o prefixo diferencia `hospede:7` de `contato:7`) |
| `acompanhante` | `acompanhante:<reserva>:<ordem>:<campo>` | mover entre reservas, posições, e trocar nome por documento |
| `mensagem` | `mensagem:<reserva>:<ordem>:<autor>` | **atribuir a mensagem a outra pessoa** — "pôr palavras na boca de alguém" trocando um número |

Por isso os gatilhos do grupo 7 proíbem mudar reserva e ordem do
acompanhante: elas fazem parte da AAD.

**`cripto_versao`**: cada linha cifrada guarda a versão do esquema de
cifragem, para permitir trocar de algoritmo ou de chave no futuro sem
confundir o que foi cifrado como.

**Limitações conhecidas, declaradas:**

- a AAD de `hospede` não inclui o nome do campo (as de acompanhante e
  mensagem incluem): dentro da mesma linha de hóspede, o bloco do nome e o
  do documento poderiam ser trocados. Corrigir exige recifrar todos os
  hóspedes; ficou registrado;
- o campo "Observações" da reserva fica em texto claro; a tela avisa para
  não escrever dado pessoal ali.

## 2.4. Índice cego (HMAC-SHA256)

**O problema:** com o e-mail cifrado, como saber se "fulano@exemplo.com"
já tem conta, ou fazer o login do hóspede pelo e-mail? Cifrar de novo não
ajuda — o nonce aleatório dá um resultado diferente a cada vez.

**A solução:** guardar, ao lado, o **HMAC-SHA256** do valor normalizado
(`email_busca`, `documento_busca`). O mesmo e-mail sempre dá o mesmo HMAC;
o banco compara resumos e garante a unicidade sem ver o e-mail.

- **Por que HMAC e não SHA-256 simples:** um CPF tem cerca de 10¹¹
  possibilidades; com SHA-256 público e rápido, quem tivesse o banco
  testaria todos em pouco tempo. O HMAC exige a chave, que não está no
  banco.
- **Normalização antes do HMAC:** Unicode NFKC (acento composto ou
  pré-combinado viram a mesma coisa), sem espaços nas pontas, minúsculas;
  no documento, só letras e dígitos (`123.456.789-00` = `12345678900`).
  Sem isso, a unicidade seria contornada por quem digitasse diferente.
- **O login do hóspede** é o HMAC do e-mail: a coluna `login` não guarda
  o e-mail em claro.
- **Consequência aceita:** não dá para buscar hóspede por **nome** — o
  nome está cifrado e não tem índice. A busca é por e-mail ou documento
  exatos.

## 2.5. Senhas das contas: Argon2id

**Por que não SHA-256:** hash comum é **rápido** — uma placa de vídeo
testa bilhões de senhas por segundo. Senha precisa de hash **lento e que
consuma memória**, para encarecer o ataque de força bruta.

**Argon2id** venceu a *Password Hashing Competition* (2015) e é a
recomendação atual da OWASP. A variante *id* combina resistência a
ataques por placa de vídeo e por canal lateral. Parâmetros do VillaJu
(`app/seguranca/senhas.py`):

| Parâmetro | Valor | Efeito |
|---|---|---|
| Memória | **256 MiB** por cálculo | ataque em massa precisa de muita memória por tentativa |
| Iterações | 10 | tempo por cálculo |
| Paralelismo | 4 | usa 4 núcleos |
| Sal | 16 bytes, sorteado a cada senha | duas senhas iguais têm resumos diferentes |
| Saída | 32 bytes | — |

O banco guarda a string completa, por exemplo
`$argon2id$v=19$m=262144,t=10,p=4$<sal>$<resumo>`: **os parâmetros vão
junto**. Isso permite **recalibrar**: se os parâmetros forem aumentados, a
senha de quem entra é recalculada no próprio login — o único momento em
que a senha em claro está disponível.

**Outras proteções da senha:**

- tamanho máximo de 1.024 caracteres (uma senha gigante não vira ataque de
  negação de serviço contra o Argon2);
- **mesma mensagem e tempo parecido** para "usuário não existe" e "senha
  errada" — não se descobre quem tem conta;
- **5 tentativas de login por minuto**; a sexta recebe 429.

## 2.6. As senhas e segredos do próprio sistema

| Segredo | Onde fica | Proteção |
|---|---|---|
| As três chaves (cifragem, índice, sessão) | `.env` | gerado por script; **ignorado pelo Git**; uma cópia deve ser guardada fora do computador (perder a chave de cifragem torna os dados ilegíveis para sempre) |
| Senha da conta `villaju_app` do MySQL | `.env` | idem; a conta tem privilégio mínimo |
| Senha do backup | `instance/backup.cnf` | permissão só para o dono do arquivo; nunca na linha de comando |
| Senha de app do Gmail (envio de e-mail) | `.env` | senha **de app**, revogável, que só envia e-mail — nunca a senha da conta |
| Senha do `root` | só na cabeça do responsável | não está em arquivo nenhum |

**Verificações:** o **gitleaks** varreu os 125 commits do histórico (na
data do Relatório 1) e não achou segredo. E um teste próprio do projeto procura texto atribuído a
nomes como `senha`, `chave`, `segredo` — porque o Bandit **não reconhece
nomes em português** (achado R1-05 do Relatório 1).

## 2.7. Links de uso único (tokens)

- Gerados com `secrets.token_urlsafe(32)`: **256 bits** de aleatoriedade —
  impossíveis de adivinhar.
- O banco guarda só o **SHA-256** do link: quem ler a tabela não consegue
  usar link nenhum.
- **Uso único:** consumir o link é uma operação condicional no banco —
  dois cliques simultâneos não usam o mesmo link duas vezes.
- **Prazos:** confirmação de cadastro, 5 minutos por padrão (configurável;
  15 na primeira prova com e-mail real); nova senha, 48 horas. O gatilho
  impede que um link nasça vencido.

## 2.8. A sessão

- O cookie `villaju_sessao` é **assinado** com a `SECRET_KEY` (HMAC): o
  navegador guarda, mas não consegue alterar sem invalidar a assinatura.
- Guarda só o identificador da conta e a **versão da sessão** — nunca
  perfil, nome ou senha.
- `HttpOnly` (script não lê), `Secure` (só HTTPS), `SameSite=Lax` (não vai
  em envios vindos de outro site), vence em **8 horas**.
- **Achado K1 (Relatório 2):** "Sair" apagava o cookie do navegador, mas
  uma **cópia** continuava valendo. Corrigido com
  `usuario.versao_sessao`: sair, trocar ou redefinir a senha incrementam a
  versão, e todo cookie anterior da conta deixa de valer. Na própria
  análise dinâmica, quando o ZAP clicou em "Sair", o cookie que ele usava
  passou a ser recusado em todas as páginas.

## 2.9. TLS (HTTPS)

| Item | VillaJu |
|---|---|
| Versões | **TLS 1.2 e 1.3** apenas (1.0 e 1.1 desligadas) |
| Cifras no TLS 1.2 | `ECDHE+AESGCM` e `ECDHE+CHACHA20`: troca de chave **efêmera** (sigilo futuro — uma chave roubada amanhã não decifra o tráfego de hoje) e cifra autenticada |
| Cifras CBC | **removidas** (achado R2-01; eram vulneráveis em potencial ao LUCKY13) |
| HSTS | o navegador passa a exigir HTTPS nas visitas seguintes |
| Certificado | autoassinado, de homologação (por isso a nota T no testssl); em produção, um certificado de autoridade reconhecida |
| Vulnerabilidades clássicas | Heartbleed, POODLE, BEAST, ROBOT, SWEET32, LOGJAM e outras: **não vulnerável** (testssl) |

## 2.10. Perguntas prováveis — Criptografia

**"Por que AES-GCM e não AES-CBC?"**
GCM cifra **e** autentica: qualquer alteração no cifrado é detectada pela
etiqueta. CBC só cifra — precisaria de um HMAC à parte, e erros nessa
combinação já causaram ataques reais (*padding oracle*).

**"O que acontece se o banco vazar?"**
Quem o tiver vê os dados pessoais como bytes ilegíveis (sem a chave, que
fica no `.env`, fora do banco); senhas como Argon2id (caríssimo de quebrar);
links como SHA-256 (inúteis); e-mail e documento de busca como HMAC (sem a
chave de índice, não se testa por força bruta).

**"E se o servidor inteiro for invadido?"**
Aí a chave também é acessível: a cifragem protege o banco **separado** do
servidor (cópia de backup, acesso só ao MySQL), não um servidor tomado.
Por isso existem as outras camadas — menor privilégio, auditoria, sessão
revogável — e por isso o backup é guardado longe do `.env`.

**"Por que o nonce não pode repetir?"**
No GCM, dois textos cifrados com a mesma chave e o mesmo nonce revelam a
diferença entre os textos e permitem forjar etiquetas. O VillaJu sorteia
96 bits a cada cifragem; a chance de repetir é desprezível.

**"Para que serve a AAD?"**
Amarra o texto cifrado ao seu lugar. Sem ela, mover o bloco do nome de um
hóspede para outro funcionaria sem erro.

**"Por que Argon2id e não SHA-256 com sal?"**
SHA-256 é rápido demais: com sal, ainda se testam bilhões de senhas por
segundo. Argon2id é lento e consome 256 MiB por tentativa, e os
parâmetros ficam guardados com o resumo, permitindo endurecer no futuro.

**"Por que três chaves?"**
Fins diferentes, impactos separados e rotação independente.

---

# PARTE III — DESENVOLVIMENTO SEGURO DE SOFTWARE

## 3.1. Os princípios adotados

| Princípio | Na prática |
|---|---|
| **Defesa em profundidade** | toda regra importante vale em mais de uma camada (aplicação **e** banco; privilégio **e** gatilho; escape **e** política de conteúdo) |
| **Menor privilégio** | conta do banco por tabela e por coluna; perfis na aplicação |
| **Falhar fechado** | sem chave, o sistema não sobe; erro desconhecido vira mensagem genérica; link vencido é recusado |
| **Minimização de dados (LGPD)** | só se pede o necessário; o que é pessoal é cifrado; cadastro não confirmado é descartado |
| **Segurança testada, não afirmada** | cada proteção tem teste, e cada teste foi validado por sabotagem |
| **Superfície pequena** | sem JavaScript, sem recurso de fora, sete dependências |

## 3.2. Back-end

**Flask com fábrica de aplicação** (`create_app` em `app/__init__.py`):
a configuração é lida e conferida no início — falta de variável
obrigatória interrompe a subida com mensagem clara.

**Quatro camadas, cada uma com uma responsabilidade:**

| Camada | Pasta | Faz | Não faz |
|---|---|---|---|
| Rotas | `app/rotas/` (12 blueprints) | recebe o pedido, confere perfil, chama o serviço, escolhe a tela | SQL, regra de negócio |
| Serviços | `app/servicos/` | regras de negócio (pedido, Pix, cadastro, cancelamento...) | HTML, SQL |
| Repositórios | `app/dados/` | o **único** SQL do sistema, parametrizado; cifra e decifra | decidir regra |
| Segurança | `app/seguranca/` | perfis, senhas, criptografia | — |

**Os 12 blueprints:** `publico`, `auth`, `painel`, `hospedes`, `chales`,
`conversa`, `mensagens`, `reservas`, `pagamentos`, `quem_vai`,
`relatorios`, `ajuda`.

## 3.3. Front-end

- **40 modelos Jinja2** e uma folha de estilo (CSS) de cerca de 1.700
  linhas. **Nenhuma linha de JavaScript** — nenhum `<script>` em modelo
  nenhum. Tudo que seria interativo é feito por formulário e navegação: a
  galeria de fotos é um link por foto; o carrossel da página inicial é só
  CSS, com uma caixa "Pausar fotos".
- **Por que sem JavaScript é segurança:** a maior parte dos ataques de
  XSS precisa executar script. Sem script legítimo, a política de conteúdo
  (CSP) pode proibir **todo** script que não venha do próprio site — e um
  script injetado não roda.
- **Escape automático do Jinja2:** tudo o que vem do usuário é exibido
  como texto (`<` vira `&lt;`). O filtro `|safe`, que desligaria o escape,
  não é usado em dado de usuário — há comentários nos modelos dizendo isso,
  e testes que gravam `<script>` e conferem que volta escapado.
- **Nenhum recurso de fora:** fontes, imagens e CSS são servidos pelo
  próprio sistema; um teste reprova qualquer `src` externo.
- **Acessibilidade (WCAG 2.1 AA):** contraste de cada par de cores
  **calculado** por teste (4,5:1 para texto, 3:1 para borda e foco);
  contorno de foco visível; quem pede menos movimento vê as fotos paradas;
  textos alternativos nas imagens; o carrossel pode ser pausado.

## 3.4. Os controles, organizados pelo OWASP Top 10 (2021)

| Risco | Controle no VillaJu | Verificado por |
|---|---|---|
| **A01 Controle de acesso** | `requer_login` e `requer_perfil` em toda rota interna; recurso de outra pessoa → 403/404; o **dono da reserva vai na condição do UPDATE** (não basta esconder o botão) | roteiro de abuso A1–A11, I1–I3: todos bloqueados |
| **A02 Falhas criptográficas** | AES-256-GCM, Argon2id, HMAC, TLS 1.2+ AEAD, HSTS | testssl; testes de cripto |
| **A03 Injeção** | SQL parametrizado só nos repositórios; escape do Jinja2; CSP | sqlmap (10/10 sem injeção); testes de código; X1–X5 |
| **A04 Design inseguro** | regras no banco (gatilhos); limite de tentativas; um pedido em aberto por conta; campos de valor e situação **calculados no servidor**, nunca aceitos do formulário | R3: `valor_total=1,00` enviado à força foi ignorado |
| **A05 Configuração** | `DEBUG` desligado em uso; cabeçalhos de segurança; servidor não anuncia versão; corpo máximo de 2 MB | ZAP; testssl |
| **A06 Componentes vulneráveis** | sete dependências com versão fixa | `pip-audit`: 0 vulnerabilidades |
| **A07 Autenticação** | Argon2id; mensagem e tempo iguais para usuário inexistente e senha errada; 5 tentativas/min; sessão de 8 h; **sessão revogada ao sair** (K1) | roteiro K1–K4, F1 |
| **A08 Integridade** | token CSRF em todo formulário, com conferência do Referer; logout só por POST | roteiro C1–C6 |
| **A09 Registro e monitoramento** | auditoria por gatilho, só acréscimo; log da aplicação **sem dado pessoal** (achado R2-05: o e-mail saiu do log) | testes de log |
| **A10 SSRF** | não se aplica: o servidor não busca endereço informado por usuário | — |

## 3.5. Autenticação e sessão

1. O login recebe a senha, busca a conta e verifica com Argon2id — com a
   **mesma resposta** (401, mesma mensagem) e tempo parecido para conta
   inexistente, senha errada ou conta desativada.
2. Acertou: `session.clear()` **antes** de gravar qualquer coisa — contra
   **fixação de sessão** (descarta o que um atacante tivesse plantado).
3. A sessão guarda só o `usuario_id` e a `versao_sessao`.
4. **Em toda requisição**, `usuario_atual()` relê a conta no banco: se foi
   desativada, apagada, ou se a versão mudou, a sessão é limpa e a pessoa
   volta ao login. Por isso uma mudança de perfil vale **na hora**, e não
   só no próximo login.
5. "Sair" só por **POST com token CSRF** — um site hostil não consegue
   derrubar a sessão de ninguém com um link.

## 3.6. Autorização: o que cada perfil pode

| Ação | Visitante | Hóspede | Recepção | Administrador |
|---|:-:|:-:|:-:|:-:|
| Ver chalés, disponibilidade, regulamento | ✔ | ✔ | ✔ | ✔ |
| Criar conta, recuperar senha | ✔ | — | — | — |
| Pedir reserva, informar Pix, conversar, cancelar a **própria** | — | ✔ | — | — |
| Painel, conferir Pix, registrar reserva, cancelar, conversar | — | — | ✔ | ✔ |
| Ver e corrigir hóspedes | — | — | ✔ | ✔ |
| Chalés e tarifas, editar quem vai, controle anual, **auditoria** | — | — | — | ✔ |

Tentativas de acesso negado vão para a auditoria com o **nome da rota**,
não o endereço — o endereço poderia carregar o número de um registro.

## 3.7. Entrada, saída e erros

- **Entrada:** toda data é convertida e validada (formato ISO, saída depois
  da entrada, máximo de diárias); números viram número; textos têm tamanho
  máximo antes de cifrar. O que não passa volta com mensagem — e **nunca
  chega ao banco** (provado no caso `saida`, Relatório 2).
- **Saída:** escape automático; CSP `default-src 'self'`; `X-Frame-Options:
  DENY` (contra *clickjacking*); `X-Content-Type-Options: nosniff`;
  `Referrer-Policy`; `Permissions-Policy` sem câmera, microfone ou
  localização; isolamento de origem (COOP, CORP, COEP); HSTS.
- **Erros:** páginas próprias para 400, 401, 403, 404, 405, 413, 429, 500 e
  503 — sem rastro técnico; o detalhe vai só para o log.

## 3.8. Registro e auditoria

| Registro | O quê | Proteção |
|---|---|---|
| `log_auditoria` (banco) | pela aplicação: login certo e errado, saída, acesso negado, senha alterada, pedido de reserva, Pix informado, pagamento conferido, cancelamentos (pelo hóspede, pela equipe, por falta de pagamento), devolução, lista de quem vai alterada, mensagem enviada; pelos gatilhos: criação, alteração e exclusão de reserva e de hóspede | só acréscimo; sem valores dos campos |
| Log da aplicação (terminal) | erros e eventos técnicos | **sem nome, documento, e-mail, senha, token ou corpo de e-mail** — garantido por testes; do destinatário de e-mail, só o domínio |

## 3.9. Privacidade e LGPD

- **Minimização:** o cadastro pede só nome, documento, telefone e e-mail;
  o formulário público de contato foi desativado.
- **Proteção:** todo dado pessoal cifrado; busca sem expor o dado (índice
  cego).
- **Descarte:** cadastro não confirmado é apagado quando o prazo vence.
- **Finalidade e transparência:** o regulamento é aceito explicitamente
  em cada pedido, com data registrada.
- **Caminho único:** só a própria pessoa informa os próprios dados (a
  recepção não cria hóspede).

## 3.10. O processo: como a segurança foi verificada

| Etapa | Resultado |
|---|---|
| **Testes automatizados** | **2.618 testes** na última suíte completa, rodada antes de cada entrega de bloco que mexe em banco, criptografia ou autenticação |
| **Sabotagem** | cada teste novo foi validado desfazendo a proteção de propósito e confirmando que ele acusa; testes que não acusavam foram corrigidos |
| **Análise estática** (Relatório 1) | Bandit, Semgrep, pip-audit, gitleaks: 6 achados, todos corrigidos; pontos cegos das ferramentas documentados |
| **Análise dinâmica** (Relatório 2) | testssl, sqlmap, OWASP ZAP e 45 casos de abuso: **7 achados reais, todos corrigidos**, 40 sabotagens; 5 falsos positivos provados falsos no sistema real |
| **Revisão por pares** | roteiro em `docs/revisao_por_pares.md`, com o grupo |
| **Controle de versão** | cerca de 140 commits no Git, cada um pequeno e com descrição; o histórico conta cada decisão |

**A lição de método principal:** as ferramentas erraram nas duas
direções — o alerta mais grave (injeção de SQL) era falso, e o problema
mais sério de integridade (a reserva dupla) não gerou alerta nenhum. Foi
a verificação de cada resultado no sistema real, e os testes escritos
pela equipe, que separaram uma coisa da outra.

## 3.11. Limitações conhecidas e o que fazer em produção

| Limitação | Recomendação |
|---|---|
| Servidor de desenvolvimento do Flask | Waitress ou Gunicorn atrás de um proxy, com número de linhas de execução ≤ pool de conexões |
| Certificado autoassinado | certificado de autoridade reconhecida, com renovação automática |
| AAD de `hospede` sem o nome do campo | recifrar com a etiqueta nova numa migração |
| "Observações" da reserva em texto claro | cifrar, ou retirar o campo |
| Chave de cifragem num só arquivo | cópia guardada fora do computador; em escala maior, um cofre de chaves |
| Sem rotação de chaves automatizada | `cripto_versao` já permite; falta o procedimento |
| Senhas de demonstração publicadas no repositório | trocar antes do uso real |

## 3.12. Perguntas prováveis — Desenvolvimento Seguro

**"Por que não usar JavaScript?"**
Superfície menor: sem script legítimo, a CSP proíbe todo script, e um XSS
que escapasse do escape ainda não executaria. E o sistema não precisa de
interatividade que um formulário não resolva.

**"Como garantem que o hóspede não vê a reserva de outro?"**
Na rota, perfil; no repositório, o **dono vai na condição do SQL**
(`WHERE id = %s AND hospede_id = %s`) — trocar o número no endereço não
acha nada. O roteiro de abuso tentou ver, escrever e cancelar reserva de
outra pessoa, e foi barrado.

**"E se alguém editar o formulário e mandar valor_total = 1?"**
O valor é calculado no servidor a partir das tarifas; campos de valor e
situação enviados no formulário são ignorados. Testado (R3): gravou R$
1.020,00, pendente.

**"O que é CSRF e como se defendem?"**
Um site hostil faz o navegador da vítima enviar um formulário ao VillaJu
com a sessão dela. Defesa: token secreto em todo formulário, conferido no
servidor, mais a conferência do Referer em HTTPS e o cookie `SameSite`.

**"Como sabem que os testes testam alguma coisa?"**
Sabotagem: para cada proteção, a equipe a desfez de propósito e conferiu
que o teste falhou. Vários testes que passavam pelo motivo errado foram
achados assim — inclusive nesta fase final.

**"Qual foi o achado mais sério?"**
Na sessão, o K1: um cookie copiado valia depois de sair. Na integridade, o
R2-06: duas reservas simultâneas do mesmo chalé passavam. Ambos achados
pelos testes escritos pela equipe — nenhuma ferramenta automática os
apontou — e ambos corrigidos, com teste e sabotagem.

---

## Glossário

| Termo | Significado |
|---|---|
| **AAD** | dado adicional autenticado: não é cifrado, mas é amarrado ao cifrado |
| **ACID** | atomicidade, consistência, isolamento, durabilidade — as garantias de uma transação |
| **AEAD** | cifra que autentica além de cifrar (o AES-GCM é uma) |
| **Argon2id** | função de hash de senha lenta e que consome memória |
| **Blueprint** | módulo de rotas do Flask |
| **CSP** | *Content-Security-Policy*: o navegador só carrega o que a política permite |
| **CSRF** | envio forjado de formulário a partir de outro site |
| **DEFINER** | a conta com cujo privilégio um gatilho executa |
| **ECDHE** | troca de chave efêmera por curvas elípticas (sigilo futuro) |
| **Gatilho** | procedimento no banco disparado por INSERT, UPDATE ou DELETE |
| **HMAC** | hash com chave secreta |
| **HSTS** | cabeçalho que obriga o navegador a usar HTTPS |
| **IDOR** | acesso a recurso alheio trocando um identificador |
| **Impasse (deadlock)** | duas transações esperando uma pela outra; o MySQL desfaz uma |
| **Índice cego** | HMAC de um valor cifrado, para buscar sem revelar |
| **Isolamento** | o quanto uma transação enxerga das outras |
| **Nonce** | valor usado uma única vez por cifragem |
| **Pool de conexões** | conexões com o banco reaproveitadas entre pedidos |
| **Sabotagem** | desfazer uma proteção de propósito para provar que o teste acusa |
| **Sal** | valor aleatório misturado à senha antes do hash |
| **SIGNAL** | comando do MySQL que recusa a operação com uma mensagem |
| **TLS** | protocolo que cifra a comunicação (o "S" do HTTPS) |
| **XSS** | injeção de script numa página vista por outra pessoa |
