# Documentação Técnica — Etapa 1: Banco de Dados e Triggers

**Projeto VillaJu — Sistema de Reservas e Divulgação**

| | |
|---|---|
| **Instituição** | Pontifícia Universidade Católica de Campinas — Escola Politécnica |
| **Curso** | Bacharelado em Cibersegurança (21853) — Módulo 4 |
| **Disciplina** | Projeto Integrador — Cibersegurança em Programação Segura |
| **Professor orientador** | Prof. Dr. Felix Carvalho Rodrigues |
| **Integrantes** | Dimas Marcondes · Eduardo Malta · Gabriel Fuzinatto · Guilherme Leal · Murilo Dias Bartkevicius |
| **Repositório** | https://github.com/dimasmmdasilva/projetoVillaJu |
| **Escopo deste documento** | Etapa 1 — Banco de dados e triggers (26/10/2026) |
| **Entrega** | Entrega 2 — Desenvolvimento e programação segura |

> **Nota sobre a referência ao plano.** A solicitação deste documento menciona
> um arquivo `CLAUDE.md`. Esse arquivo não existe no repositório. A
> especificação do projeto está em `docs/contexto.md`, excluído do controle
> de versão por decisão do grupo. Todas as referências a seções neste
> documento apontam para `docs/contexto.md`.

---

## Sumário executivo

A Etapa 1 implementa o modelo de dados do VillaJu e, sobretudo, as triggers
que impõem a regra de não-sobreposição de reservas **no nível do banco de
dados**. É o argumento central do projeto na área de Banco de Dados.

Foram criadas 7 tabelas, 17 triggers e 1 usuário de banco com privilégio
mínimo, acompanhados de uma massa de dados fictícios e de 271 novos testes
automatizados. A suíte passou de 145 para **416 testes**, todos aprovados.

O critério de conclusão da etapa — um `INSERT` conflitante executado
diretamente no MySQL, sem passar pela aplicação, sendo rejeitado — foi
verificado e a saída literal está transcrita na seção 4.2.

A etapa estava planejada para 26 a 28/10 e foi concluída em **26/10**, dois
dias antes.

---

# 1. O QUE FOI IMPLEMENTADO

## 1.1. Visão geral

A etapa entrega quatro artefatos executáveis e um documento:

| Artefato | Caminho | Conteúdo |
|---|---|---|
| Script de bancos | `banco/00_bancos.sql` | Cria `villaju` e `villaju_teste` e o usuário de testes |
| Schema | `banco/01_schema.sql` | As 7 tabelas, chaves, restrições e índices |
| Triggers | `banco/02_triggers.sql` | 17 triggers em 5 grupos |
| Usuário da aplicação | `banco/03_usuario_app.sql` | `villaju_app` com privilégio mínimo |
| Massa de dados | `banco/04_dados_ficticios.sql` | 5 chalés, 10 tarifas, 5 hóspedes, 3 usuários, 7 reservas, 4 contatos |
| Diagrama ER | `docs/modelo_entidade_relacionamento.md` | Modelo em Mermaid, versionável |

A propriedade central entregue é esta: **não existe caminho pelo qual uma
reserva conflitante chegue à tabela**. Nem pela aplicação, nem por SQL
direto, nem por operação no MySQL Workbench.

### Decisão estrutural dos scripts

Os scripts `01`, `02`, `03` e `04` **não contêm `CREATE DATABASE` nem
`USE`**. O banco em que os objetos nascem é determinado pela conexão que
executa o script.

Isso permite que a suíte automatizada construa o banco de testes a partir
dos mesmos arquivos que constroem o banco real. Testar uma cópia do schema
não provaria nada: a cópia poderia divergir do original exatamente no ponto
que contém o defeito.

## 1.2. Modelo de dados

Banco: **MySQL Community Server 8.0.43**. Todas as tabelas em `InnoDB`, com
charset `utf8mb4` e collation `utf8mb4_unicode_ci`, verificados na
`information_schema` após a criação.

### 1.2.1. `chale`

| Coluna | Tipo | Nulo | Observação |
|---|---|---|---|
| `id` | `INT UNSIGNED` | não | PK, AUTO_INCREMENT |
| `nome` | `VARCHAR(50)` | não | UNIQUE (`uq_chale_nome`) |
| `capacidade` | `TINYINT UNSIGNED` | não | Máximo de pessoas |
| `configuracao_camas` | `VARCHAR(255)` | sim | |
| `comodidades` | `TEXT` | sim | |
| `descricao` | `TEXT` | sim | Usado na vitrine pública |
| `ativo` | `BOOLEAN` | não | DEFAULT TRUE |
| `criado_em` | `DATETIME` | não | Preenchido por trigger |
| `alterado_em` | `DATETIME` | não | Preenchido por trigger |

### 1.2.2. `hospede`

Todos os campos pessoais são binários, destinados a receber o resultado da
cifragem AES-256-GCM implementada na Etapa 2.

| Coluna | Tipo | Nulo | Observação |
|---|---|---|---|
| `id` | `INT UNSIGNED` | não | PK |
| `nome_cif` | `VARBINARY(512)` | não | Texto cifrado |
| `nome_nonce` | `VARBINARY(12)` | não | 96 bits |
| `nome_tag` | `VARBINARY(16)` | não | 128 bits |
| `documento_cif` | `VARBINARY(256)` | não | |
| `documento_nonce` | `VARBINARY(12)` | não | |
| `documento_tag` | `VARBINARY(16)` | não | |
| `telefone_cif` | `VARBINARY(256)` | sim | |
| `telefone_nonce` | `VARBINARY(12)` | sim | |
| `telefone_tag` | `VARBINARY(16)` | sim | |
| `email_cif` | `VARBINARY(512)` | sim | |
| `email_nonce` | `VARBINARY(12)` | sim | |
| `email_tag` | `VARBINARY(16)` | sim | |
| `email_busca` | `VARBINARY(32)` | sim | UNIQUE — HMAC-SHA256 |
| `documento_busca` | `VARBINARY(32)` | sim | UNIQUE — HMAC-SHA256 |
| `cripto_versao` | `TINYINT UNSIGNED` | não | DEFAULT 1 |
| `criado_em` | `DATETIME` | não | |
| `alterado_em` | `DATETIME` | não | |

### 1.2.3. `usuario`

| Coluna | Tipo | Nulo | Observação |
|---|---|---|---|
| `id` | `INT UNSIGNED` | não | PK |
| `login` | `VARCHAR(50)` | não | UNIQUE (`uq_usuario_login`) |
| `nome` | `VARCHAR(100)` | não | |
| `senha_hash` | `VARCHAR(255)` | não | String codificada do Argon2id |
| `perfil` | `ENUM` | não | `administrador`, `recepcionista`, `hospede` |
| `hospede_id` | `INT UNSIGNED` | sim | FK → `hospede.id` |
| `ativo` | `BOOLEAN` | não | DEFAULT TRUE |
| `ultimo_acesso` | `DATETIME` | sim | |
| `criado_em` | `DATETIME` | não | |
| `alterado_em` | `DATETIME` | não | |

### 1.2.4. `tarifa`

| Coluna | Tipo | Nulo | Observação |
|---|---|---|---|
| `id` | `INT UNSIGNED` | não | PK |
| `chale_id` | `INT UNSIGNED` | não | FK → `chale.id` |
| `data_inicio` | `DATE` | não | |
| `data_fim` | `DATE` | não | |
| `valor_diaria` | `DECIMAL(10,2)` | não | |
| `descricao` | `VARCHAR(100)` | sim | |
| `criado_em` | `DATETIME` | não | |
| `alterado_em` | `DATETIME` | não | |

### 1.2.5. `reserva`

| Coluna | Tipo | Nulo | Observação |
|---|---|---|---|
| `id` | `INT UNSIGNED` | não | PK |
| `chale_id` | `INT UNSIGNED` | não | FK → `chale.id` |
| `hospede_id` | `INT UNSIGNED` | não | FK → `hospede.id` |
| `data_entrada` | `DATE` | não | Inclusive |
| `data_saida` | `DATE` | não | Exclusive |
| `num_pessoas` | `TINYINT UNSIGNED` | não | Limitado pela capacidade |
| `situacao` | `ENUM` | não | `pendente`, `confirmada`, `cancelada`, `concluida` |
| `valor_total` | `DECIMAL(10,2)` | sim | |
| `observacoes` | `TEXT` | sim | Não cifrada |
| `criado_por` | `INT UNSIGNED` | sim | FK → `usuario.id` |
| `criado_em` | `DATETIME` | não | |
| `alterado_em` | `DATETIME` | não | |

### 1.2.6. `log_auditoria`

