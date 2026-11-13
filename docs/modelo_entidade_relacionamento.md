# Modelo Entidade-Relacionamento — VillaJu

Documentação do modelo de dados implementado em `banco/01_schema.sql`.
Atualizado em 13/11/2026, com a Etapa 5 até o Bloco 7.

O diagrama abaixo é escrito em Mermaid, que o GitHub renderiza
automaticamente. Por ser texto, ele vai ao controle de versão como código:
qualquer alteração aparece no `git diff`, ao contrário de uma imagem
exportada, em que a mudança é invisível no histórico.

> A exportação em imagem do MySQL Workbench acompanha este documento, para
> atender ao formato pedido no plano. Este arquivo é a versão autoritativa,
> porque é a que evolui junto com o schema.

---

## Diagrama

```mermaid
erDiagram
    CHALE ||--o{ TARIFA : "tem preço por período"
    CHALE ||--o{ RESERVA : "é reservado em"
    CHALE ||--o{ CONTATO : "desperta interesse em"

    HOSPEDE ||--o{ RESERVA : "faz"
    HOSPEDE ||--o| USUARIO : "pode ter conta de acesso"

    HOSPEDE ||--o{ CONTATO : "pode ter originado"

    USUARIO ||--o{ RESERVA : "registra"
    USUARIO ||--o{ LOG_AUDITORIA : "é autor de"
    USUARIO ||--o{ TOKEN_ACESSO : "recebe link"

    RESERVA ||--o{ PAGAMENTO : "tem lançamentos"
    USUARIO ||--o{ PAGAMENTO : "conferiu no extrato"

    CHALE {
        int id PK
        varchar nome UK "único"
        tinyint capacidade "máximo de pessoas"
        varchar configuracao_camas
        text comodidades
        text descricao "usado na vitrine pública"
        boolean ativo "desativa sem apagar"
        datetime criado_em
        datetime alterado_em
    }

    TARIFA {
        int id PK
        int chale_id FK
        date data_inicio
        date data_fim
        decimal valor_diaria "DECIMAL, nunca FLOAT"
        varchar descricao "alta ou baixa temporada"
        datetime criado_em
        datetime alterado_em
    }

    HOSPEDE {
        int id PK
        varbinary nome_cif "cifrado AES-256-GCM"
        varbinary nome_nonce "96 bits"
        varbinary nome_tag "128 bits"
        varbinary documento_cif "cifrado"
        varbinary documento_nonce
        varbinary documento_tag
        varbinary telefone_cif "cifrado, opcional"
        varbinary telefone_nonce
        varbinary telefone_tag
        varbinary email_cif "cifrado, opcional"
        varbinary email_nonce
        varbinary email_tag
        varbinary email_busca UK "HMAC-SHA256"
        varbinary documento_busca UK "HMAC-SHA256"
        tinyint cripto_versao "permite migração"
        datetime criado_em
        datetime alterado_em
    }

    USUARIO {
        int id PK
        varchar login UK
        varchar nome
        varchar senha_hash "Argon2id com parâmetros embutidos"
        enum perfil "administrador, recepcionista, hospede"
        int hospede_id FK "só quando perfil = hospede"
        boolean ativo
        datetime ultimo_acesso
        datetime criado_em
        datetime alterado_em
    }

    RESERVA {
        int id PK
        int chale_id FK
        int hospede_id FK
        date data_entrada "inclusive"
        date data_saida "exclusive"
        tinyint num_pessoas "limitado pela capacidade"
        enum situacao "pendente, confirmada, cancelada, concluida"
        decimal valor_total "congelado na criação"
        enum modalidade "sinal, integral; nula se registrada pela equipe"
        datetime prazo_pagamento "NOW() + 90 min, calculado pelo banco"
        datetime prazo_restante "23h59 de 2 dias antes da entrada"
        datetime pix_informado_em "declaração do hóspede"
        datetime restante_informado_em
        datetime regulamento_aceito_em
        datetime cancelado_em
        enum motivo_cancelamento "prazo_do_pix, falta_de_pagamento, pedido_do_hospede, equipe"
        text observacoes "não cifrado: sem dado sensível"
        int criado_por FK
        datetime criado_em
        datetime alterado_em
    }

    LOG_AUDITORIA {
        bigint id PK
        int usuario_id FK "nulo em evento sem sessão"
        varchar acao "INSERT, UPDATE, DELETE, LOGIN_OK..."
        varchar tabela_afetada
        int registro_id
        datetime data_hora
        varchar detalhe "descrição genérica, sem valores"
    }

    CONTATO {
        int id PK
        varbinary nome_cif "cifrado AES-256-GCM, com nonce e tag"
        varbinary email_cif "cifrado, com nonce e tag"
        varbinary telefone_cif "cifrado, opcional"
        varbinary mensagem_cif "cifrado, opcional"
        varbinary email_busca "HMAC-SHA256"
        tinyint cripto_versao
        int chale_interesse_id FK
        int hospede_id FK "quando o contato virou cadastro"
        date data_entrada_desejada
        date data_saida_desejada
        varchar origem "site, instagram"
        varbinary ip_origem "para limitação de taxa"
        enum situacao "novo, em_atendimento, respondido, convertido, descartado"
        datetime criado_em
    }

    TOKEN_ACESSO {
        int id PK
        int usuario_id FK
        varbinary token_hash UK "SHA-256; o token nunca é guardado"
        enum tipo "confirmacao, recuperacao"
        datetime expira_em
        datetime consumido_em "uso único"
        datetime criado_em
    }

    PAGAMENTO {
        int id PK
        int reserva_id FK
        enum tipo "sinal, restante, integral, devolucao"
        decimal valor "positivo na entrada, negativo na devolução"
        int registrado_por FK "quem conferiu o Pix"
        datetime registrado_em "preenchido pelo gatilho"
    }
```

