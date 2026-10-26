-- ---------------------------------------------------------------------------
-- VillaJu - criação dos bancos e do usuário de testes
--
-- Este é o ÚNICO script que precisa do root. Depois dele, nada mais no
-- projeto se conecta como administrador do servidor.
--
-- No PowerShell (o operador '<' não existe lá, é sintaxe de cmd e de bash):
--
--     mysql -u root -p -e "SOURCE banco/00_bancos.sql"
--
-- No cmd ou no Git Bash:
--
--     mysql -u root -p < banco/00_bancos.sql
--
-- ATENÇÃO: há duas senhas diferentes envolvidas, e confundi-las é o erro
-- mais provável aqui.
--
--   No prompt "Enter password:"  -> a senha do ROOT do MySQL, definida por
--                                   você na instalação do servidor.
--   Dentro deste arquivo         -> a senha do usuário villaju_teste, que
--                                   NÃO existe ainda: é o CREATE USER abaixo
--                                   que a estabelece.
--
-- ANTES DE EXECUTAR: substitua COLE_AQUI_A_SENHA_DE_TESTE pelo valor de
-- VILLAJU_DB_SENHA_TESTE que o script scripts/gerar_chaves.py produziu e
-- que está no seu .env. As duas precisam ser iguais, senão a suíte de
-- testes não consegue conectar.
--
-- Este arquivo é versionado e NÃO pode conter a senha real. Se você editou
-- a senha aqui para executar, desfaça a edição antes de commitar - há um
-- teste automatizado que falha se uma senha real aparecer neste arquivo.
-- ---------------------------------------------------------------------------

-- --- Os dois bancos --------------------------------------------------------
-- utf8mb4 é o único charset do MySQL que cobre o Unicode inteiro. O antigo
-- utf8 guarda no máximo 3 bytes por caractere e trunca emoji, que pode
-- aparecer no nome de um hóspede ou na mensagem de um contato.
--
-- A collation unicode_ci ordena corretamente caracteres acentuados: com a
-- collation padrão, "Ásturias" não apareceria junto de "Asturias".

CREATE DATABASE IF NOT EXISTS villaju
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

CREATE DATABASE IF NOT EXISTS villaju_teste
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

-- --- Permissão para criar triggers sem SUPER -------------------------------
-- Com o log binário ativo (padrão no MySQL 8.0), criar uma trigger exige o
-- privilégio SUPER, a menos que esta variável esteja ligada. O erro, sem
-- ela, é:
--
--     1419 (HY000): You do not have the SUPER privilege and binary logging
--     is enabled
--
-- A alternativa seria conceder SUPER ao usuário de testes - e SUPER é um
-- privilégio global que permite, entre outras coisas, encerrar conexões de
-- terceiros, alterar qualquer variável do servidor e ignorar restrições de
-- somente-leitura. Ligar esta variável é incomparavelmente mais estreito.
--
-- O que a variável de fato afirma: que quem cria trigger ou função é
-- confiável para não gerar eventos inseguros no log binário. No VillaJu o
-- risco é nulo por duas razões - o binlog_format é ROW, que registra as
-- linhas resultantes e não o comando que as produziu, e o projeto não usa
-- replicação nem recuperação ponto-a-ponto.
--
-- SET PERSIST grava em mysqld-auto.cnf e sobrevive ao reinício do serviço,
-- ao contrário de SET GLOBAL.

SET PERSIST log_bin_trust_function_creators = 1;

-- --- Usuário da suíte de testes --------------------------------------------
-- Ele precisa de CREATE, DROP e TRIGGER, porque a suíte monta o schema do
-- zero a cada execução a partir dos mesmos scripts que criam o banco real.
-- Testar uma cópia do schema em vez do schema verdadeiro não provaria nada.
--
-- Esse privilégio é concedido SOMENTE sobre villaju_teste. O usuário não
-- enxerga o banco villaju nem qualquer outro: mesmo que um teste contenha
-- um DROP TABLE equivocado, ele não alcança dado real.
--
-- 'localhost' restringe a conexão à própria máquina. Com '%' o usuário
-- aceitaria conexão de qualquer origem da rede.

CREATE USER IF NOT EXISTS 'villaju_teste'@'localhost'
    IDENTIFIED BY 'COLE_AQUI_A_SENHA_DE_TESTE';

GRANT ALL PRIVILEGES ON villaju_teste.* TO 'villaju_teste'@'localhost';

FLUSH PRIVILEGES;

-- --- Conferência -----------------------------------------------------------
-- Executado por último, mostra o resultado para quem rodou o script.

SELECT
    schema_name AS banco,
    default_character_set_name AS charset,
    default_collation_name AS collation
FROM information_schema.schemata
WHERE schema_name IN ('villaju', 'villaju_teste');

SHOW GRANTS FOR 'villaju_teste'@'localhost';
