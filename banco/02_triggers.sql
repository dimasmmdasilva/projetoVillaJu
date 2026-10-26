-- ---------------------------------------------------------------------------
-- VillaJu - triggers
--
-- Este arquivo contém o argumento central do projeto: a regra de
-- não-sobreposição de reservas mora AQUI, no banco, e não na aplicação.
--
-- Validação feita apenas em Python falha em três situações:
--
--   1. Concorrência - duas requisições chegam ao mesmo tempo, as duas
--      consultam, as duas não encontram conflito, as duas gravam.
--   2. Acesso direto - alguém opera o banco pelo Workbench ou por script.
--   3. Esquecimento - um caminho de código novo não chama a validação.
--
-- A trigger cobre as três, porque o banco é o último portão antes do dado
-- gravado, e não existe caminho que o contorne.
--
-- A aplicação também valida antes de tentar gravar, mas apenas para dar
-- mensagem amigável ao usuário. A garantia está neste arquivo.
--
-- ---------------------------------------------------------------------------
-- Como executar
--
--     mysql -u root -p villaju -e "SOURCE banco/02_triggers.sql"
--
-- Assim como 01_schema.sql, este script não seleciona banco: quem decide é
-- a conexão. A suíte automatizada o aplica ao banco villaju_teste.
--
-- ---------------------------------------------------------------------------
-- Sobre DELIMITER
--
-- O corpo de uma trigger contém pontos e vírgula. Sem trocar o delimitador,
-- o cliente encerraria o comando no primeiro deles e enviaria ao servidor
-- um pedaço de trigger, que não é SQL válido. DELIMITER é instrução do
-- cliente, não do servidor - o conftest.py da suíte de testes a interpreta.
--
-- ---------------------------------------------------------------------------
-- Sobre o autor da operação
--
-- Uma trigger de MySQL não conhece o usuário da aplicação, apenas o usuário
-- do banco - que é sempre villaju_app. Para registrar QUEM fez a operação,
-- a aplicação define uma variável de sessão logo após abrir a conexão:
--
--     SET @app_usuario_id = ?
--
-- e as triggers de auditoria a leem. Se estiver nula, o log grava NULL: o
-- evento fica registrado de qualquer forma, apenas sem o vínculo.
--
-- ---------------------------------------------------------------------------
-- Inventário - 17 triggers em 5 grupos
--
--   1. Regra de reserva          2 (antes de inserir, antes de alterar)
--   2. Auditoria de reserva      3 (depois de inserir, alterar, excluir)
--   3. Auditoria de hóspede      3 (depois de inserir, alterar, excluir)
--   4. Carimbo de tempo          8 (chale, tarifa, usuario, hospede x2;
--                                   contato x1, que só tem criado_em)
--   5. Integridade de perfil     1 (usuario, antes de inserir)
--
-- O requisito acadêmico pede no mínimo 5 grupos. O carimbo de tempo poderia
-- ser DEFAULT CURRENT_TIMESTAMP na definição da coluna, mas o plano escolhe
-- trigger explícita para que o comportamento fique visível no script, e não
-- escondido na declaração.
-- ---------------------------------------------------------------------------

DELIMITER $$

-- ===========================================================================
-- GRUPO 1 - Regra de reserva
-- ===========================================================================

DROP TRIGGER IF EXISTS trg_reserva_antes_inserir$$

CREATE TRIGGER trg_reserva_antes_inserir
BEFORE INSERT ON reserva
FOR EACH ROW
BEGIN
    DECLARE v_capacidade TINYINT UNSIGNED DEFAULT NULL;
    DECLARE v_conflito INT DEFAULT 0;

    -- --- 1. Coerência de datas ---------------------------------------------
    -- Estritamente posterior: uma reserva de zero noites não existe.
    IF NEW.data_saida <= NEW.data_entrada THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'A data de saída deve ser posterior à data de entrada.';
    END IF;

    -- --- 2. Capacidade do chalé --------------------------------------------
    SELECT capacidade INTO v_capacidade
    FROM chale
    WHERE id = NEW.chale_id;

    IF v_capacidade IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Chalé inexistente.';
    END IF;

    IF NEW.num_pessoas < 1 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'A reserva precisa ter ao menos uma pessoa.';
    END IF;

    IF NEW.num_pessoas > v_capacidade THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Número de pessoas acima da capacidade do chalé.';
    END IF;

    -- --- 3. Não-sobreposição -----------------------------------------------
    -- Só reserva ativa ocupa o chalé. Uma reserva cancelada ou concluída
    -- não impede nova reserva no mesmo período.
    IF NEW.situacao IN ('pendente', 'confirmada') THEN

        -- Intervalo SEMIABERTO: ocupa de data_entrada (inclusive) até
        -- data_saida (exclusive). Duas reservas se sobrepõem quando cada
        -- uma começa antes de a outra terminar:
        --
        --     NEW.data_entrada < data_saida  AND  NEW.data_saida > data_entrada
        --
        -- Com isso, sair dia 10 e outro hóspede entrar dia 10 é permitido -
        -- que é como a hospedaria opera. Se a comparação usasse <= e >=, o
        -- chalé ficaria bloqueado um dia inteiro sem necessidade.
        --
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
    END IF;

    -- --- 4. Carimbo de tempo -----------------------------------------------
    -- Fica aqui, e não em trigger separada, porque seria o mesmo evento
    -- sobre a mesma tabela.
    SET NEW.criado_em = NOW();
    SET NEW.alterado_em = NOW();
