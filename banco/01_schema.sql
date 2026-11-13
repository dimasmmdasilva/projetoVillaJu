-- ---------------------------------------------------------------------------
-- VillaJu - modelo de dados
--
-- As tabelas do sistema: as 7 da seção 5.1 do plano do projeto, mais
-- `token_acesso`, `pagamento` e `acompanhante`, acrescentadas na Etapa 5.
--
-- Este script NÃO contém CREATE DATABASE nem USE, de propósito: quem decide
-- em qual banco os objetos nascem é a conexão que o executa. É isso que
-- permite à suíte automatizada montar o banco de teste a partir deste mesmo
-- arquivo, em vez de testar uma cópia que pode divergir do original.
--
-- No PowerShell:
--
--     mysql -u root -p villaju -e "SOURCE banco/01_schema.sql"
--
-- A ordem de criação segue as dependências de chave estrangeira: uma tabela
-- só pode referenciar outra que já exista.
--
--     chale  ->  tarifa, contato
--     hospede  ->  usuario
--     chale + hospede + usuario  ->  reserva
--     usuario  ->  log_auditoria
--     reserva  ->  pagamento, acompanhante
--
-- Sobre criado_em e alterado_em: são NOT NULL e não têm valor padrão. Quem
-- os preenche são as triggers de carimbo de tempo, em 02_triggers.sql. O
-- plano exige triggers explícitas em vez de DEFAULT CURRENT_TIMESTAMP para
-- que o comportamento fique visível no script, e não escondido na definição
-- da coluna.
-- ---------------------------------------------------------------------------

-- ===========================================================================
-- 1. chale
-- ===========================================================================
-- Os cinco chalés da hospedaria: Embaré, Camburi, Asturias, Boracéia e Dunas.

-- ---------------------------------------------------------------------------
-- CODIFICACAO - leia antes de mudar a linha abaixo
--
-- Este arquivo esta em UTF-8 e contem acentos. O cliente `mysql.exe` no
-- Windows assume por padrao a pagina de codigo do console (CP850, CP437 ou
-- CP1252, conforme a maquina) e anuncia essa pagina ao servidor. Como os
-- bytes enviados sao UTF-8, o servidor converte do charset errado e grava
-- texto corrompido: "Embare" vira "Embar" seguido de dois simbolos, e o
-- erro so aparece na tela, depois.
--
-- O SET NAMES abaixo informa ao servidor a codificacao de verdade dos
-- comandos que seguem, tornando o script correto independentemente de como
-- o cliente foi invocado. Sem ele, seria preciso lembrar de passar
-- --default-character-set=utf8mb4 em toda chamada - e esquecer uma vez
-- basta para corromper a base.
-- ---------------------------------------------------------------------------

SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE chale (
    id                  INT UNSIGNED NOT NULL AUTO_INCREMENT,
    nome                VARCHAR(50) NOT NULL,
    capacidade          TINYINT UNSIGNED NOT NULL,
    configuracao_camas  VARCHAR(255) NULL,
    comodidades         TEXT NULL,
    descricao           TEXT NULL,

    -- Desativa o chalé sem apagá-lo. Apagar perderia o histórico de
    -- reservas, e as chaves estrangeiras RESTRICT impediriam a exclusão
    -- de qualquer forma.
    ativo               BOOLEAN NOT NULL DEFAULT TRUE,

    criado_em           DATETIME NOT NULL,
    alterado_em         DATETIME NOT NULL,

    PRIMARY KEY (id),

    -- Dois chalés com o mesmo nome tornariam ambíguo tudo o que vem depois:
    -- a vitrine pública, a escolha na reserva e o relatório de ocupação.
    UNIQUE KEY uq_chale_nome (nome)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ===========================================================================
-- 2. hospede
-- ===========================================================================
-- Dados dos locatários. TODOS os campos pessoais são cifrados com
-- AES-256-GCM antes de chegarem aqui (seção 6.2 do plano).
--
-- Por que VARBINARY e não VARCHAR: VARCHAR possui charset e collation, e o
-- MySQL aplicaria conversão de codificação sobre os bytes. Texto cifrado é
-- sequência arbitrária de bytes, não texto - a conversão o corromperia em
-- silêncio, e a corrupção só apareceria na hora de decifrar.
--
-- Cada campo cifrado ocupa três colunas, porque o AES-GCM produz três
-- valores que precisam ser guardados juntos:
--
--     _cif    o texto cifrado
--     _nonce  12 bytes (96 bits), único por operação de cifragem
--     _tag    16 bytes (128 bits), autentica o conteúdo contra adulteração

CREATE TABLE hospede (
    id                  INT UNSIGNED NOT NULL AUTO_INCREMENT,

    -- Nome e documento são obrigatórios para identificar o hóspede.
    nome_cif            VARBINARY(512) NOT NULL,
    nome_nonce          VARBINARY(12) NOT NULL,
    nome_tag            VARBINARY(16) NOT NULL,

    documento_cif       VARBINARY(256) NOT NULL,
    documento_nonce     VARBINARY(12) NOT NULL,
    documento_tag       VARBINARY(16) NOT NULL,

    -- Telefone e e-mail são opcionais. As três colunas de cada um são
    -- nulas em conjunto: ou o campo existe inteiro, ou não existe.
    telefone_cif        VARBINARY(256) NULL,
    telefone_nonce      VARBINARY(12) NULL,
    telefone_tag        VARBINARY(16) NULL,

    email_cif           VARBINARY(512) NULL,
    email_nonce         VARBINARY(12) NULL,
    email_tag           VARBINARY(16) NULL,

    -- Campos de busca: HMAC-SHA256 do valor normalizado, com chave de
    -- índice separada da chave de cifragem.
    --
    -- O AES-GCM é não determinístico por projeto - cifrar duas vezes o
    -- mesmo e-mail produz bytes diferentes, o que impede WHERE e UNIQUE
    -- sobre a coluna cifrada. O HMAC é determinístico, permite os dois, e
    -- não é reversível.
    --
    -- Hash simples não serviria: sem chave, um CPF tem apenas 10^11
    -- possibilidades e é quebrável por força bruta em segundos.
    --
    -- 32 bytes é a saída exata do SHA-256.
    email_busca         VARBINARY(32) NULL,
    documento_busca     VARBINARY(32) NULL,

    -- Permite migrar de algoritmo ou de parâmetros sem decifrar tudo de
    -- uma vez: cada registro sabe com qual versão foi cifrado.
    cripto_versao       TINYINT UNSIGNED NOT NULL DEFAULT 1,

    criado_em           DATETIME NOT NULL,
    alterado_em         DATETIME NOT NULL,

    PRIMARY KEY (id),

    -- Impedem o mesmo hóspede cadastrado duas vezes. A restrição funciona
    -- sobre o HMAC justamente porque ele é determinístico.
    UNIQUE KEY uq_hospede_email_busca (email_busca),
    UNIQUE KEY uq_hospede_documento_busca (documento_busca)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ===========================================================================
-- 3. usuario
-- ===========================================================================
-- Contas da equipe da hospedaria e dos hóspedes com acesso às próprias
-- reservas.

CREATE TABLE usuario (
    id                  INT UNSIGNED NOT NULL AUTO_INCREMENT,
    login               VARCHAR(50) NOT NULL,
    nome                VARCHAR(100) NOT NULL,

    -- String codificada completa do Argon2id, no formato
    -- $argon2id$v=19$m=65536,t=3,p=4$<sal>$<hash>
    --
    -- Os parâmetros vão embutidos, o que permite recalibrar o custo no
    -- futuro sem invalidar as senhas já cadastradas: cada hash carrega os
    -- parâmetros com que foi gerado. 255 caracteres acomodam folgadamente
    -- qualquer calibração razoável.
    senha_hash          VARCHAR(255) NOT NULL,

    perfil              ENUM('administrador','recepcionista','hospede') NOT NULL,

    -- Preenchido somente quando perfil = 'hospede', ligando a conta ao
    -- registro de hóspede cujas reservas ela pode consultar.
    hospede_id          INT UNSIGNED NULL,

    ativo               BOOLEAN NOT NULL DEFAULT TRUE,
    ultimo_acesso       DATETIME NULL,
    criado_em           DATETIME NOT NULL,
    alterado_em         DATETIME NOT NULL,

    PRIMARY KEY (id),
    UNIQUE KEY uq_usuario_login (login),

    -- RESTRICT: não se apaga um hóspede que tem conta de acesso. A conta
    -- precisa ser removida antes, de forma deliberada.
    CONSTRAINT fk_usuario_hospede
        FOREIGN KEY (hospede_id) REFERENCES hospede (id)
        ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ===========================================================================
-- 4. tarifa
-- ===========================================================================
-- Valor da diária por chalé e por período, permitindo alta e baixa
-- temporada. O cálculo do valor total de uma reserva percorre as tarifas
-- que cobrem o período, tratando a virada de temporada.

CREATE TABLE tarifa (
    id                  INT UNSIGNED NOT NULL AUTO_INCREMENT,
    chale_id            INT UNSIGNED NOT NULL,
    data_inicio         DATE NOT NULL,
    data_fim            DATE NOT NULL,

    -- DECIMAL, nunca FLOAT: ponto flutuante binário não representa valores
    -- decimais com exatidão, e erro de arredondamento em dinheiro é
    -- inaceitável. DECIMAL(10,2) comporta até 99.999.999,99.
    valor_diaria        DECIMAL(10,2) NOT NULL,

    descricao           VARCHAR(100) NULL,
    criado_em           DATETIME NOT NULL,
    alterado_em         DATETIME NOT NULL,

    PRIMARY KEY (id),

    -- RESTRICT: preserva o histórico de preços praticados.
    CONSTRAINT fk_tarifa_chale
        FOREIGN KEY (chale_id) REFERENCES chale (id)
        ON DELETE RESTRICT ON UPDATE CASCADE,

    -- Acelera a busca da tarifa vigente para um chalé em uma data.
    KEY idx_tarifa_periodo (chale_id, data_inicio, data_fim)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ===========================================================================
-- 5. reserva
-- ===========================================================================
-- O coração do sistema. É sobre esta tabela que atuam as triggers que
-- garantem a regra de não-sobreposição - o argumento central do projeto.

CREATE TABLE reserva (
    id                  INT UNSIGNED NOT NULL AUTO_INCREMENT,
    chale_id            INT UNSIGNED NOT NULL,
    hospede_id          INT UNSIGNED NOT NULL,

    -- Intervalo semiaberto: a reserva ocupa de data_entrada (inclusive) até
    -- data_saida (exclusive). Assim a saída de um hóspede libera o chalé
    -- para a entrada de outro no mesmo dia.
    data_entrada        DATE NOT NULL,
    data_saida          DATE NOT NULL,

    num_pessoas         TINYINT UNSIGNED NOT NULL,

    situacao            ENUM('pendente','confirmada','cancelada','concluida')
                        NOT NULL DEFAULT 'pendente',

    valor_total         DECIMAL(10,2) NULL,

    -- -----------------------------------------------------------------------
    -- Pagamento por Pix — Etapa 5, Bloco 7
    --
    -- O sistema NÃO transaciona: o Pix acontece entre o aplicativo do banco
    -- do hóspede e o da pousada. Estas colunas guardam prazos e
    -- declarações. O que a equipe conferiu no extrato vai para a tabela
    -- `pagamento`, que só aceita acréscimo.
    --
    -- Reserva registrada pela equipe deixa todas nulas: segue o fluxo
    -- manual de antes, e o cancelamento automático não a alcança.
    -- -----------------------------------------------------------------------

    -- `sinal` com 7 dias ou mais de antecedência; `integral` com menos.
    modalidade              ENUM('sinal','integral') NULL,

    -- Até quando o hóspede pode informar o primeiro Pix. Calculado PELO
    -- BANCO no INSERT (NOW() + 90 minutos), para que o prazo e a varredura
    -- que o aplica leiam o mesmo relógio. No Bloco 4, dois relógios para
    -- um mesmo prazo foram defeito.
    prazo_pagamento         DATETIME NULL,

    -- Até quando o restante pode ser informado. Só existe com sinal.
    prazo_restante          DATETIME NULL,

    -- Quando o hóspede clicou "Fiz o Pix". É uma DECLARAÇÃO, não um
    -- pagamento: para a contagem dos 90 minutos e põe o pedido na fila de
    -- conferência da equipe.
    pix_informado_em        DATETIME NULL,
    restante_informado_em   DATETIME NULL,

    -- Quando o hóspede marcou "Li e aceito o regulamento". Se um dia ele
    -- contestar uma devolução negada, este registro mostra que a regra foi
    -- lida antes do pedido.
    regulamento_aceito_em   DATETIME NULL,

    cancelado_em            DATETIME NULL,
    motivo_cancelamento     ENUM('prazo_do_pix', 'falta_de_pagamento',
                                 'pedido_do_hospede', 'equipe') NULL,

    -- Campo de texto livre. NÃO gravar dado sensível aqui: esta coluna não
    -- é cifrada e seu conteúdo aparece na interface sem proteção.
    observacoes         TEXT NULL,

    -- Qual usuário criou a reserva. SET NULL ao apagar: desligar um
    -- recepcionista não pode apagar as reservas que ele registrou.
    criado_por          INT UNSIGNED NULL,

    criado_em           DATETIME NOT NULL,
    alterado_em         DATETIME NOT NULL,

    PRIMARY KEY (id),

    -- RESTRICT nos dois: não se apaga chalé nem hóspede com reserva.
    CONSTRAINT fk_reserva_chale
        FOREIGN KEY (chale_id) REFERENCES chale (id)
        ON DELETE RESTRICT ON UPDATE CASCADE,

    CONSTRAINT fk_reserva_hospede
        FOREIGN KEY (hospede_id) REFERENCES hospede (id)
        ON DELETE RESTRICT ON UPDATE CASCADE,

    CONSTRAINT fk_reserva_usuario
        FOREIGN KEY (criado_por) REFERENCES usuario (id)
        ON DELETE SET NULL ON UPDATE CASCADE,

    -- Índice obrigatório do plano. A ordem das colunas não é arbitrária:
    -- a trigger de conflito e a consulta de disponibilidade filtram
    -- primeiro por chalé, depois pelo intervalo de datas, e por último
    -- descartam as reservas canceladas.
    KEY idx_reserva_disponibilidade (chale_id, data_entrada, data_saida, situacao),

    -- A varredura dos pedidos vencidos procura por situação e prazo.
    KEY idx_reserva_prazo_pagamento (situacao, prazo_pagamento),

    -- Pedido feito pelo site tem modalidade E prazo; reserva da equipe não
    -- tem nenhum dos dois. Metade preenchida seria um pedido que nunca
    -- vence, ou um prazo sem regra que o explique.
    CONSTRAINT ck_reserva_modalidade_e_prazo
        CHECK ((modalidade IS NULL) = (prazo_pagamento IS NULL)),

    -- O restante só existe quando houve sinal.
    CONSTRAINT ck_reserva_restante_so_com_sinal
        CHECK (prazo_restante IS NULL OR modalidade = 'sinal')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ===========================================================================
-- 6. log_auditoria
-- ===========================================================================
-- Registro de acessos e de operações críticas.
--
-- NUNCA contém dado pessoal nem conteúdo de campo cifrado. Um log que
-- copiasse valores anularia a cifragem: bastaria ler o log em vez da
-- tabela protegida.
--
-- BIGINT na chave primária porque esta tabela cresce a cada operação do
-- sistema, enquanto as demais crescem a cada reserva ou hóspede.

CREATE TABLE log_auditoria (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    -- Nulo em evento sem sessão, como uma tentativa de login que falhou
    -- antes de identificar o usuário.
    --
    -- SET NULL ao apagar, e esta é a decisão mais importante da tabela: se
    -- apagar o usuário apagasse seus registros, bastaria remover a conta
    -- para fazer sumir o rastro das operações. O evento permanece, apenas
    -- sem o vínculo.
    usuario_id          INT UNSIGNED NULL,

    -- INSERT, UPDATE, DELETE, LOGIN_OK, LOGIN_FALHA, ACESSO_NEGADO.
    -- VARCHAR em vez de ENUM porque ações novas aparecerão ao longo do
    -- desenvolvimento, e acrescentar valor a um ENUM exige ALTER TABLE.
    acao                VARCHAR(30) NOT NULL,

    tabela_afetada      VARCHAR(50) NULL,
    registro_id         INT UNSIGNED NULL,
    data_hora           DATETIME NOT NULL,

    -- Apenas descrição genérica. Nenhum valor de campo, nenhum dado
    -- pessoal, nada que tenha vindo de coluna cifrada.
    detalhe             VARCHAR(255) NULL,

    PRIMARY KEY (id),

    CONSTRAINT fk_log_usuario
        FOREIGN KEY (usuario_id) REFERENCES usuario (id)
        ON DELETE SET NULL ON UPDATE CASCADE,

    -- Consultas de auditoria perguntam "o que aconteceu neste período" e
    -- "o que este usuário fez".
    KEY idx_log_data_hora (data_hora),
    KEY idx_log_usuario (usuario_id, data_hora)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ===========================================================================
-- 7. contato
-- ===========================================================================
-- Pedidos vindos do formulário público. É a única tabela em que a face
-- pública escreve.
--
-- Os dados aqui NÃO são cifrados: são fornecidos voluntariamente por quem
-- busca orçamento e ainda não é hóspede. Decisão registrada como aberta no
-- plano - se a responsável entender que devem ser protegidos, aplica-se o
-- mesmo mecanismo da tabela hospede.

CREATE TABLE contato (
    id                      INT UNSIGNED NOT NULL AUTO_INCREMENT,

    -- --------------------------------------------------------------------
    -- Dados pessoais, cifrados com AES-256-GCM
    --
    -- A primeira versão desta tabela guardava nome, e-mail, telefone e
    -- mensagem em texto simples, com o argumento de que um pedido de
    -- contato é dado de menor sensibilidade que um cadastro de hóspede.
    -- O argumento não se sustenta: é a mesma pessoa, com o mesmo nome e o
    -- mesmo telefone, e quem obtiver acesso de leitura ao banco não vai
    -- achar a distinção relevante.
    --
    -- Decidido em 06/11: tudo o que a hospedaria obtém de uma pessoa
    -- nasce cifrado, sem exceção por categoria.
    --
    -- O ciphertext do AES-GCM tem o mesmo tamanho do texto claro, por
    -- isso os limites em bytes abaixo são os limites de entrada do
    -- formulário, e a aplicação os impõe antes de cifrar.
    -- --------------------------------------------------------------------
    nome_cif                VARBINARY(512) NOT NULL,
    nome_nonce              VARBINARY(12) NOT NULL,
    nome_tag                VARBINARY(16) NOT NULL,

    email_cif               VARBINARY(512) NOT NULL,
    email_nonce             VARBINARY(12) NOT NULL,
    email_tag               VARBINARY(16) NOT NULL,

    telefone_cif            VARBINARY(256) NULL,
    telefone_nonce          VARBINARY(12) NULL,
    telefone_tag            VARBINARY(16) NULL,

    mensagem_cif            VARBINARY(4096) NULL,
    mensagem_nonce          VARBINARY(12) NULL,
    mensagem_tag            VARBINARY(16) NULL,

    -- Índice HMAC-SHA256 do e-mail, para a recepção ver quantas vezes a
    -- mesma pessoa já escreveu. NÃO é único: vários pedidos da mesma
    -- pessoa são legítimos, e é justamente contá-los que interessa.
    -- O campo cifrado não serve para isso: o AES-GCM é não determinístico,
    -- e cifrar o mesmo e-mail duas vezes produz bytes diferentes.
    email_busca             VARBINARY(32) NOT NULL,

    cripto_versao           TINYINT UNSIGNED NOT NULL DEFAULT 1,

    -- --------------------------------------------------------------------
    -- Dados não pessoais
    -- --------------------------------------------------------------------

    -- SET NULL: o pedido de contato continua válido mesmo que o chalé de
    -- interesse deixe de existir.
    chale_interesse_id      INT UNSIGNED NULL,

    data_entrada_desejada   DATE NULL,
    data_saida_desejada     DATE NULL,
    origem                  VARCHAR(50) NULL,

    -- VARBINARY(16) acomoda tanto IPv4 (4 bytes) quanto IPv6 (16 bytes) na
    -- forma binária devolvida por INET6_ATON. Guardar como texto ocuparia
    -- mais espaço e dificultaria a comparação.
    ip_origem               VARBINARY(16) NULL,

    situacao                ENUM('novo','em_atendimento','respondido',
                                 'convertido','descartado')
                            NOT NULL DEFAULT 'novo',

    -- Preenchido quando a recepção converte o pedido num cadastro de
    -- hóspede. SET NULL, e não CASCADE: o contato é o registro de que
    -- alguém pediu informação, e sobrevive à remoção do cadastro que ele
    -- originou. Apagá-lo junto destruiria a resposta a "de onde vieram os
    -- hóspedes deste verão?".
    hospede_id              INT UNSIGNED NULL,

    criado_em               DATETIME NOT NULL,

    PRIMARY KEY (id),

    CONSTRAINT fk_contato_chale
        FOREIGN KEY (chale_interesse_id) REFERENCES chale (id)
        ON DELETE SET NULL ON UPDATE CASCADE,

    CONSTRAINT fk_contato_hospede
        FOREIGN KEY (hospede_id) REFERENCES hospede (id)
        ON DELETE SET NULL ON UPDATE CASCADE,

    -- A tela de atendimento lista os contatos novos, mais recentes antes.
    KEY idx_contato_situacao (situacao, criado_em),

    -- A limitação de taxa conta envios por origem numa janela de tempo.
    KEY idx_contato_ip (ip_origem, criado_em),

    -- Quantas vezes esta pessoa já escreveu.
    KEY idx_contato_email (email_busca, criado_em)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ===========================================================================
-- TOKEN DE ACESSO
-- ===========================================================================
-- Prova de posse de um endereço de e-mail. O sistema cria a conta inativa,
-- gera um token, e só a abertura do link contido na mensagem a ativa.
--
-- Avisar não bastaria: se o link aparecesse na tela do cadastro, bastaria
-- digitar o endereço de outra pessoa para criar conta no nome dela.
--
-- --------------------------------------------------------------------------
-- Por que a coluna guarda o HASH e não o token
--
-- Mesma lógica da senha. Quem obtiver leitura do banco não consegue abrir
-- link nenhum: teria o hash, e o hash não volta ao token.
--
-- E por que SHA-256 aqui, enquanto a senha usa Argon2id: o token vem de
-- `secrets.token_urlsafe(32)` e carrega 256 bits de aleatoriedade. Não há
-- dicionário nem força bruta contra isso. Argon2id existe para proteger
-- segredos de BAIXA entropia — senhas que pessoas escolhem e repetem — e
-- o custo dele (0,658 s) seria desperdiçado num valor que já é
-- impossível de adivinhar.
-- --------------------------------------------------------------------------

CREATE TABLE token_acesso (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,

    usuario_id      INT UNSIGNED NOT NULL,

    -- SHA-256 do token, 32 bytes exatos.
    token_hash      VARBINARY(32) NOT NULL,

    tipo            ENUM('confirmacao','recuperacao') NOT NULL,

    expira_em       DATETIME NOT NULL,

    -- Preenchido no consumo. Uso único: um link já aberto não abre de
    -- novo, mesmo dentro do prazo. Sem isso, um link que vazasse depois de
    -- usado continuaria valendo.
    consumido_em    DATETIME NULL,

    criado_em       DATETIME NOT NULL,

    PRIMARY KEY (id),

    -- Único: dois tokens com o mesmo hash seriam o mesmo token, e a
    -- consulta de consumo precisa achar no máximo uma linha.
    UNIQUE KEY uq_token_hash (token_hash),

    -- CASCADE, ao contrário de `contato.hospede_id`, que é SET NULL. Um
    -- token é autorização pendente, não histórico: apagada a conta, o
    -- token não autoriza mais nada e guardá-lo seria guardar lixo com
    -- poder.
    CONSTRAINT fk_token_usuario
        FOREIGN KEY (usuario_id) REFERENCES usuario (id)
        ON DELETE CASCADE ON UPDATE CASCADE,

    -- Invalidar os pendentes de uma conta quando um token novo é emitido.
    KEY idx_token_usuario (usuario_id, tipo, consumido_em),

    -- Limpeza dos vencidos.
    KEY idx_token_expiracao (expira_em)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ===========================================================================
-- PAGAMENTO — o livro dos Pix conferidos (Etapa 5, Bloco 7)
-- ===========================================================================
-- Uma linha por fato que alguém da equipe conferiu no extrato: o sinal, o
-- restante, o pagamento integral, ou a devolução — esta com valor negativo.
--
-- --------------------------------------------------------------------------
-- Só aceita acréscimo
--
-- Os gatilhos recusam UPDATE e DELETE, inclusive de quem tem acesso direto
-- ao banco, e a conta da aplicação recebe apenas SELECT e INSERT. Correção
-- é lançamento novo, nunca reescrita.
--
-- É o mesmo desenho do `log_auditoria`, e pela mesma razão, aqui mais
-- forte: o dinheiro não passa pelo sistema, e este livro é a única prova
-- de quem afirmou ter visto o Pix no extrato, e quando.
--
-- A soma mensal do painel do administrador é uma soma sobre esta tabela,
-- pela data do registro. A devolução entra no mês em que acontece.
-- --------------------------------------------------------------------------

CREATE TABLE pagamento (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,

    reserva_id      INT UNSIGNED NOT NULL,

    tipo            ENUM('sinal','restante','integral','devolucao') NOT NULL,

    -- Positivo na entrada, negativo na devolução. A soma do mês é a soma
    -- simples da coluna, sem regra de sinal espalhada pelas consultas.
    valor           DECIMAL(10,2) NOT NULL,

    -- Quem conferiu. NOT NULL e RESTRICT, ao contrário do SET NULL de
    -- `reserva.criado_por`: aqui o autor é a parte do registro que lhe dá
    -- valor. Conta de equipe se desativa, não se apaga.
    registrado_por  INT UNSIGNED NOT NULL,

    -- Preenchido pelo gatilho, nunca pela aplicação.
    registrado_em   DATETIME NOT NULL,

    PRIMARY KEY (id),

    -- Um lançamento de cada tipo por reserva. Um clique duplo em "Pagou 50%"
    -- não conta o sinal duas vezes.
    UNIQUE KEY uq_pagamento_reserva_tipo (reserva_id, tipo),

    -- A soma por mês.
    KEY idx_pagamento_data (registrado_em),

    -- RESTRICT nos dois sentidos: não se apaga reserva com pagamento, e o
    -- identificador referenciado não muda por baixo do lançamento. Uma
    -- ação em cascata alteraria a linha SEM disparar os gatilhos — o MySQL
    -- não os executa em ações de chave estrangeira.
    CONSTRAINT fk_pagamento_reserva
        FOREIGN KEY (reserva_id) REFERENCES reserva (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,

    CONSTRAINT fk_pagamento_usuario
        FOREIGN KEY (registrado_por) REFERENCES usuario (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,

    CONSTRAINT ck_pagamento_sinal_do_valor
        CHECK ((tipo = 'devolucao' AND valor < 0)
            OR (tipo <> 'devolucao' AND valor > 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ===========================================================================
-- ACOMPANHANTE — quem vai se hospedar junto com o titular (Etapa 5, Bloco 15)
-- ===========================================================================
-- O titular da reserva é o hóspede que a pediu e conta como uma das
-- pessoas. Uma reserva de 4 pessoas tem, portanto, 3 acompanhantes.
--
-- Meios de hospedagem registram TODOS os hóspedes na FNRH, e não só quem
-- reservou; é essa a base para guardar o dado de quem não tem conta.
--
-- --------------------------------------------------------------------------
-- Cifrado, como `hospede` e `contato`
--
-- É dado pessoal de quem nem se cadastrou, e por isso nasce cifrado com
-- AES-256-GCM, sem exceção por categoria (decisão de 06/11).
--
-- A diferença está no dado adicional autenticado. `hospede` e `contato`
-- usam o identificador da linha, que só existe depois do INSERT — e por
-- isso gravam em duas fases, INSERT e depois UPDATE. Aqui a aplicação
-- tem só SELECT e INSERT, e a etiqueta é `acompanhante:<reserva>:<ordem>`,
-- conhecida ANTES do INSERT. A unicidade de (reserva_id, ordem) garante
-- que a etiqueta não se repete, e mover um bloco cifrado para outra
-- linha faz a verificação de integridade falhar.
--
-- --------------------------------------------------------------------------
-- Só acréscimo
--
-- Não há edição pelo site depois do pedido. A conta da aplicação recebe
-- SELECT e INSERT; a correção de um nome digitado errado é feita pela
-- recepção no registro de chegada.
-- --------------------------------------------------------------------------

CREATE TABLE acompanhante (
    id                  INT UNSIGNED NOT NULL AUTO_INCREMENT,

    reserva_id          INT UNSIGNED NOT NULL,

    -- Posição na lista, de 1 a num_pessoas - 1. Entra na etiqueta da
    -- cifra; o gatilho confere o limite.
    ordem               TINYINT UNSIGNED NOT NULL,

    -- O nome tem até 120 caracteres na aplicação: 480 bytes no pior caso
    -- de UTF-8, dentro dos 512.
    nome_cif            VARBINARY(512) NOT NULL,
    nome_nonce          VARBINARY(12) NOT NULL,
    nome_tag            VARBINARY(16) NOT NULL,

    -- Anulável: menor de idade pode não ter documento. Os três juntos.
    documento_cif       VARBINARY(64) NULL,
    documento_nonce     VARBINARY(12) NULL,
    documento_tag       VARBINARY(16) NULL,

    menor_de_idade      BOOLEAN NOT NULL DEFAULT FALSE,

    cripto_versao       TINYINT UNSIGNED NOT NULL DEFAULT 1,

    -- Preenchido pelo gatilho.
    criado_em           DATETIME NOT NULL,

    PRIMARY KEY (id),

    -- A etiqueta da cifra não se repete, e a lista tem uma ordem estável.
    UNIQUE KEY uq_acompanhante_reserva_ordem (reserva_id, ordem),

    -- RESTRICT nos dois sentidos, como `pagamento`: quem se hospedou é
    -- parte do histórico da reserva, e uma ação em cascata alteraria a
    -- linha sem disparar gatilho.
    CONSTRAINT fk_acompanhante_reserva
        FOREIGN KEY (reserva_id) REFERENCES reserva (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,

    CONSTRAINT ck_acompanhante_ordem
        CHECK (ordem >= 1),

    -- Adulto tem documento; menor pode não ter.
    CONSTRAINT ck_acompanhante_documento_ou_menor
        CHECK (documento_cif IS NOT NULL OR menor_de_idade = TRUE),

    -- As três colunas do documento andam juntas: metade preenchida é um
    -- bloco que nunca se decifra.
    CONSTRAINT ck_acompanhante_documento_inteiro
        CHECK ((documento_cif IS NULL) = (documento_nonce IS NULL)
           AND (documento_cif IS NULL) = (documento_tag IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