| Coluna | Tipo | Nulo | Observação |
|---|---|---|---|
| `id` | `BIGINT UNSIGNED` | não | PK |
| `usuario_id` | `INT UNSIGNED` | sim | FK → `usuario.id` |
| `acao` | `VARCHAR(30)` | não | |
| `tabela_afetada` | `VARCHAR(50)` | sim | |
| `registro_id` | `INT UNSIGNED` | sim | |
| `data_hora` | `DATETIME` | não | |
| `detalhe` | `VARCHAR(255)` | sim | Descrição genérica |

### 1.2.7. `contato`

| Coluna | Tipo | Nulo | Observação |
|---|---|---|---|
| `id` | `INT UNSIGNED` | não | PK |
| `nome` | `VARCHAR(100)` | não | Não cifrado |
| `email` | `VARCHAR(150)` | não | Não cifrado |
| `telefone` | `VARCHAR(30)` | sim | |
| `mensagem` | `TEXT` | sim | |
| `chale_interesse_id` | `INT UNSIGNED` | sim | FK → `chale.id` |
| `data_entrada_desejada` | `DATE` | sim | |
| `data_saida_desejada` | `DATE` | sim | |
| `origem` | `VARCHAR(50)` | sim | |
| `ip_origem` | `VARBINARY(16)` | sim | |
| `situacao` | `ENUM` | não | `novo`, `em_atendimento`, `respondido`, `descartado` |
| `criado_em` | `DATETIME` | não | |

### 1.2.8. Chaves estrangeiras como criadas

Saída da `information_schema` do banco `villaju`:

| Tabela | Coluna | Referencia | ON DELETE | ON UPDATE |
|---|---|---|---|---|
| `contato` | `chale_interesse_id` | `chale` | SET NULL | CASCADE |
| `log_auditoria` | `usuario_id` | `usuario` | SET NULL | CASCADE |
| `reserva` | `chale_id` | `chale` | RESTRICT | CASCADE |
| `reserva` | `hospede_id` | `hospede` | RESTRICT | CASCADE |
| `reserva` | `criado_por` | `usuario` | SET NULL | CASCADE |
| `tarifa` | `chale_id` | `chale` | RESTRICT | CASCADE |
| `usuario` | `hospede_id` | `hospede` | RESTRICT | CASCADE |

### 1.2.9. Índices como criados

Saída da `information_schema`:

| Tabela | Índice | Colunas | Único |
|---|---|---|---|
| `chale` | `PRIMARY` | `id` | sim |
| `chale` | `uq_chale_nome` | `nome` | sim |
| `contato` | `PRIMARY` | `id` | sim |
| `contato` | `fk_contato_chale` | `chale_interesse_id` | não |
| `contato` | `idx_contato_ip` | `ip_origem`, `criado_em` | não |
| `contato` | `idx_contato_situacao` | `situacao`, `criado_em` | não |
| `hospede` | `PRIMARY` | `id` | sim |
| `hospede` | `uq_hospede_documento_busca` | `documento_busca` | sim |
| `hospede` | `uq_hospede_email_busca` | `email_busca` | sim |
| `log_auditoria` | `PRIMARY` | `id` | sim |
| `log_auditoria` | `idx_log_data_hora` | `data_hora` | não |
| `log_auditoria` | `idx_log_usuario` | `usuario_id`, `data_hora` | não |
| `reserva` | `PRIMARY` | `id` | sim |
| `reserva` | `idx_reserva_disponibilidade` | `chale_id`, `data_entrada`, `data_saida`, `situacao` | não |
| `reserva` | `fk_reserva_hospede` | `hospede_id` | não |
| `reserva` | `fk_reserva_usuario` | `criado_por` | não |
| `tarifa` | `PRIMARY` | `id` | sim |
| `tarifa` | `idx_tarifa_periodo` | `chale_id`, `data_inicio`, `data_fim` | não |
| `usuario` | `PRIMARY` | `id` | sim |
| `usuario` | `uq_usuario_login` | `login` | sim |
| `usuario` | `fk_usuario_hospede` | `hospede_id` | não |

Os índices com prefixo `fk_` são criados automaticamente pelo InnoDB para
sustentar as chaves estrangeiras; não foram declarados no script.

### 1.2.10. Divergências em relação à seção 5.1 de `docs/contexto.md`

| # | Divergência | Razão |
|---|---|---|
| 1 | Chaves primárias e estrangeiras são `INT UNSIGNED` / `BIGINT UNSIGNED`; a especificação diz apenas `INT` / `BIGINT` | Identificador negativo não tem significado neste modelo. `UNSIGNED` dobra o alcance (de 2,1 para 4,3 bilhões) e permite ao banco rejeitar um valor negativo em vez de aceitá-lo silenciosamente |
| 2 | Acrescentado `idx_tarifa_periodo` em `tarifa` | O cálculo de valor total da Etapa 3 busca a tarifa vigente por chalé e data a cada diária; sem índice, cada busca varre a tabela |
| 3 | Acrescentados `idx_log_data_hora` e `idx_log_usuario` em `log_auditoria` | Esta é a tabela que mais cresce. As duas consultas previstas — "o que aconteceu neste período" e "o que este usuário fez" — ficariam sem suporte |
| 4 | Acrescentados `idx_contato_situacao` e `idx_contato_ip` em `contato` | O primeiro sustenta a fila de atendimento; o segundo sustenta a limitação de taxa por origem exigida pelo RNF05 |
| 5 | Restrições de unicidade e chaves estrangeiras receberam nomes explícitos (`uq_`, `fk_`) | Sem nome explícito o MySQL gera identificadores como `chale_ibfk_1`, que não dizem nada numa mensagem de erro |

As divergências 2 a 4 são acréscimos, não alterações: nenhum índice previsto
foi removido ou modificado. Nenhuma coluna, tipo ou restrição da
especificação foi alterada.

## 1.3. Triggers

17 triggers, organizadas em 5 grupos. Listagem obtida da `information_schema`
do banco `villaju`:

| Tabela | Momento | Evento | Nome |
|---|---|---|---|
| `chale` | BEFORE | INSERT | `trg_chale_antes_inserir` |
| `chale` | BEFORE | UPDATE | `trg_chale_antes_alterar` |
| `contato` | BEFORE | INSERT | `trg_contato_antes_inserir` |
| `hospede` | BEFORE | INSERT | `trg_hospede_antes_inserir` |
| `hospede` | BEFORE | UPDATE | `trg_hospede_antes_alterar` |
| `hospede` | AFTER | INSERT | `trg_hospede_depois_inserir` |
| `hospede` | AFTER | UPDATE | `trg_hospede_depois_alterar` |
| `hospede` | AFTER | DELETE | `trg_hospede_depois_excluir` |
| `reserva` | BEFORE | INSERT | `trg_reserva_antes_inserir` |
| `reserva` | BEFORE | UPDATE | `trg_reserva_antes_alterar` |
| `reserva` | AFTER | INSERT | `trg_reserva_depois_inserir` |
| `reserva` | AFTER | UPDATE | `trg_reserva_depois_alterar` |
| `reserva` | AFTER | DELETE | `trg_reserva_depois_excluir` |
| `tarifa` | BEFORE | INSERT | `trg_tarifa_antes_inserir` |
| `tarifa` | BEFORE | UPDATE | `trg_tarifa_antes_alterar` |
| `usuario` | BEFORE | INSERT | `trg_usuario_antes_inserir` |
| `usuario` | BEFORE | UPDATE | `trg_usuario_antes_alterar` |

### 1.3.1. Grupo 1 — Regra de reserva

**`trg_reserva_antes_inserir`** · BEFORE INSERT em `reserva`

Valida quatro condições e, ao final, preenche o carimbo de tempo.

| Validação | Mensagem do `SIGNAL` |
|---|---|
| `data_saida` posterior a `data_entrada` | `A data de saída deve ser posterior à data de entrada.` |
| Chalé existe | `Chalé inexistente.` |
| Ao menos uma pessoa | `A reserva precisa ter ao menos uma pessoa.` |
| Dentro da capacidade | `Número de pessoas acima da capacidade do chalé.` |
| Sem sobreposição | `Já existe reserva ativa para este chalé no período informado.` |

SQL real da verificação de sobreposição, transcrito de
`banco/02_triggers.sql`:

```sql
        -- Não há cláusula excluindo a própria linha aqui: no INSERT a linha
        -- ainda não existe na tabela, logo não pode conflitar consigo mesma.
        SELECT COUNT(*) INTO v_conflito
        FROM reserva
        WHERE chale_id = NEW.chale_id
          AND situacao IN ('pendente', 'confirmada')
          AND NEW.data_entrada < data_saida
          AND NEW.data_saida > data_entrada;

        IF v_conflito > 0 THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Já existe reserva ativa para este chalé no período informado.';
        END IF;
```

A verificação só roda quando `NEW.situacao IN ('pendente','confirmada')` —
registrar uma reserva já cancelada não é impedido por conflito.

**`trg_reserva_antes_alterar`** · BEFORE UPDATE em `reserva`