END$$


DROP TRIGGER IF EXISTS trg_reserva_antes_alterar$$

CREATE TRIGGER trg_reserva_antes_alterar
BEFORE UPDATE ON reserva
FOR EACH ROW
BEGIN
    DECLARE v_capacidade TINYINT UNSIGNED DEFAULT NULL;
    DECLARE v_conflito INT DEFAULT 0;

    IF NEW.data_saida <= NEW.data_entrada THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'A data de saída deve ser posterior à data de entrada.';
    END IF;

    SELECT capacidade INTO v_capacidade
    FROM chale
    WHERE id = NEW.chale_id;

    IF v_capacidade IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Chalé inexistente.';
    END IF;

    IF NEW.num_pessoas < 1 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'A reserva precisa ter ao menos uma pessoa.';
    END IF;

    IF NEW.num_pessoas > v_capacidade THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Número de pessoas acima da capacidade do chalé.';
    END IF;

    IF NEW.situacao IN ('pendente', 'confirmada') THEN

        -- A diferença para o INSERT está na linha do COALESCE: no UPDATE a
        -- própria reserva JÁ ESTÁ na tabela e seria encontrada pela
        -- consulta, fazendo-a conflitar consigo mesma. Sem esta cláusula,
        -- alterar apenas a observação de uma reserva seria rejeitado.
        --
        -- Este é o risco registrado no plano do projeto, e o caso de teste
        -- 11 existe exatamente para cobri-lo.
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
    END IF;

    -- A data de criação é imutável: preservá-la impede que uma alteração,
    -- acidental ou deliberada, reescreva quando o registro nasceu.
    SET NEW.criado_em = OLD.criado_em;
    SET NEW.alterado_em = NOW();
END$$


-- ===========================================================================
-- GRUPO 2 - Auditoria de reserva
-- ===========================================================================
-- Registram O QUE aconteceu, nunca COM QUAIS VALORES. O log é consultável
-- por quem tem acesso de leitura; se copiasse conteúdo de campo, viraria
-- uma segunda via dos dados, fora da proteção da tabela original.
--
-- A verificação de existência do usuário antes de gravar evita que uma
-- variável de sessão apontando para usuário inexistente derrube a operação
-- por violação de chave estrangeira. Auditoria nunca pode impedir o
-- trabalho que ela observa.

DROP TRIGGER IF EXISTS trg_reserva_depois_inserir$$

CREATE TRIGGER trg_reserva_depois_inserir
AFTER INSERT ON reserva
FOR EACH ROW
BEGIN
    DECLARE v_usuario INT UNSIGNED DEFAULT NULL;

    SET v_usuario = NULLIF(@app_usuario_id, 0);
    IF v_usuario IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM usuario WHERE id = v_usuario) THEN
        SET v_usuario = NULL;
    END IF;

    INSERT INTO log_auditoria
        (usuario_id, acao, tabela_afetada, registro_id, data_hora, detalhe)
    VALUES
        (v_usuario, 'INSERT', 'reserva', NEW.id, NOW(), 'Reserva criada');
END$$


DROP TRIGGER IF EXISTS trg_reserva_depois_alterar$$

CREATE TRIGGER trg_reserva_depois_alterar
AFTER UPDATE ON reserva
FOR EACH ROW
BEGIN
    DECLARE v_usuario INT UNSIGNED DEFAULT NULL;
    DECLARE v_detalhe VARCHAR(255) DEFAULT 'Reserva alterada';

    SET v_usuario = NULLIF(@app_usuario_id, 0);
    IF v_usuario IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM usuario WHERE id = v_usuario) THEN
        SET v_usuario = NULL;
    END IF;

    -- A situação é a única informação que o log registra além da ação,
    -- porque ela é categoria fechada e não dado pessoal. Saber que uma
    -- reserva foi cancelada é justamente o que a auditoria precisa mostrar.
    IF NEW.situacao <> OLD.situacao THEN
        SET v_detalhe = CONCAT('Situação alterada de ', OLD.situacao,
                               ' para ', NEW.situacao);
    END IF;

    INSERT INTO log_auditoria
        (usuario_id, acao, tabela_afetada, registro_id, data_hora, detalhe)
    VALUES
        (v_usuario, 'UPDATE', 'reserva', NEW.id, NOW(), v_detalhe);