---

## Relacionamentos

| Origem | Destino | Cardinalidade | Ao apagar o pai | Justificativa |
|---|---|---|---|---|
| `tarifa` | `chale` | N:1 | **RESTRICT** | Preserva o histórico de preços praticados |
| `reserva` | `chale` | N:1 | **RESTRICT** | Não se apaga um chalé que tem reservas |
| `reserva` | `hospede` | N:1 | **RESTRICT** | Não se apaga um hóspede com histórico |
| `reserva` | `usuario` | N:1 | **SET NULL** | Desligar um recepcionista não apaga as reservas que ele registrou |
| `usuario` | `hospede` | 1:0..1 | **RESTRICT** | A conta precisa ser removida antes, deliberadamente |
| `log_auditoria` | `usuario` | N:1 | **SET NULL** | **A auditoria sobrevive à exclusão do usuário** |
| `contato` | `chale` | N:1 | **SET NULL** | O pedido continua válido sem o chalé de interesse |
| `contato` | `hospede` | N:1 | **SET NULL** | O contato é o registro de que alguém pediu informação, e sobrevive ao cadastro |
| `token_acesso` | `usuario` | N:1 | **CASCADE** | Token é autorização pendente, não histórico: sem a conta, não autoriza nada |
| `pagamento` | `reserva` | N:1 | **RESTRICT** | Não se apaga reserva com lançamento |
| `pagamento` | `usuario` | N:1 | **RESTRICT** | O autor é a parte do lançamento que lhe dá valor; conta de equipe se desativa |

A escolha em `log_auditoria` é a mais importante de todas. Com `CASCADE`,
bastaria apagar uma conta para fazer sumir o rastro de tudo o que ela fez —
exatamente o que alguém faria para encobrir uma ação indevida. Com
`SET NULL`, o evento permanece registrado, apenas sem o vínculo.

---

## Índices

| Tabela | Índice | Colunas | Para quê |
|---|---|---|---|
| `reserva` | `idx_reserva_disponibilidade` | `chale_id`, `data_entrada`, `data_saida`, `situacao` | Consulta de disponibilidade e trigger de conflito |
| `tarifa` | `idx_tarifa_periodo` | `chale_id`, `data_inicio`, `data_fim` | Busca da tarifa vigente numa data |
| `log_auditoria` | `idx_log_data_hora` | `data_hora` | "O que aconteceu neste período" |
| `log_auditoria` | `idx_log_usuario` | `usuario_id`, `data_hora` | "O que este usuário fez" |
| `contato` | `idx_contato_situacao` | `situacao`, `criado_em` | Fila de atendimento |
| `contato` | `idx_contato_ip` | `ip_origem`, `criado_em` | Limitação de taxa do formulário público |
| `reserva` | `idx_reserva_prazo_pagamento` | `situacao`, `prazo_pagamento` | Varredura dos pedidos com o prazo do Pix vencido |
| `token_acesso` | `uq_token_hash` | `token_hash` | Consumo do link; único, porque dois hashes iguais seriam o mesmo token |
| `token_acesso` | `idx_token_usuario` | `usuario_id`, `tipo`, `consumido_em` | Invalidar os pendentes quando um link novo é emitido |
| `pagamento` | `uq_pagamento_reserva_tipo` | `reserva_id`, `tipo` | **Único**: um clique duplo não conta o sinal duas vezes |
| `pagamento` | `idx_pagamento_data` | `registrado_em` | A soma do mês no painel do administrador |

