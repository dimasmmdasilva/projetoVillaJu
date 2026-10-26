-- ---------------------------------------------------------------------------
-- VillaJu - usuário de aplicação com privilégio mínimo
--
-- A aplicação NÃO se conecta como root. Ela usa uma conta que só consegue
-- fazer aquilo de que precisa, e nada além.
--
-- A razão é a contenção de dano. Se a aplicação for comprometida - por uma
-- injeção de SQL que escapou, por uma dependência vulnerável, por uma falha
-- que ainda não conhecemos - o atacante herda exatamente os privilégios
-- dela. Com root, ele apagaria o banco, criaria contas e leria qualquer
-- esquema do servidor. Com esta conta, ele manipula dados das sete tabelas
-- do VillaJu e para por aí.
--
-- ---------------------------------------------------------------------------
-- O que esta conta PODE
--
--   SELECT, INSERT, UPDATE, DELETE   nas seis tabelas de dados
--   SELECT, INSERT                   em log_auditoria
--
-- O que esta conta NÃO PODE
--
--   UPDATE ou DELETE em log_auditoria   <- a decisão mais importante aqui
--   CREATE, ALTER, DROP                 mudança de estrutura
--   GRANT                               conceder privilégio a si ou a outros
--   qualquer acesso a outro banco       information_schema e mysql incluídos
--
-- Sobre o log de auditoria: conceder apenas SELECT e INSERT torna a tabela
-- APPEND-ONLY do ponto de vista da aplicação. Mesmo comprometida, ela pode
-- gravar novos eventos, mas não apagar nem reescrever os que já existem.
-- Sem isso, a primeira coisa que um atacante faria é limpar o próprio
-- rastro - e a auditoria perderia todo o valor justamente quando ela mais
-- importa.
--
-- As triggers continuam conseguindo gravar no log porque executam com o
-- privilégio do DEFINER (o root que as criou), não do usuário que dispara
-- a operação. É por isso que a aplicação consegue gerar auditoria sem ter
-- permissão de escrita direta na tabela.
--
-- ---------------------------------------------------------------------------
-- Como executar
--
-- Este script concede privilégios sobre o banco SELECIONADO na conexão, da
-- mesma forma que 01_schema.sql e 02_triggers.sql. Rode uma vez para o
-- banco real e uma vez para o banco de testes:
--
--     mysql -u root -p villaju       -e "SOURCE banco/03_usuario_app.sql"
--     mysql -u root -p villaju_teste -e "SOURCE banco/03_usuario_app.sql"
--
-- O banco de testes recebe os mesmos privilégios para que a suíte possa
-- VERIFICAR as restrições na prática, conectando como esta conta e
-- comprovando o que ela não consegue fazer. Ler o arquivo de GRANT não
-- prova nada; tentar o DELETE e receber a recusa, sim.
--
-- As tabelas precisam existir antes: rode 01_schema.sql primeiro.
--
-- ANTES DE EXECUTAR: substitua COLE_AQUI_A_SENHA_DO_APP pelo valor de
-- VILLAJU_DB_SENHA que está no seu .env. Desfaça a edição antes de
-- commitar - há teste automatizado que falha se a senha real ficar aqui.
-- ---------------------------------------------------------------------------

-- --- A conta ---------------------------------------------------------------
-- 'localhost' limita a conexão à própria máquina. Com '%', a conta aceitaria
-- conexão de qualquer origem da rede, o que não é necessário: aplicação e
-- banco rodam no mesmo computador.

CREATE USER IF NOT EXISTS 'villaju_app'@'localhost'
    IDENTIFIED BY 'COLE_AQUI_A_SENHA_DO_APP';

-- --- Os privilégios --------------------------------------------------------
-- Os GRANT usam comando preparado porque o nome do banco não pode ser
-- parametrizado em SQL. Montá-lo a partir de DATABASE() permite que este
-- mesmo arquivo sirva ao banco real e ao de testes, sem duplicação - e
-- duas cópias de um arquivo de privilégios é precisamente o tipo de coisa
-- que diverge com o tempo e abre brecha.
--
-- O privilégio é concedido TABELA A TABELA, não com ON villaju.*. A
-- diferença é o que torna o log append-only: um GRANT no banco inteiro
-- daria UPDATE e DELETE também sobre log_auditoria.

SET @conta = "'villaju_app'@'localhost'";
SET @banco = DATABASE();

-- Seis tabelas de dados: leitura e escrita completas.
SET @sql = CONCAT('GRANT SELECT, INSERT, UPDATE, DELETE ON `', @banco, '`.chale TO ', @conta);
PREPARE comando FROM @sql; EXECUTE comando; DEALLOCATE PREPARE comando;

SET @sql = CONCAT('GRANT SELECT, INSERT, UPDATE, DELETE ON `', @banco, '`.tarifa TO ', @conta);
PREPARE comando FROM @sql; EXECUTE comando; DEALLOCATE PREPARE comando;

SET @sql = CONCAT('GRANT SELECT, INSERT, UPDATE, DELETE ON `', @banco, '`.hospede TO ', @conta);
PREPARE comando FROM @sql; EXECUTE comando; DEALLOCATE PREPARE comando;

SET @sql = CONCAT('GRANT SELECT, INSERT, UPDATE, DELETE ON `', @banco, '`.reserva TO ', @conta);
PREPARE comando FROM @sql; EXECUTE comando; DEALLOCATE PREPARE comando;

SET @sql = CONCAT('GRANT SELECT, INSERT, UPDATE, DELETE ON `', @banco, '`.usuario TO ', @conta);
PREPARE comando FROM @sql; EXECUTE comando; DEALLOCATE PREPARE comando;

SET @sql = CONCAT('GRANT SELECT, INSERT, UPDATE, DELETE ON `', @banco, '`.contato TO ', @conta);
PREPARE comando FROM @sql; EXECUTE comando; DEALLOCATE PREPARE comando;

-- Auditoria: apenas leitura e acréscimo. Sem UPDATE, sem DELETE.
SET @sql = CONCAT('GRANT SELECT, INSERT ON `', @banco, '`.log_auditoria TO ', @conta);
PREPARE comando FROM @sql; EXECUTE comando; DEALLOCATE PREPARE comando;

FLUSH PRIVILEGES;

-- --- Conferência -----------------------------------------------------------

SHOW GRANTS FOR 'villaju_app'@'localhost';
