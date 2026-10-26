-- ---------------------------------------------------------------------------
-- VillaJu - modelo de dados
--
-- As 7 tabelas do sistema, conforme a seção 5.1 do plano do projeto.
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
    KEY idx_reserva_disponibilidade (chale_id, data_entrada, data_saida, situacao)
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
    nome                    VARCHAR(100) NOT NULL,
    email                   VARCHAR(150) NOT NULL,
    telefone                VARCHAR(30) NULL,
    mensagem                TEXT NULL,

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

    situacao                ENUM('novo','em_atendimento','respondido','descartado')
                            NOT NULL DEFAULT 'novo',

    criado_em               DATETIME NOT NULL,

    PRIMARY KEY (id),

    CONSTRAINT fk_contato_chale
        FOREIGN KEY (chale_interesse_id) REFERENCES chale (id)
        ON DELETE SET NULL ON UPDATE CASCADE,

    -- A tela de atendimento lista os contatos novos, mais recentes antes.
    KEY idx_contato_situacao (situacao, criado_em),

    -- A limitação de taxa conta envios por origem numa janela de tempo.
    KEY idx_contato_ip (ip_origem, criado_em)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