Mesmas validações, com duas diferenças. A primeira é a cláusula que exclui a
própria linha da consulta de conflito:

```sql
        SELECT COUNT(*) INTO v_conflito
        FROM reserva
        WHERE chale_id = NEW.chale_id
          AND situacao IN ('pendente', 'confirmada')
          AND id <> COALESCE(NEW.id, 0)
          AND NEW.data_entrada < data_saida
          AND NEW.data_saida > data_entrada;

        IF v_conflito > 0 THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Já existe reserva ativa para este chalé no período informado.';
        END IF;
```

Sem `id <> COALESCE(NEW.id, 0)`, a reserva que está sendo alterada seria
encontrada pela própria consulta e conflitaria consigo mesma — qualquer
alteração, inclusive uma que não toca nas datas, seria rejeitada. Este é o
risco registrado no plano e coberto pelo caso de teste 11.

A segunda diferença é a preservação de `criado_em`:

```sql
    SET NEW.criado_em = OLD.criado_em;
    SET NEW.alterado_em = NOW();
```

### 1.3.2. Grupo 2 — Auditoria de reserva

`trg_reserva_depois_inserir`, `trg_reserva_depois_alterar`,
`trg_reserva_depois_excluir` · AFTER INSERT/UPDATE/DELETE.

Gravam em `log_auditoria` a ação, a tabela, o identificador do registro e a
data. **Nenhum valor de campo é copiado.** A exceção é a mudança de situação
no UPDATE, registrada no campo `detalhe`:

```sql
    IF NEW.situacao <> OLD.situacao THEN
        SET v_detalhe = CONCAT('Situação alterada de ', OLD.situacao,
                               ' para ', NEW.situacao);
    END IF;
```

A situação é categoria fechada do `ENUM`, não dado pessoal.

Todas as seis triggers de auditoria resolvem o autor da operação assim:

```sql
    SET v_usuario = NULLIF(@app_usuario_id, 0);
    IF v_usuario IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM usuario WHERE id = v_usuario) THEN
        SET v_usuario = NULL;
    END IF;
```

A verificação de existência impede que uma variável de sessão apontando para
usuário inexistente produza violação de chave estrangeira e aborte a
operação de negócio.

### 1.3.3. Grupo 3 — Auditoria de hóspede

`trg_hospede_depois_inserir`, `trg_hospede_depois_alterar`,
`trg_hospede_depois_excluir` · AFTER INSERT/UPDATE/DELETE.

Mesma mecânica, com uma regra adicional: **jamais copiar para o log o
conteúdo das colunas `_cif`, `_nonce`, `_tag` ou dos campos `_busca`**. Os
valores gravados em `detalhe` são as constantes `'Hóspede cadastrado'`,
`'Hóspede alterado'` e `'Hóspede excluído'`.

### 1.3.4. Grupo 4 — Carimbo de tempo

`trg_chale_antes_inserir`, `trg_chale_antes_alterar`,
`trg_tarifa_antes_inserir`, `trg_tarifa_antes_alterar`,
`trg_hospede_antes_inserir`, `trg_hospede_antes_alterar`,
`trg_contato_antes_inserir`.

Preenchem `criado_em` e `alterado_em`. Em todas as alterações,
`criado_em` é preservado de `OLD`.

As triggers de `tarifa` validam adicionalmente:

| Validação | Mensagem do `SIGNAL` |
|---|---|
| `data_fim` não anterior a `data_inicio` | `A data final da tarifa não pode ser anterior à inicial.` |
| Valor não negativo | `O valor da diária não pode ser negativo.` |

`contato` tem apenas `trg_contato_antes_inserir` porque a tabela não possui
coluna `alterado_em`.

### 1.3.5. Grupo 5 — Integridade de perfil

`trg_usuario_antes_inserir`, `trg_usuario_antes_alterar` · BEFORE
INSERT/UPDATE em `usuario`.

| Validação | Mensagem do `SIGNAL` |
|---|---|
| Perfil `hospede` exige `hospede_id` | `Usuário com perfil hospede precisa estar vinculado a um hóspede.` |
| Demais perfis não podem ter `hospede_id` | `Apenas usuário com perfil hospede pode ter hospede_id.` |

## 1.4. Usuário de banco da aplicação

A aplicação conecta como `villaju_app@localhost`, **não como `root`**. Saída
literal de `SHOW GRANTS` após a execução de `banco/03_usuario_app.sql` no
banco `villaju`:

```
Grants for villaju_app@localhost
GRANT USAGE ON *.* TO `villaju_app`@`localhost`
GRANT SELECT, INSERT, UPDATE, DELETE ON `villaju`.`chale` TO `villaju_app`@`localhost`
GRANT SELECT, INSERT, UPDATE, DELETE ON `villaju`.`contato` TO `villaju_app`@`localhost`
GRANT SELECT, INSERT, UPDATE, DELETE ON `villaju`.`hospede` TO `villaju_app`@`localhost`
GRANT SELECT, INSERT ON `villaju`.`log_auditoria` TO `villaju_app`@`localhost`
GRANT SELECT, INSERT, UPDATE, DELETE ON `villaju`.`reserva` TO `villaju_app`@`localhost`
GRANT SELECT, INSERT, UPDATE, DELETE ON `villaju`.`tarifa` TO `villaju_app`@`localhost`
GRANT SELECT, INSERT, UPDATE, DELETE ON `villaju`.`usuario` TO `villaju_app`@`localhost`
```

`USAGE ON *.*` significa **nenhum privilégio**: é o registro de que a conta
existe e pode conectar. Toda conta do MySQL tem essa linha.

### Privilégios concedidos

| Alvo | Privilégios |
|---|---|
| `chale`, `tarifa`, `hospede`, `reserva`, `usuario`, `contato` | SELECT, INSERT, UPDATE, DELETE |
| `log_auditoria` | SELECT, INSERT |

### Privilégios negados, verificados em execução

Os testes de `testes/test_privilegios.py` conectam como `villaju_app` e
confirmam a recusa:

| Operação tentada | Resultado |
|---|---|
| `UPDATE log_auditoria` | recusada, erro 1142 |
| `DELETE FROM log_auditoria` | recusada, erro 1142 |
| `TRUNCATE TABLE log_auditoria` | recusada, erro 1142 |
| `CREATE TABLE` | recusada, erro 1142 |
| `DROP TABLE` | recusada, erro 1142 |
| `ALTER TABLE` | recusada, erro 1142 |
| `CREATE INDEX` | recusada, erro 1142 |
| `CREATE TRIGGER` | recusada |
| `DROP TRIGGER` | recusada |
| `CREATE USER` | recusada |
| `GRANT` | recusada |
| `SELECT ... FROM mysql.user` | recusada |
| `SET GLOBAL` | recusada |
| `SELECT` em `performance_schema.events_statements_history` | recusada, erro 1142 |

O privilégio é concedido **tabela a tabela**, não com `ON villaju.*`. É essa
diferença que torna `log_auditoria` append-only do ponto de vista da
aplicação.

### Como a trigger grava no log sem a aplicação ter permissão

As triggers executam com o privilégio do `DEFINER` — o `root` que as criou —
e não do usuário que dispara a operação. Verificado pelo teste
`test_trigger_grava_no_log_apesar_da_restricao`: a aplicação insere uma
reserva e o registro de auditoria correspondente aparece.

## 1.5. Massa de dados fictícios

Saída literal da conferência ao final de `banco/04_dados_ficticios.sql`
executado no banco `villaju`:

```
tabela	registros
chale	5
tarifa	10
hospede	5
usuario	3
reserva	7
contato	4
log_auditoria	12
```

As 12 linhas de auditoria são geradas pelas triggers durante a própria
carga: 5 inserções de hóspede e 7 de reserva.

### Cenários cobertos

| Chalé | Cenário | Para quê |
|---|---|---|
| Embaré | Duas reservas encostadas — saída dia 20, entrada dia 20 | Só passam por causa do intervalo semiaberto |
| Camburi | Reserva de 10 a 18/12, atravessando a virada de temporada em 15/12 | Cálculo de valor total da Etapa 3 |
| Asturias | Uma reserva cancelada e outra confirmada no mesmo período | Cancelada não ocupa |
| Boracéia | Reserva `pendente` | Ciclo de vida da situação |
| Dunas | Reserva `concluida`, de julho de 2026 | Histórico de temporada passada |

As quatro situações do `ENUM` de `reserva` e as quatro de `contato` estão
representadas. Há um hóspede sem telefone e sem e-mail, para exercitar os
campos opcionais.

O script é **idempotente**: foi executado duas vezes consecutivas e produziu
os mesmos números.

### Duas ressalvas documentadas no próprio arquivo

**Os dados de hóspede não são ciphertext real.** As colunas `_cif`, `_nonce`
e `_tag` recebem `RANDOM_BYTES`, não o resultado de uma cifragem
AES-256-GCM. Gerar ciphertext verdadeiro exigiria a chave do `.env`, que não
entra em arquivo versionado, e o módulo de criptografia só nasce na Etapa 2.
Esses registros serão regerados pela aplicação a partir de lá.