END$$


DROP TRIGGER IF EXISTS trg_reserva_depois_excluir$$

CREATE TRIGGER trg_reserva_depois_excluir
AFTER DELETE ON reserva
FOR EACH ROW
BEGIN
    DECLARE v_usuario INT UNSIGNED DEFAULT NULL;

    SET v_usuario = NULLIF(@app_usuario_id, 0);
    IF v_usuario IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM usuario WHERE id = v_usuario) THEN
        SET v_usuario = NULL;
    END IF;

    INSERT INTO log_auditoria
        (usuario_id, acao, tabela_afetada, registro_id, data_hora, detalhe)
    VALUES
        (v_usuario, 'DELETE', 'reserva', OLD.id, NOW(), 'Reserva excluída');
END$$


-- ===========================================================================
-- GRUPO 3 - Auditoria de hóspede
-- ===========================================================================
-- Mesma função do grupo anterior, com uma regra adicional e absoluta:
--
--     JAMAIS copiar para o log o conteúdo das colunas _cif, _nonce, _tag
--     ou dos campos _busca.
--
-- Copiar o ciphertext seria inútil e perigoso: inútil porque ninguém o lê
-- sem a chave, perigoso porque multiplica as cópias do dado protegido.
-- Copiar os campos _busca seria pior: o HMAC é determinístico, então duas
-- linhas com o mesmo valor denunciariam que se trata do mesmo hóspede.
--
-- O log guarda apenas o id, que já é público dentro do sistema.

DROP TRIGGER IF EXISTS trg_hospede_depois_inserir$$

CREATE TRIGGER trg_hospede_depois_inserir
AFTER INSERT ON hospede
FOR EACH ROW
BEGIN
    DECLARE v_usuario INT UNSIGNED DEFAULT NULL;

    SET v_usuario = NULLIF(@app_usuario_id, 0);
    IF v_usuario IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM usuario WHERE id = v_usuario) THEN
        SET v_usuario = NULL;
    END IF;

    INSERT INTO log_auditoria
        (usuario_id, acao, tabela_afetada, registro_id, data_hora, detalhe)
    VALUES
        (v_usuario, 'INSERT', 'hospede', NEW.id, NOW(), 'Hóspede cadastrado');
END$$


DROP TRIGGER IF EXISTS trg_hospede_depois_alterar$$

CREATE TRIGGER trg_hospede_depois_alterar
AFTER UPDATE ON hospede
FOR EACH ROW
BEGIN
    DECLARE v_usuario INT UNSIGNED DEFAULT NULL;

    SET v_usuario = NULLIF(@app_usuario_id, 0);
    IF v_usuario IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM usuario WHERE id = v_usuario) THEN
        SET v_usuario = NULL;
    END IF;

    INSERT INTO log_auditoria
        (usuario_id, acao, tabela_afetada, registro_id, data_hora, detalhe)
    VALUES
        (v_usuario, 'UPDATE', 'hospede', NEW.id, NOW(), 'Hóspede alterado');
END$$


DROP TRIGGER IF EXISTS trg_hospede_depois_excluir$$

CREATE TRIGGER trg_hospede_depois_excluir
AFTER DELETE ON hospede
FOR EACH ROW
BEGIN
    DECLARE v_usuario INT UNSIGNED DEFAULT NULL;

    SET v_usuario = NULLIF(@app_usuario_id, 0);
    IF v_usuario IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM usuario WHERE id = v_usuario) THEN
        SET v_usuario = NULL;
    END IF;

    INSERT INTO log_auditoria
        (usuario_id, acao, tabela_afetada, registro_id, data_hora, detalhe)
    VALUES
        (v_usuario, 'DELETE', 'hospede', OLD.id, NOW(), 'Hóspede excluído');
END$$


-- ===========================================================================
-- GRUPO 4 - Carimbo de tempo
-- ===========================================================================
-- Preenchem criado_em e alterado_em nas tabelas que não têm trigger de
-- regra. A reserva não aparece aqui porque seu carimbo já é feito pelas
-- triggers do grupo 1.
--
-- Em todas as alterações, criado_em é preservado a partir de OLD: a data de
-- nascimento de um registro não muda.