A ordem das colunas em `idx_reserva_disponibilidade` não é arbitrária. Um
índice composto só é aproveitado da esquerda para a direita: filtra-se
primeiro pelo chalé, depois pelo intervalo de datas, e por último
descartam-se as reservas canceladas. Invertida a ordem, a trigger de
conflito passaria a varrer a tabela inteira.

---

## Decisões de modelagem

### Por que os dados do hóspede ocupam três colunas cada

A cifragem AES-256-GCM produz três valores que só fazem sentido juntos: o
texto cifrado, o *nonce* de 96 bits usado naquela operação e a *tag* de
autenticação de 128 bits. Guardá-los em colunas separadas torna explícito
o que cada um é, e evita o trabalho de serializar e desserializar um campo
único a cada leitura.

### Por que VARBINARY e não VARCHAR

`VARCHAR` tem charset e collation, e o MySQL aplica conversão de
codificação sobre o conteúdo. Texto cifrado é sequência arbitrária de
bytes, não texto — a conversão o corromperia em silêncio, e a corrupção só
apareceria na hora de decifrar, possivelmente muito depois.

### Por que existem os campos `_busca`

O AES-GCM é não determinístico por projeto: cifrar duas vezes o mesmo
e-mail produz bytes diferentes. Isso impede `WHERE email_cif = ?` e impede
`UNIQUE` sobre a coluna cifrada.

A solução é guardar ao lado um HMAC-SHA256 do valor normalizado, com uma
chave de índice separada da chave de cifragem. O HMAC é determinístico,
permite busca e restrição de unicidade, e não é reversível.

Hash simples não serviria: sem chave, um CPF tem cerca de 10¹¹
possibilidades e é quebrável por força bruta em segundos.

### Por que `contato` passou a ser cifrada

A primeira versão guardava o pedido de contato em texto simples, com o
argumento de que é dado de menor sensibilidade que o cadastro de hóspede.
**O argumento foi recusado em 06/11:** é a mesma pessoa, com o mesmo nome
e o mesmo telefone. Desde a Etapa 4, `contato` usa o mesmo mecanismo de
`hospede` — AES-256-GCM com nonce e tag por campo, e HMAC-SHA256 para a
busca pelo e-mail.

### Por que o livro de pagamentos só aceita acréscimo

O sistema não transaciona: o Pix acontece entre o aplicativo do banco do
hóspede e o da pousada. O que a tabela `pagamento` guarda é **quem da
equipe afirmou ter visto o dinheiro no extrato, e quando** — e essa é a
única prova que existe. Por isso duas camadas, como no `log_auditoria`:

- **Privilégio:** a conta da aplicação tem só `SELECT` e `INSERT`. Nem a
  aplicação comprometida reescreve um lançamento.
- **Gatilho:** `UPDATE` e `DELETE` são recusados também para quem entra no
  banco com outra conta, com o Workbench aberto, por exemplo.

Correção é lançamento novo. A devolução entra com valor negativo, no mês
em que acontece, para que a soma do mês seja a soma simples da coluna.

### Por que o prazo do Pix é calculado pelo banco

`prazo_pagamento` é gravado como `NOW() + 90 minutos` no próprio `INSERT`,
e conferido com `prazo_pagamento > NOW()` no `UPDATE` que registra o "Fiz o
Pix". Um relógio só para a mesma regra: no Bloco 4 da Etapa 5, dois
relógios para um prazo foram defeito.

O gatilho de não-sobreposição **não mudou**: ele trata todo `pendente`
como ocupação, inclusive um pedido vencido que ainda não foi cancelado.
Por isso toda escrita de reserva cancela os pedidos vencidos **antes** de
gravar, na mesma transação — e há teste que prende esse fato do banco.

### Por que o intervalo de reserva é semiaberto

A reserva ocupa de `data_entrada` inclusive até `data_saida` exclusive.
Assim a saída de um hóspede libera o chalé para a entrada de outro no mesmo
dia — que é como a hospedaria opera, com saída pela manhã e entrada à
tarde. Se o intervalo fosse fechado, o chalé ficaria bloqueado um dia
inteiro sem necessidade, e o sistema recusaria receita legítima.

---

## Como exportar o diagrama do MySQL Workbench

Para gerar a versão em imagem:

1. **Database → Reverse Engineer** (`Ctrl+R`)
2. Conectar em `localhost:3306` como `root`
3. Selecionar o schema `villaju`
4. Avançar até o diagrama ser gerado
5. **File → Export → Export as PNG**, salvando em `docs/`

O Workbench posiciona as tabelas automaticamente, o que costuma produzir
um emaranhado de linhas. Vale arrastar as sete caixas antes de exportar,
mantendo `reserva` ao centro — é ela que concentra as relações.