**As senhas de demonstração são públicas.** Os hashes são Argon2id reais,
com os parâmetros da seção 6.1 (`m=65536,t=3,p=4`), e as senhas em claro
estão documentadas no cabeçalho do script:

| Login | Senha | Perfil |
|---|---|---|
| `admin` | `Villa@Admin2026` | administrador |
| `recepcao` | `Villa@Recep2026` | recepcionista |
| `joao` | `Villa@Hospede2026` | hospede |

O teste `test_hash_armazenado_verifica_a_senha_documentada` confirma que
cada hash confere com a senha documentada.

## 1.6. Diagrama entidade-relacionamento

**Situação: parcial.**

| Formato | Caminho | Situação |
|---|---|---|
| Mermaid, versionável | `docs/modelo_entidade_relacionamento.md` | **Entregue** |
| Imagem exportada do MySQL Workbench | — | **Pendente** |

O arquivo em Mermaid contém o diagrama completo das 7 tabelas com todas as
colunas, a tabela de relacionamentos com cardinalidade e regra de exclusão,
a tabela de índices e as decisões de modelagem.

A exportação em imagem do MySQL Workbench **não foi gerada**. Ela depende de
operação em interface gráfica — *Database → Reverse Engineer*, seleção do
schema `villaju`, e *File → Export → Export as PNG*. O passo a passo está no
fim do arquivo em Mermaid. Responsável: Dimas Marcondes.

---

# 2. DECISÕES DE PROJETO

## 2.1. Scripts SQL não selecionam banco

**Decisão.** Nenhum dos scripts `01` a `04` contém `CREATE DATABASE` ou
`USE`. O banco é determinado pela conexão que os executa.

**Justificativa.** A suíte automatizada precisa construir um banco de testes
idêntico ao real. Se os scripts fixassem o nome do banco, seria necessário
manter uma segunda cópia para os testes — e duas cópias de um arquivo de
schema divergem com o tempo. Testar uma cópia divergente do schema real não
prova nada sobre o schema real.

**Consequência aceita.** Quem executa os scripts precisa informar o banco na
linha de comando. O cabeçalho de cada arquivo traz o comando completo.

## 2.2. Banco de testes separado, com usuário próprio

**Decisão.** Existem dois bancos, `villaju` e `villaju_teste`, e dois
usuários além do `root`. O usuário `villaju_teste` tem privilégio total
sobre o banco de testes e nenhum sobre qualquer outro.

**Justificativa.** A suíte apaga e recria todas as tabelas a cada execução.
Fazer isso no banco real destruiria dados a cada `pytest`. O isolamento por
privilégio, e não apenas por nome de banco, garante que mesmo um teste mal
escrito — com um `DROP TABLE` equivocado — não alcance dado de produção.

**Verificação em execução.** Conectado como `villaju_teste`:

```
Bancos visíveis: information_schema, performance_schema, villaju_teste
USE villaju -> bloqueado, erro 1044
CREATE e DROP em villaju_teste -> OK
```

**Alternativa descartada.** Rodar os testes como `root` seria mais simples
de montar e indefensável diante da banca num projeto cujo tema é segurança.

## 2.3. Comportamento das chaves estrangeiras ao apagar

**Decisão.** `RESTRICT` onde apagar o pai destruiria histórico de negócio;
`SET NULL` onde o filho deve sobreviver ao pai.

**Justificativa, caso a caso.**

| Relação | Regra | Razão |
|---|---|---|
| `reserva` → `chale` | RESTRICT | Apagar um chalé não pode fazer sumir as reservas dele |
| `reserva` → `hospede` | RESTRICT | Idem para o hóspede |
| `tarifa` → `chale` | RESTRICT | Preserva o histórico de preços praticados |
| `usuario` → `hospede` | RESTRICT | A conta precisa ser removida antes, deliberadamente |
| `reserva` → `usuario` | SET NULL | Desligar um recepcionista não pode apagar as reservas que ele registrou |
| `log_auditoria` → `usuario` | SET NULL | **A auditoria sobrevive à exclusão do usuário** |
| `contato` → `chale` | SET NULL | O pedido continua válido sem o chalé de interesse |

A escolha em `log_auditoria` é a mais importante das sete. Com `CASCADE`,
bastaria apagar uma conta para fazer sumir o rastro de tudo o que ela fez —
exatamente o que alguém faria para encobrir uma ação indevida.

**Esta decisão não estava especificada** em `docs/contexto.md`, que define
as chaves estrangeiras sem indicar o comportamento ao apagar.

## 2.4. Carimbo de tempo por trigger, não por `DEFAULT`

**Decisão.** `criado_em` e `alterado_em` são `NOT NULL` sem valor padrão,
preenchidos por trigger.

**Justificativa.** A seção 5.2 de `docs/contexto.md` admite
`DEFAULT CURRENT_TIMESTAMP` como alternativa aceitável, mas opta pela
trigger explícita por duas razões: o requisito acadêmico pede uso de
triggers, e a trigger deixa o comportamento visível no script em vez de
escondido na declaração da coluna.

**Decisão acrescentada durante a execução:** em todas as alterações,
`criado_em` é preservado de `OLD`. Isso torna a data de criação imutável —
nem uma alteração deliberada consegue reescrever quando o registro nasceu.
Não estava na especificação.

**Consequência aceita.** Entre a Etapa 1 e a Etapa 3, qualquer `INSERT`
manual precisa confiar na trigger. Nos testes do schema, escritos antes das
triggers existirem, as datas são informadas explicitamente.

## 2.5. `log_bin_trust_function_creators` em vez de `SUPER`

**Hipótese original.** Conceder ao usuário de testes apenas
`ALL PRIVILEGES` sobre o banco dele bastaria para criar triggers.

**O que foi testado.** A primeira execução de `banco/02_triggers.sql` como
`villaju_teste` falhou:

```
02_triggers.sql: falha no comando 2 de 34
  início: CREATE TRIGGER trg_reserva_antes_inserir
  erro:   1419 (HY000): You do not have the SUPER privilege and binary
          logging is enabled (you *might* want to use the less safe
          log_bin_trust_function_creators variable)
```

Diagnóstico obtido consultando o servidor:

| Variável | Valor |
|---|---|
| `log_bin` | 1 |
| `log_bin_trust_function_creators` | 0 |
| `binlog_format` | ROW |

**Decisão.** Ligar `log_bin_trust_function_creators` com `SET PERSIST`, em
vez de conceder `SUPER`.

**Justificativa.** `SUPER` é privilégio global que permite encerrar conexões
de terceiros, alterar qualquer variável do servidor e ignorar restrições de
somente-leitura. A variável é incomparavelmente mais estreita: afirma apenas
que quem cria trigger ou função é confiável para não gerar eventos inseguros
no log binário.

No VillaJu esse risco é nulo por duas razões verificadas: o `binlog_format`
é `ROW`, que registra as linhas resultantes e não o comando que as produziu,
e o projeto não usa replicação nem recuperação ponto-a-ponto.

`SET PERSIST` grava em `mysqld-auto.cnf` e sobrevive ao reinício, ao
contrário de `SET GLOBAL`.

**Consequência aceita.** É uma alteração de configuração global do servidor,
não restrita ao projeto. Está registrada em `banco/00_bancos.sql` com a
justificativa, de modo que cada integrante a aplique conscientemente.

## 2.6. Senhas geradas por script, nunca escolhidas

**Decisão.** As senhas dos usuários de banco são geradas por
`scripts/gerar_chaves.py`, com `secrets.token_urlsafe(24)` — 192 bits.

**Justificativa.** Senha pensada por pessoa tem padrão, e padrão é o que o
ataque por dicionário explora. Usar a mesma fonte de entropia das chaves
criptográficas torna a resposta consistente: todo segredo do projeto vem do
mesmo gerador.

O alfabeto de `token_urlsafe` é `A-Z a-z 0-9 - _`. Nenhum desses caracteres
exige escape dentro de uma string SQL nem confunde o leitor de `.env`, o que
elimina uma classe inteira de erro na criação do usuário.

**Esta decisão foi tomada durante a execução**, por solicitação do grupo. A
proposta inicial era que a senha fosse escolhida manualmente.

## 2.7. Senha dos scripts versionados protegida por teste

**Decisão.** `banco/00_bancos.sql` e `banco/03_usuario_app.sql` são
versionados contendo o placeholder `COLE_AQUI_A_SENHA_...`, e um teste
automatizado falha se uma senha real permanecer neles.

**Justificativa.** Os dois scripts precisam de senha para executar. O risco
concreto é alguém colar a senha, rodar o script e commitar sem desfazer a
edição. O teste varre o padrão `IDENTIFIED BY '...'` procurando valor que se
pareça com senha gerada.