DROP TRIGGER IF EXISTS trg_chale_antes_inserir$$
CREATE TRIGGER trg_chale_antes_inserir
BEFORE INSERT ON chale
FOR EACH ROW
BEGIN
    SET NEW.criado_em = NOW();
    SET NEW.alterado_em = NOW();
END$$

DROP TRIGGER IF EXISTS trg_chale_antes_alterar$$
CREATE TRIGGER trg_chale_antes_alterar
BEFORE UPDATE ON chale
FOR EACH ROW
BEGIN
    SET NEW.criado_em = OLD.criado_em;
    SET NEW.alterado_em = NOW();
END$$


DROP TRIGGER IF EXISTS trg_tarifa_antes_inserir$$
CREATE TRIGGER trg_tarifa_antes_inserir
BEFORE INSERT ON tarifa
FOR EACH ROW
BEGIN
    -- A mesma coerência de datas exigida da reserva: um período de tarifa
    -- que termina antes de começar não faz sentido.
    IF NEW.data_fim < NEW.data_inicio THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'A data final da tarifa não pode ser anterior à inicial.';
    END IF;

    IF NEW.valor_diaria < 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'O valor da diária não pode ser negativo.';
    END IF;

    SET NEW.criado_em = NOW();
    SET NEW.alterado_em = NOW();
END$$

DROP TRIGGER IF EXISTS trg_tarifa_antes_alterar$$
CREATE TRIGGER trg_tarifa_antes_alterar
BEFORE UPDATE ON tarifa
FOR EACH ROW
BEGIN
    IF NEW.data_fim < NEW.data_inicio THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'A data final da tarifa não pode ser anterior à inicial.';
    END IF;

    IF NEW.valor_diaria < 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'O valor da diária não pode ser negativo.';
    END IF;

    SET NEW.criado_em = OLD.criado_em;
    SET NEW.alterado_em = NOW();
END$$


DROP TRIGGER IF EXISTS trg_hospede_antes_inserir$$
CREATE TRIGGER trg_hospede_antes_inserir
BEFORE INSERT ON hospede
FOR EACH ROW
BEGIN
    SET NEW.criado_em = NOW();
    SET NEW.alterado_em = NOW();
END$$

DROP TRIGGER IF EXISTS trg_hospede_antes_alterar$$
CREATE TRIGGER trg_hospede_antes_alterar
BEFORE UPDATE ON hospede
FOR EACH ROW
BEGIN
    SET NEW.criado_em = OLD.criado_em;
    SET NEW.alterado_em = NOW();
END$$


DROP TRIGGER IF EXISTS trg_contato_antes_inserir$$
CREATE TRIGGER trg_contato_antes_inserir
BEFORE INSERT ON contato
FOR EACH ROW
BEGIN
    -- A tabela contato tem apenas criado_em: um pedido de contato não é
    -- editado, apenas atendido, e a mudança de situação é registrada pelo
    -- fluxo de atendimento.
    SET NEW.criado_em = NOW();
END$$


-- ===========================================================================
-- GRUPO 5 - Integridade de perfil de usuário
-- ===========================================================================
-- A coluna hospede_id só faz sentido quando o perfil é 'hospede'. Sem esta
-- verificação, um administrador poderia ficar vinculado a um registro de
-- hóspede, e a rota que lista "minhas reservas" passaria a devolver dados
-- de outra pessoa para ele.

DROP TRIGGER IF EXISTS trg_usuario_antes_inserir$$
CREATE TRIGGER trg_usuario_antes_inserir
BEFORE INSERT ON usuario
FOR EACH ROW
BEGIN
    IF NEW.perfil = 'hospede' AND NEW.hospede_id IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Usuário com perfil hospede precisa estar vinculado a um hóspede.';
    END IF;

    IF NEW.perfil <> 'hospede' AND NEW.hospede_id IS NOT NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Apenas usuário com perfil hospede pode ter hospede_id.';
    END IF;

    SET NEW.criado_em = NOW();
    SET NEW.alterado_em = NOW();
END$$

DROP TRIGGER IF EXISTS trg_usuario_antes_alterar$$
CREATE TRIGGER trg_usuario_antes_alterar
BEFORE UPDATE ON usuario
FOR EACH ROW
BEGIN
    IF NEW.perfil = 'hospede' AND NEW.hospede_id IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Usuário com perfil hospede precisa estar vinculado a um hóspede.';
    END IF;

    IF NEW.perfil <> 'hospede' AND NEW.hospede_id IS NOT NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Apenas usuário com perfil hospede pode ter hospede_id.';
    END IF;

    SET NEW.criado_em = OLD.criado_em;
    SET NEW.alterado_em = NOW();
END$$

DELIMITER ;