**Consequência aceita.** O fluxo exige dois passos manuais — colar e
restaurar. A alternativa seria ler a senha de variável de ambiente dentro do
SQL, o que o MySQL não suporta de forma portável.

## 2.8. Auditoria não pode derrubar a operação que observa

**Decisão.** As seis triggers de auditoria verificam se o usuário informado
em `@app_usuario_id` existe antes de gravar, e usam `NULL` quando não
existe.

**Justificativa.** Sem essa verificação, uma variável de sessão apontando
para usuário inexistente produziria violação de chave estrangeira dentro da
trigger, abortando a transação inteira. Uma reserva legítima seria perdida
por causa de um defeito no registro de auditoria.

**Consequência aceita.** A verificação custa uma consulta adicional por
operação auditada, e mascara o sintoma de uma aplicação que informa
identificador inválido. O teste
`test_usuario_inexistente_na_sessao_nao_derruba_a_operacao` documenta o
comportamento.

## 2.9. Validações acrescentadas além da especificação

**Decisão.** Três validações não previstas em `docs/contexto.md` foram
implementadas.

| Validação | Onde | Razão |
|---|---|---|
| `num_pessoas >= 1` | `reserva` | Reserva sem nenhuma pessoa não é reserva |
| Coerência de datas e valor não negativo | `tarifa` | Mesmo raciocínio aplicado à reserva; uma tarifa que termina antes de começar torna o cálculo da Etapa 3 indefinido |
| Integridade de perfil de usuário | `usuario` | `hospede_id` preenchido num administrador faria a tela "minhas reservas" devolver dados de outra pessoa |

A terceira é a mais relevante em segurança: é uma quebra de controle de
acesso que nasceria de um dado inconsistente, não de um defeito de código.

## 2.10. Leitor de scripts SQL que interpreta `DELIMITER`

**Decisão.** O `conftest.py` da suíte implementa um separador de comandos
que reconhece a instrução `DELIMITER`.

**Justificativa.** `DELIMITER` é comando do cliente `mysql`, não do
servidor, e o driver `mysql-connector-python` não o reconhece. Sem
interpretá-lo, o corpo de cada trigger seria partido no primeiro ponto e
vírgula interno, e nenhum dos pedaços seria SQL válido.

Um teste unitário — `test_leitor_respeita_delimiter` — cobre o separador sem
depender do banco.

## 2.11. Separação entre fixtures de leitura e de escrita

**Hipótese original.** Todo teste de banco receberia um banco limpo.

**O que foi medido.** Com 133 testes de estrutura recriando as 7 tabelas a
cada caso, a suíte de schema levava **30,11 segundos**.

**Decisão.** Testes que apenas consultam a `information_schema` passaram a
usar uma fixture de escopo por arquivo; testes que gravam, apagam ou
disparam trigger continuam recebendo banco limpo a cada caso.

**Resultado medido.** A mesma suíte de 133 testes passou a levar **3,98
segundos** — redução de 7,5 vezes.

---

# 3. PREVISTO VERSUS ENTREGUE

## 3.1. Tarefas previstas na Etapa 1 de `docs/contexto.md`

| # | Tarefa prevista | Situação | Observação |
|---|---|---|---|
| 1 | Escrever `banco/01_schema.sql` com as 7 tabelas, tipos exatos, chaves estrangeiras, restrições de unicidade e índice de disponibilidade | **Concluída** | 5 divergências documentadas na seção 1.2.10, todas acréscimos |
| 2 | Escrever `banco/02_triggers.sql` com as 5 triggers usando `SIGNAL SQLSTATE '45000'` | **Concluída** | 17 triggers em 5 grupos |
| 3 | Implementar a leitura de `@app_usuario_id` nas triggers de auditoria | **Concluída** | Com verificação de existência do usuário |
| 4 | Escrever `banco/03_usuario_app.sql` com privilégio mínimo | **Concluída** | Privilégio tabela a tabela |
| 5 | Escrever `banco/04_dados_ficticios.sql` | **Concluída** | 5 chalés, 10 tarifas, 5 hóspedes, 3 usuários, 7 reservas, 4 contatos |
| 6 | Documentar o diagrama ER no MySQL Workbench e exportar para `docs/` | **Parcial** | Diagrama em Mermaid entregue; exportação em imagem do Workbench pendente |
| 7 | Escrever `testes/test_triggers.py` com os casos críticos | **Concluída** | 38 testes; os 13 obrigatórios cobertos |

## 3.2. O que foi além do planejado

| Entrega adicional | Razão |
|---|---|
| `banco/00_bancos.sql` | O plano não previa script de criação dos bancos nem do usuário de testes |
| `testes/conftest.py` com leitor de `DELIMITER` | Sem ele, nenhum teste de trigger seria possível |
| `testes/test_schema.py` — 133 testes | O plano previa testes de trigger, não de estrutura |
| `testes/test_privilegios.py` — 33 testes | O plano não previa verificar os privilégios em execução |
| `testes/test_dados_ficticios.py` — 33 testes | O plano não previa verificar a massa de dados |
| Geração de senha em `scripts/gerar_chaves.py` | Solicitação do grupo durante a execução |
| `docs/modelo_entidade_relacionamento.md` | O plano pedia diagrama do Workbench; a versão em texto o complementa e evolui com o schema |

## 3.3. O que mudou em relação ao plano, e por quê

| Mudança | Razão |
|---|---|
| Período da etapa: previsto 24 a 28/10, realizado em 26/10 | Decisão do grupo de iniciar em 26/10, e conclusão antecipada |
| Chaves primárias `UNSIGNED` | Seção 1.2.10, divergência 1 |
| Cinco índices acrescentados | Seção 1.2.10, divergências 2 a 4 |
| Três validações acrescentadas nas triggers | Seção 2.9 |
| Alteração de configuração global do MySQL | Seção 2.5 |

## 3.4. Riscos que se materializaram

### Risco previsto no plano

> "A trigger de `UPDATE` dar falso positivo ao comparar a reserva consigo
> mesma. Mitigado pela cláusula `id <> COALESCE(NEW.id, 0)`. Testar
> explicitamente."

**Não se materializou.** A cláusula foi implementada desde a primeira versão
e os casos de teste 10 e 11 a cobrem. A sabotagem que a removeu fez três
testes falharem, confirmando que a proteção é efetiva (seção 4.3).

### Riscos não previstos que se materializaram

**1. Erro 1419 ao criar triggers.** Descrito na seção 2.5. Tratamento:
`log_bin_trust_function_creators` com justificativa documentada.

**2. `Unread result found` ao carregar scripts com `SELECT`.** Os scripts
`03` e `04` terminam com `SHOW GRANTS` e `SELECT` de conferência. O driver
guarda essas linhas e recusa a consulta seguinte enquanto não forem lidas.
A falha apareceu como 31 erros na suíte de dados fictícios. Tratamento: a
função `aplicar_script` do `conftest.py` passou a consumir resultados
pendentes após cada comando.

**3. Teste com expectativa incorreta sobre `performance_schema`.** Um teste
exigia que o banco `performance_schema` fosse invisível à aplicação. A
investigação mostrou que o MySQL 8.0 o expõe a toda conta conectada,
independentemente dos `GRANT` do projeto, mas que seu conteúdo sensível está
bloqueado. Tratamento: o teste foi reescrito para verificar o acesso ao
conteúdo — histórico de instruções, contas e threads — em vez do nome do
banco.

---

# 4. CASOS DE TESTE E RESULTADOS

## 4.1. Os 13 casos obrigatórios

Cenário base de todos os casos, salvo indicação: chalé Embaré com capacidade
4, uma reserva confirmada de 10 a 15/12/2026.

| ID | Cenário | Entrada | Esperado | Obtido |
|---|---|---|---|---|
| 1 | Reserva que não conflita com nada | 05 a 10/01/2027 | grava | **grava** — 2 reservas na tabela |
| 2 | Reserva idêntica a uma existente | 10 a 15/12/2026 | bloqueada | **bloqueada** — erro 1644, "Já existe reserva ativa..." |
| 3 | Reserva que começa no meio de outra | 12 a 18/12/2026 | bloqueada | **bloqueada** — erro 1644 |
| 4 | Reserva que engloba outra por completo | 05 a 20/12/2026 | bloqueada | **bloqueada** — erro 1644 |
| 5 | Reserva que começa no dia de saída de outra | 15 a 20/12/2026 | **grava** | **grava** — 2 reservas na tabela |
| 6 | Reserva que termina no dia de entrada de outra | 05 a 10/12/2026 | **grava** | **grava** — 2 reservas na tabela |
| 7 | Reserva conflitante com outra já cancelada | 10 a 15/12/2026, após cancelar a existente | **grava** | **grava** — 2 reservas na tabela |
| 8 | `num_pessoas` acima da capacidade | 5 pessoas, chalé para 4 | bloqueada | **bloqueada** — erro 1644, "Número de pessoas acima da capacidade do chalé." |
| 9 | `data_saida` anterior ou igual a `data_entrada` | 10/03 a 05/03 e 10/03 a 10/03 | bloqueada | **bloqueada** — erro 1644, "A data de saída deve ser posterior à data de entrada." |
| 10 | `UPDATE` que move uma reserva para cima de outra | mover para 11 a 14/12 | bloqueado | **bloqueado** — erro 1644 |
| 11 | `UPDATE` que altera apenas a observação | texto novo em `observacoes` | passa | **passa** — valor lido de volta confere |
| 12 | Operação em `reserva` e em `hospede` gera linha em `log_auditoria` | inserir ambos | gera | **gera** — 1 linha por operação, ações `INSERT`, `UPDATE`, `DELETE` na ordem |
| 13 | Nenhuma linha de `log_auditoria` contém conteúdo de campo cifrado | hóspede com marcador reconhecível nos campos cifrados | nenhum vestígio | **nenhum vestígio** — varredura de todas as colunas do log |

### Os dois casos contraintuitivos

**Caso 5 — intervalo semiaberto.** A reserva existente sai no dia 15; a nova
entra no dia 15. O sistema **aceita**.

Verificação executada diretamente no MySQL, com a reserva de 20 a 27/12 já
existente no Embaré:

```
INSERT INTO reserva (chale_id, hospede_id, data_entrada, data_saida, num_pessoas, situacao)
VALUES (1, 3, '2026-12-27', '2026-12-30', 2, 'confirmada');

+------------------+-----------+
| linhas_inseridas | id_gerado |
+------------------+-----------+
|                1 |         8 |
+------------------+-----------+
```

Na hospedaria, o hóspede sai pela manhã e o próximo entra à tarde. Se a
comparação usasse `>=` em vez de `>`, o chalé ficaria bloqueado um dia
inteiro e o sistema recusaria receita legítima.

**Caso 7 — conflito com reserva cancelada.** O sistema **aceita**.

A primeira tentativa de demonstrar este caso foi mal construída e está
registrada aqui por honestidade metodológica: usou-se o chalé Asturias, que
na massa de dados tem **duas** reservas no mesmo período — uma cancelada e
uma confirmada. A inserção foi corretamente rejeitada, mas por conflito com
a confirmada, não com a cancelada.

A verificação correta isolou o cenário no chalé Dunas, em período livre:

```
-- Preparo: reserva CANCELADA em período livre
INSERT INTO reserva (chale_id, hospede_id, data_entrada, data_saida, num_pessoas, situacao)
VALUES (5, 1, '2027-09-10', '2027-09-15', 2, 'cancelada');
-- id gerado: 9

-- Nova reserva CONFIRMADA no mesmo período
INSERT INTO reserva (chale_id, hospede_id, data_entrada, data_saida, num_pessoas, situacao)
VALUES (5, 2, '2027-09-10', '2027-09-15', 4, 'confirmada');

+------------------+-----------+
| linhas_inseridas | id_gerado |
+------------------+-----------+
|                1 |        10 |
+------------------+-----------+

-- Estado final
+----+--------------+------------+------------+
| id | data_entrada | data_saida | situacao   |
+----+--------------+------------+------------+
| 10 | 2027-09-10   | 2027-09-15 | confirmada |
|  9 | 2027-09-10   | 2027-09-15 | cancelada  |
+----+--------------+------------+------------+
```

As reservas 8, 9 e 10 criadas nestas demonstrações foram removidas pela
recarga de `banco/04_dados_ficticios.sql`, que devolveu a tabela a 7
registros.

## 4.2. Evidência do critério de conclusão da etapa

> **Critério, conforme `docs/contexto.md`:** "Um `INSERT` conflitante
> executado **diretamente no MySQL Workbench**, sem passar pela aplicação, é
> rejeitado com mensagem clara. Este é o teste que prova o argumento central
> do projeto."

A verificação foi executada pelo cliente de linha de comando `mysql`,
conectado como **`root`** ao banco `villaju` — o mesmo caminho e os mesmos
privilégios de uma operação feita no Workbench.

### Estado inicial

```
+----+----------+--------------+------------+-------------+------------+
| id | chale_id | data_entrada | data_saida | num_pessoas | situacao   |
+----+----------+--------------+------------+-------------+------------+
|  1 |        1 | 2026-12-15   | 2026-12-20 |           4 | confirmada |
|  2 |        1 | 2026-12-20   | 2026-12-27 |           2 | confirmada |
+----+----------+--------------+------------+-------------+------------+
```

### Comando enviado

```sql
INSERT INTO reserva
    (chale_id, hospede_id, data_entrada, data_saida, num_pessoas, situacao)
VALUES
    (1, 3, '2026-12-17', '2026-12-22', 2, 'confirmada');
```

O período de 17 a 22/12 sobrepõe a reserva 1 (15 a 20/12) e a reserva 2
(20 a 27/12).

### Saída recebida, na íntegra

```
ERROR 1644 (45000) at line 1: Já existe reserva ativa para este chalé no período informado.
```

Código de saída do processo: **1**.

### Estado após a tentativa

```
+-------+
| total |
+-------+
|     2 |
+-------+
```

A tabela permaneceu com 2 reservas. **Nada foi gravado.**

### As demais regras, verificadas pelo mesmo caminho

| Comando enviado | Saída recebida |
|---|---|
| `INSERT ... num_pessoas = 6` num chalé para 4 | `ERROR 1644 (45000) at line 1: Número de pessoas acima da capacidade do chalé.` |
| `INSERT ... data_entrada '2027-06-10', data_saida '2027-06-05'` | `ERROR 1644 (45000) at line 1: A data de saída deve ser posterior à data de entrada.` |
| `INSERT ... data_entrada '2027-06-10', data_saida '2027-06-10'` | `ERROR 1644 (45000) at line 1: A data de saída deve ser posterior à data de entrada.` |
| `UPDATE reserva SET data_entrada='2026-12-16', data_saida='2026-12-19' WHERE id=2` | `ERROR 1644 (45000) at line 1: Já existe reserva ativa para este chalé no período informado.` |

> **Nota sobre codificação.** As mensagens acima estão transcritas como o
> MySQL as emite, em UTF-8. O console do Git Bash, sob a página de código
> padrão do Windows, as apresenta com acentuação corrompida
> (`J� existe reserva...`). Trata-se de limitação de exibição do terminal,
> não do conteúdo armazenado.

## 4.3. Validação dos testes por sabotagem deliberada

Mantendo a prática da Etapa 0: cada proteção nova foi quebrada de propósito,
confirmou-se que o teste correspondente falha, e o estado foi restaurado.

### Schema

| # | Sabotagem aplicada | Teste que detectou | Resultado |
|---|---|---|---|
| 1 | `email_cif` de `VARBINARY(512)` para `VARCHAR(512)` | `test_coluna_tem_tipo_e_nulidade_corretos`, `test_campo_cifrado_e_binario` | 2 falhas, 131 aprovados |
| 2 | Índice de disponibilidade reordenado para `(situacao, chale_id, data_entrada, data_saida)` | `test_indice_de_disponibilidade_existe_na_ordem_certa` | 1 falha, 132 aprovados |
| 3 | `log_auditoria` → `usuario` de `SET NULL` para `CASCADE` | `test_chave_estrangeira_tem_a_regra_de_exclusao_certa[log_auditoria-usuario_id]` | 1 falha, 132 aprovados |

### Triggers

| # | Sabotagem aplicada | Consequência real | Teste que detectou | Resultado |
|---|---|---|---|---|
| 4 | `<` e `>` trocados por `<=` e `>=` na verificação de sobreposição | Chalé bloqueado um dia inteiro sem necessidade | `test_entrada_no_dia_da_saida_de_outra_e_aceita`, `test_saida_no_dia_da_entrada_de_outra_e_aceita` | 2 falhas, 36 aprovados |
| 5 | Removida a cláusula `AND id <> COALESCE(NEW.id, 0)` do `UPDATE` | Nenhuma reserva poderia ser editada | `test_update_apenas_da_observacao_e_aceito`, `test_update_que_mantem_as_mesmas_datas_e_aceito`, `test_alteracao_e_exclusao_geram_auditoria` | 3 falhas, 35 aprovados |
| 6 | Removido o filtro `AND situacao IN ('pendente','confirmada')` | Reserva cancelada voltaria a ocupar o chalé para sempre | `test_conflito_com_reserva_cancelada_e_aceito`, `test_conflito_com_reserva_concluida_e_aceito`, `test_cancelar_libera_o_periodo` | 3 falhas, 35 aprovados |
| 7 | Trigger de auditoria passou a gravar `CONCAT('Hóspede: ', NEW.nome_cif)` | Cifragem anulada: o log viraria segunda via do dado protegido | `test_log_nunca_contem_conteudo_cifrado` | 1 falha, 37 aprovados |
| 8 | Removida a verificação de capacidade | Superlotação dos chalés | `test_acima_da_capacidade_e_bloqueado`, `test_update_acima_da_capacidade_e_bloqueado` | 2 falhas, 36 aprovados |
| 9 | Removido `SET NEW.criado_em = OLD.criado_em` | Data de criação reescrevível | `test_data_de_criacao_e_imutavel` | 1 falha, 37 aprovados |

### Privilégios

| # | Sabotagem aplicada | Teste que detectou | Resultado |
|---|---|---|---|
| 10 | `GRANT UPDATE, DELETE ON villaju_teste.log_auditoria` à aplicação, e `GRANT CREATE, DROP, ALTER ON villaju_teste.*` | `test_aplicacao_nao_altera_o_log`, `test_aplicacao_nao_apaga_do_log`, `test_aplicacao_nao_esvazia_o_log`, `test_aplicacao_nao_cria_tabela`, `test_aplicacao_nao_apaga_tabela`, `test_aplicacao_nao_altera_tabela` | 6 falhas, 27 aprovados |

Os privilégios foram revogados e `SHOW GRANTS` confirmou o retorno ao estado
correto.

### Massa de dados

| # | Sabotagem aplicada | Teste que detectou | Resultado |
|---|---|---|---|
| 11 | CPF `123.456.789-00` inserido nas observações de uma reserva | `test_observacoes_nao_contem_dado_sensivel` | 1 falha, 32 aprovados |
| 12 | Nome em texto claro gravado numa coluna `_cif` | `test_campos_pessoais_nao_contem_texto_legivel` | 1 falha, 32 aprovados |
| 13 | Capacidade do Embaré reduzida de 4 para 2 | **A carga inteira foi recusada** | 31 erros, 2 aprovados |

A sabotagem 13 produziu erros em vez de falhas, e isso é o resultado mais
forte dos treze: a massa de dados não "passa" nos testes — ela **não
carrega** se violar as regras. A mensagem identifica o ponto exato:

```
AssertionError: 04_dados_ficticios.sql: falha no comando 21 de 24
  início: INSERT INTO reserva
  erro:   1644 (45000): Número de pessoas acima da capacidade do chalé.
```

**Total: 13 sabotagens aplicadas, 13 detectadas.** Em todos os casos o
estado original foi restaurado e a suíte voltou a passar integralmente.

## 4.4. Contagem de testes

| Momento | Total |
|---|---|
| Ao final da Etapa 0 | **145** |
| Acrescentados na Etapa 1 | **+271** |
| Ao final da Etapa 1 | **416** |

### Distribuição por arquivo

| Arquivo | Etapa 0 | Etapa 1 | Variação |
|---|---|---|---|
| `testes/test_ambiente.py` | 21 | 21 | — |
| `testes/test_repositorio.py` | 36 | 36 | — |
| `testes/test_tls.py` | 11 | 11 | — |
| `testes/test_configuracao.py` | 38 | 57 | +19 |
| `testes/test_codigo_sem_acento.py` | 39 | 54 | +15 |
| `testes/test_schema.py` | — | 133 | +133 |
| `testes/test_triggers.py` | — | 38 | +38 |
| `testes/test_privilegios.py` | — | 33 | +33 |
| `testes/test_dados_ficticios.py` | — | 33 | +33 |
| **Total** | **145** | **416** | **+271** |

O crescimento de `test_codigo_sem_acento.py` (+15) não vem de testes novos,
e sim da parametrização existente, que passou a cobrir os quatro arquivos
Python criados nesta etapa. O de `test_configuracao.py` (+19) combina as
duas coisas: testes novos para as senhas de banco e parametrização sobre os
dois scripts SQL que contêm placeholder de senha.

### Resultado da execução final

```
============================ 416 passed in 16.86s =============================
```

---

# 5. ANÁLISE ESTÁTICA APÓS A ETAPA

Todas as ferramentas foram executadas após o último commit da etapa.

| Ferramenta | Versão | Alvo | Achados |
|---|---|---|---|
| Bandit | 1.9.4 | `app/`, `scripts/`, `run.py` | **0** |
| Semgrep OSS | 1.182.1 | `app/`, `run.py`, `scripts/`, `testes/` | **0** |
| pip-audit | 2.10.1 | Ambiente da aplicação | **0** |
| gitleaks | 8.30.1 | Histórico completo do Git | **0** |

## 5.1. Bandit

```
Code scanned:
	Total lines of code: 203
	Total lines skipped (#nosec): 0
	Total potential issues skipped due to specifically being disabled (e.g., #nosec BXXX): 0

Run metrics:
	Total issues (by severity):
		Undefined: 0
		Low: 0
		Medium: 0
		High: 0
	Total issues (by confidence):
		Undefined: 0
		Low: 0
		Medium: 0
		High: 0
Files skipped (0):
```

## 5.2. Semgrep OSS

Conjuntos de regras `p/owasp-top-ten` e `p/flask`, com envio de métricas
desativado:

```
 • Findings: 0 (0 blocking)
 • Rules run: 154
 • Targets scanned: 22
 • Parsed lines: ~100.0%
Ran 154 rules on 22 files: 0 findings.
```

## 5.3. pip-audit

```
No known vulnerabilities found
```

## 5.4. gitleaks

Varredura do histórico completo, com todas as referências:

```
4:58PM INF 36 commits scanned.
4:58PM INF scanned ~257065 bytes (257.07 KB) in 218ms
4:58PM INF no leaks found
```

Os 36 commits incluem os 19 da Etapa 0 e os 17 da Etapa 1.

## 5.5. Observação sobre a cobertura das ferramentas

Nenhuma das quatro ferramentas analisa SQL. Os arquivos `banco/*.sql` — que
concentram a lógica entregue nesta etapa — não são cobertos por análise
estática automatizada.

A verificação desses arquivos foi feita por outros meios: 237 testes
automatizados que exercitam o schema, as triggers, os privilégios e a massa
de dados, 13 sabotagens deliberadas, e a execução dos scripts contra um
servidor MySQL real. Nenhum deles substitui uma ferramenta de análise
estática de SQL, e essa é uma limitação conhecida, registrada na seção 6.2.

---

# 6. PENDÊNCIAS E LIMITAÇÕES

## 6.1. Pendências da Etapa 1

| Pendência | Responsável | Prazo |
|---|---|---|
| Exportar o diagrama ER em imagem do MySQL Workbench para `docs/` | Dimas Marcondes | Antes da Entrega 2 (04/11) |
| Revisão por pares dos scripts SQL por integrante que não os escreveu | Grupo | Antes da Entrega 2 |

A revisão por pares está prevista no plano como prática da etapa — "1
integrante responsável, 1 revisor" — e **não foi realizada**. Os scripts
foram escritos e verificados por testes automatizados, mas não lidos por
outro integrante.

## 6.2. Limitações conhecidas

### Previstas — serão tratadas em etapa posterior

| Limitação | Etapa que trata |
|---|---|
| Os dados de hóspede na massa fictícia não são ciphertext real | Etapa 2 |
| Não existe aplicação que use este banco; não há repositório de dados em Python | Etapa 2 e 3 |
| A regra de não-sobreposição não é antecipada pela aplicação com mensagem amigável | Etapa 3 |
| O cálculo de valor total na virada de temporada não está implementado | Etapa 3 |
| Nenhuma rota consulta disponibilidade | Etapa 3 |

### Aceitas — decisão consciente

| Limitação | Razão |
|---|---|
| Os scripts SQL não passam por análise estática automatizada | Nenhuma das ferramentas do projeto analisa SQL. Mitigado por 237 testes e 13 sabotagens |
| `log_bin_trust_function_creators = 1` altera configuração global do servidor | Seção 2.5. A alternativa seria conceder `SUPER` |
| As senhas de demonstração estão publicadas no repositório | São contas fictícias. Registrado no cabeçalho do script que precisam ser removidas ou trocadas antes de qualquer uso real |
| A trigger de auditoria mascara identificador de usuário inválido | Seção 2.8. Auditoria não pode derrubar a operação que observa |
| O arquivo `.mylogin.cnf` guarda a senha do `root` de forma ofuscada, não criptografada | Criado para permitir a execução automatizada dos scripts sem expor a senha. Removível com `mysql_config_editor remove --login-path=villaju_root` |

## 6.3. Pendências herdadas da Etapa 0

| Pendência | Situação |
|---|---|
| Os quatro demais integrantes montarem o ambiente | **Pendente.** O ambiente continua validado em uma única máquina. A tabela de ambientes do `README.md` tem quatro linhas vazias |
| Senha do usuário de banco `villaju_app` | **Resolvida.** Gerada por `scripts/gerar_chaves.py` com `secrets.token_urlsafe(24)` — 192 bits. Está no `.env` local, fora do controle de versão |
| Capacidade exata de cada chalé | **Resolvida.** Confirmada em 4 pessoas para os cinco chalés. Implementada na massa de dados e verificada pelo teste `test_todos_os_chales_comportam_quatro_pessoas` |
| Horários de entrada e saída | **Pendente.** Não confirmados com a responsável |

### Sobre os horários de entrada e saída

**Não foram confirmados.** O impacto é o seguinte:

**Não há impacto técnico sobre a trigger.** A regra de sobreposição opera
sobre colunas do tipo `DATE`, sem componente de hora. O intervalo semiaberto
— `NEW.data_entrada < data_saida AND NEW.data_saida > data_entrada` —
funciona independentemente de quais sejam os horários praticados.

**O impacto é de justificativa.** A escolha do intervalo semiaberto assume
que a saída de um hóspede ocorre antes da entrada do próximo no mesmo dia.
Essa premissa é o que torna a regra correta do ponto de vista do negócio.
Sem os horários confirmados, a defesa diante da banca fica apoiada numa
suposição razoável, e não num dado da parceira.

Se os horários forem incompatíveis — por exemplo, entrada às 10h e saída às
12h —, a regra atual permitiria uma sobreposição real de duas horas. Nesse
caso o tratamento seria de processo, não de schema: bloquear a entrada no
mesmo dia da saída passaria a exigir alterar a comparação para `>=`, com
perda de uma diária por troca de hóspede.

A confirmação continua pendente com Dimas Marcondes.

## 6.4. Decisões que estavam em aberto e foram resolvidas

| Item em aberto em `docs/contexto.md` | Prazo previsto | Escolha feita | Razão |
|---|---|---|---|
| Capacidade exata por chalé | Etapa 1 | **4 pessoas nos cinco chalés** | Confirmado pelo grupo. Implementado em `banco/04_dados_ficticios.sql` e verificado por teste |
| Horários de entrada e saída | Etapa 1 | **Não resolvido** | Depende de confirmação com a responsável |
| Campos de cadastro do hóspede | Etapa 1 | **Mantido o previsto** | Nome e documento obrigatórios; telefone e e-mail opcionais. Implementado na nulidade das colunas e verificado por teste |
| Hospedagem do repositório | Etapa 0 | **GitHub** | Decidido na prática ao criar o repositório. O Git é software livre; o GitHub é serviço proprietário de uso gratuito |

Permanecem em aberto, com prazo em etapas posteriores: o tratamento do caso
de borda do AAD no `INSERT` de hóspede novo (Etapa 2), a cifragem dos dados
de `contato` (Etapa 5), o escopo de marketing (Etapa 5) e a publicação na
internet (até a Etapa 5).

---

# 7. ANEXOS

## 7.1. Histórico de commits da Etapa 1

| # | Identificador | Descrição |
|---|---|---|
| 1 | `329165d` | documenta protótipo e plano de testes da Etapa 0 |
| 2 | `8fb9e99` | gera também as senhas de banco com secrets |
| 3 | `2593897` | cria bancos de produção e de teste com usuário próprio |
| 4 | `6657df8` | testa geração de senhas e protege contra senha versionada |
| 5 | `868a458` | esclarece as duas senhas e o comando no PowerShell |
| 6 | `14115b5` | cria schema com as sete tabelas e chaves estrangeiras |
| 7 | `f31dc5c` | monta banco de teste a partir dos scripts reais |
| 8 | `73299a5` | testa estrutura, tipos e integridade do schema |
| 9 | `85fdf44` | permite criar triggers sem conceder SUPER ao usuário |
| 10 | `82b5910` | implementa as dezessete triggers de regra e auditoria |
| 11 | `ee7ab82` | cobre os treze casos obrigatórios de trigger |
| 12 | `666f78c` | cria usuário de aplicação com privilégio mínimo |
| 13 | `8057ce6` | verifica restrições do usuário de aplicação no banco |
| 14 | `148d035` | adiciona massa de dados dos cinco chalés |
| 15 | `32ce56d` | documenta modelo entidade-relacionamento |
| 16 | `ae20958` | testa integridade da massa de dados fictícios |
| 17 | `a96b5ca` | corrige cabeçalho e atualiza cronograma do README |

O commit 1 entrega a documentação técnica da Etapa 0 e foi feito já dentro
da janela da Etapa 1. Total acumulado do repositório ao final da etapa: 36
commits.

## 7.2. Comandos de verificação da etapa

Reprodução a partir de um clone limpo, por terceiro.

### Preparo do ambiente

```powershell
git clone https://github.com/dimasmmdasilva/projetoVillaJu.git
cd projetoVillaJu

python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
python scripts\gerar_chaves.py
# colar a saída no .env
```

### Construção do banco

Cole em `banco/00_bancos.sql` o valor de `VILLAJU_DB_SENHA_TESTE` e em
`banco/03_usuario_app.sql` o de `VILLAJU_DB_SENHA`. Restaure os
placeholders depois.

```powershell
mysql -u root -p -e "SOURCE banco/00_bancos.sql"

mysql -u root -p villaju -e "SOURCE banco/01_schema.sql"
mysql -u root -p villaju -e "SOURCE banco/02_triggers.sql"
mysql -u root -p villaju -e "SOURCE banco/03_usuario_app.sql"
mysql -u root -p villaju -e "SOURCE banco/04_dados_ficticios.sql"

mysql -u root -p villaju_teste -e "SOURCE banco/03_usuario_app.sql"
```

> No PowerShell, o operador `<` não existe. Use `-e "SOURCE arquivo.sql"`.
> No `cmd` ou no Git Bash, `mysql -u root -p villaju < banco/01_schema.sql`
> também funciona.

### Execução dos testes

```powershell
pytest
# esperado: 416 passed

pytest testes/test_triggers.py -v
pytest testes/test_schema.py -v
pytest testes/test_privilegios.py -v
pytest testes/test_dados_ficticios.py -v
```

### Reprodução do critério de conclusão

```powershell
mysql -u root -p villaju -e "INSERT INTO reserva (chale_id, hospede_id, data_entrada, data_saida, num_pessoas, situacao) VALUES (1, 3, '2026-12-17', '2026-12-22', 2, 'confirmada');"
```

Saída esperada:

```
ERROR 1644 (45000) at line 1: Já existe reserva ativa para este chalé no período informado.
```

### Análise estática

```powershell
bandit -r app/ scripts/ run.py
pip-audit
gitleaks git . --log-opts="--all" --redact

.\venv-ferramentas\Scripts\activate
semgrep --config=p/owasp-top-ten --config=p/flask --metrics=off app/ run.py scripts/ testes/
```

### Inspeção direta do banco

```powershell
mysql -u root -p villaju -e "SELECT trigger_name, event_object_table, action_timing, event_manipulation FROM information_schema.triggers WHERE trigger_schema='villaju' ORDER BY event_object_table"

mysql -u root -p -e "SHOW GRANTS FOR 'villaju_app'@'localhost'"
```

## 7.3. Scripts SQL entregues

| Caminho | Finalidade | Executar como |
|---|---|---|
| `banco/00_bancos.sql` | Cria os bancos `villaju` e `villaju_teste` com `utf8mb4`, cria o usuário `villaju_teste` restrito ao banco de testes e liga `log_bin_trust_function_creators` | `root`, uma vez |
| `banco/01_schema.sql` | Cria as 7 tabelas com chaves estrangeiras, restrições de unicidade e índices. Não seleciona banco | `root`, por banco |
| `banco/02_triggers.sql` | Cria as 17 triggers em 5 grupos. Não seleciona banco | `root`, por banco |
| `banco/03_usuario_app.sql` | Cria `villaju_app` e concede privilégio mínimo tabela a tabela. Não seleciona banco | `root`, por banco |
| `banco/04_dados_ficticios.sql` | Carrega a massa de dados. Idempotente. Não seleciona banco | `root` ou `villaju_app`, por banco |

A ordem de execução é a numérica. O `01` precisa rodar antes do `03`, porque
privilégios tabela a tabela exigem que as tabelas existam. O `02` precisa
rodar antes do `04`, porque a carga depende das triggers de carimbo de tempo
para preencher `criado_em` e `alterado_em`.

---

## Declaração de conduta ética nos testes

Todas as ferramentas de análise empregadas nesta etapa — Bandit, Semgrep,
pip-audit e gitleaks — foram executadas **exclusivamente contra o sistema
desenvolvido pelo grupo, em ambiente local**.

As sabotagens descritas na seção 4.3 foram aplicadas a arquivos do próprio
repositório, em cópia de trabalho, e revertidas imediatamente após a
verificação. Os privilégios excedentes concedidos na sabotagem 10 foram
concedidos e revogados no banco de testes local, nunca no banco real.

Nenhum teste foi executado contra sistema da instituição parceira, de
terceiros, ou contra qualquer endereço que não seja a instância local do
projeto.
