-- ---------------------------------------------------------------------------
-- VillaJu - massa de dados fictícios
--
-- Conjunto mínimo para demonstrar o sistema e exercitar as triggers: os
-- cinco chalés reais da hospedaria, tarifas de alta e baixa temporada,
-- contas de equipe, hóspedes e um calendário de reservas.
--
--     mysql -u root -p villaju -e "SOURCE banco/04_dados_ficticios.sql"
--
-- O script é idempotente: limpa o que existe antes de inserir, de modo que
-- rodá-lo duas vezes não duplica nada nem falha por chave única.
--
-- ---------------------------------------------------------------------------
-- AVISO 1 - os dados de hóspede NÃO são ciphertext real
--
-- As colunas _cif, _nonce e _tag recebem bytes aleatórios, não o resultado
-- de uma cifragem AES-256-GCM. Eles NÃO podem ser decifrados, e não é essa
-- a função deles aqui.
--
-- A razão é a ordem das etapas: o módulo de criptografia nasce na Etapa 2,
-- depois desta. Gerar ciphertext verdadeiro exigiria a chave de cifragem,
-- que vive no .env de cada integrante e jamais entra num arquivo
-- versionado.
--
-- A partir da Etapa 2, estes registros são regerados pela aplicação, que
-- cifra de verdade. Até lá eles servem ao que precisam servir: dar
-- hospede_id válido para as reservas e permitir verificar que o log de
-- auditoria nunca copia conteúdo dessas colunas.
--
-- ---------------------------------------------------------------------------
-- AVISO 2 - as senhas de demonstração são públicas
--
-- Os hashes abaixo são Argon2id verdadeiros, gerados com os parâmetros da
-- seção 6.1 (64 MiB, 3 iterações, paralelismo 4). As senhas em claro estão
-- documentadas aqui porque são contas de demonstração:
--
--     admin      Villa@Admin2026      administrador
--     recepcao   Villa@Recep2026      recepcionista
--     joao       Villa@Hospede2026    hospede
--
-- Estas contas existem para a apresentação e para os testes manuais. Se o
-- sistema algum dia receber dado real, elas precisam ser removidas ou ter
-- a senha trocada - senha publicada em repositório é senha comprometida.
-- ---------------------------------------------------------------------------


-- ===========================================================================
-- LIMPEZA
-- ===========================================================================
-- A ordem respeita as chaves estrangeiras: filhos antes dos pais. O log de
-- auditoria vai por último porque as exclusões acima disparam triggers que
-- escrevem nele.

DELETE FROM reserva;
DELETE FROM contato;
DELETE FROM usuario;
DELETE FROM tarifa;
DELETE FROM hospede;
DELETE FROM chale;
DELETE FROM log_auditoria;

-- Reinicia os contadores para que os identificadores sejam previsíveis a
-- cada execução, o que torna a demonstração reproduzível.
ALTER TABLE reserva       AUTO_INCREMENT = 1;
ALTER TABLE contato       AUTO_INCREMENT = 1;
ALTER TABLE usuario       AUTO_INCREMENT = 1;
ALTER TABLE tarifa        AUTO_INCREMENT = 1;
ALTER TABLE hospede       AUTO_INCREMENT = 1;
ALTER TABLE chale         AUTO_INCREMENT = 1;
ALTER TABLE log_auditoria AUTO_INCREMENT = 1;

-- Nenhum usuário da aplicação está operando ainda: os registros de
-- auditoria desta carga ficam sem autor, o que é o comportamento correto.
SET @app_usuario_id = NULL;


-- ===========================================================================
-- CHALÉS
-- ===========================================================================
-- Os cinco chalés reais da VillaJu, na Praia da Juréia, São Sebastião (SP).
-- Capacidade de 4 pessoas em todos, confirmada com a responsável.
--
-- criado_em e alterado_em não são informados: as triggers de carimbo de
-- tempo os preenchem.

INSERT INTO chale (nome, capacidade, configuracao_camas, comodidades, descricao) VALUES
('Embaré',   4, '1 cama de casal e 2 de solteiro',
 'Wi-Fi, ar-condicionado, cozinha equipada, varanda',
 'Chalé com vista para a mata, a poucos minutos da praia da Juréia.'),

('Camburi',  4, '1 cama de casal e 1 beliche',
 'Wi-Fi, ventilador de teto, cozinha equipada, rede na varanda',
 'Chalé rústico cercado por Mata Atlântica, ideal para famílias.'),

('Asturias', 4, '2 camas de casal',
 'Wi-Fi, ar-condicionado, cozinha equipada, área externa privativa',
 'O mais reservado dos cinco, indicado para quem busca silêncio.'),

('Boracéia', 4, '1 cama de casal e 2 de solteiro',
 'Wi-Fi, ar-condicionado, cozinha equipada, varanda com vista',
 'Chalé próximo à piscina e à churrasqueira compartilhadas.'),

('Dunas',    4, '1 cama de casal e 1 sofá-cama',
 'Wi-Fi, ventilador de teto, cozinha compacta, varanda',
 'O mais próximo do acesso à praia, indicado para casais.');


-- ===========================================================================
-- TARIFAS
-- ===========================================================================
-- Duas temporadas por chalé, cobrindo o verão de 2026/2027.
--
-- A alta temporada vai de 15/12 a 28/02 - período de férias escolares e
-- Carnaval. O restante do ano é baixa temporada.
--
-- Esta estrutura existe para exercitar o cálculo de valor total na virada
-- de temporada, que a Etapa 3 implementa e que o plano aponta como a parte
-- mais sujeita a erro.

INSERT INTO tarifa (chale_id, data_inicio, data_fim, valor_diaria, descricao) VALUES
-- Embaré
(1, '2026-03-01', '2026-12-14', 320.00, 'Baixa temporada'),
(1, '2026-12-15', '2027-02-28', 580.00, 'Alta temporada'),
-- Camburi
(2, '2026-03-01', '2026-12-14', 300.00, 'Baixa temporada'),
(2, '2026-12-15', '2027-02-28', 540.00, 'Alta temporada'),
-- Asturias
(3, '2026-03-01', '2026-12-14', 380.00, 'Baixa temporada'),
(3, '2026-12-15', '2027-02-28', 660.00, 'Alta temporada'),
-- Boracéia
(4, '2026-03-01', '2026-12-14', 340.00, 'Baixa temporada'),
(4, '2026-12-15', '2027-02-28', 600.00, 'Alta temporada'),
-- Dunas
(5, '2026-03-01', '2026-12-14', 280.00, 'Baixa temporada'),
(5, '2026-12-15', '2027-02-28', 500.00, 'Alta temporada');


-- ===========================================================================
-- HÓSPEDES
-- ===========================================================================
-- Ver o AVISO 1 no cabeçalho: estes bytes NÃO são ciphertext real.
--
-- RANDOM_BYTES é uma função do MySQL que devolve bytes criptograficamente
-- aleatórios. Usá-la aqui garante que nenhum valor legível seja gravado,
-- nem por acidente - o que seria o pior dos mundos numa tabela cujo
-- propósito é não conter texto claro.
--
-- Os tamanhos imitam o que a cifragem real produzirá: nonce de 12 bytes,
-- tag de 16, ciphertext proporcional ao texto original, e campos de busca
-- com os 32 bytes de um HMAC-SHA256.

INSERT INTO hospede
    (nome_cif, nome_nonce, nome_tag,
     documento_cif, documento_nonce, documento_tag,
     telefone_cif, telefone_nonce, telefone_tag,
     email_cif, email_nonce, email_tag,
     email_busca, documento_busca, cripto_versao)
VALUES
-- Hóspede 1
(RANDOM_BYTES(48), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(16), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(16), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(32), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(32), RANDOM_BYTES(32), 1),
-- Hóspede 2
(RANDOM_BYTES(48), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(16), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(16), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(32), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(32), RANDOM_BYTES(32), 1),
-- Hóspede 3
(RANDOM_BYTES(48), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(16), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(16), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(32), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(32), RANDOM_BYTES(32), 1),
-- Hóspede 4
(RANDOM_BYTES(48), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(16), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(16), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(32), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(32), RANDOM_BYTES(32), 1),
-- Hóspede 5 - sem telefone nem e-mail, para exercitar os campos opcionais
(RANDOM_BYTES(48), RANDOM_BYTES(12), RANDOM_BYTES(16),
 RANDOM_BYTES(16), RANDOM_BYTES(12), RANDOM_BYTES(16),
 NULL, NULL, NULL,
 NULL, NULL, NULL,
 NULL, RANDOM_BYTES(32), 1);


-- ===========================================================================
-- USUÁRIOS
-- ===========================================================================
-- Ver o AVISO 2 no cabeçalho: as senhas destas contas são públicas.
--
-- Os três perfis do sistema estão representados, o que permite demonstrar o
-- controle de acesso: o que o recepcionista não vê, o que o hóspede não
-- alcança.
--
-- A conta 'joao' é vinculada ao hóspede 1. A trigger de integridade de
-- perfil exige esse vínculo quando perfil = 'hospede', e o proíbe nos
-- demais perfis.

INSERT INTO usuario (login, nome, senha_hash, perfil, hospede_id) VALUES
('admin', 'Vania Silva',
 '$argon2id$v=19$m=65536,t=3,p=4$t9bPO3S10PZ9eDMQtdyEZA$fZPq/FZjWm9Ze0hJzQefojB9uLE6QpYxCQiYOQ9jvHE',
 'administrador', NULL),

('recepcao', 'Recepção VillaJu',
 '$argon2id$v=19$m=65536,t=3,p=4$/CX6w5lR3qG7un0bn8pL5g$i6PIdgbdv/BYJ8FEaQcDhwvY6uFCAQTWKPJTl+G6Ooc',
 'recepcionista', NULL),

('joao', 'João da Silva',
 '$argon2id$v=19$m=65536,t=3,p=4$cdejdTpk/+9Pr0qyFEkbDA$PD7JC7Qsfc0eAFayxpBYvvPvLiTFb2Erou9GtWjPrXQ',
 'hospede', 1);


-- ===========================================================================
-- RESERVAS
-- ===========================================================================
-- Daqui em diante a recepção está operando: o identificador do usuário vai
-- para a variável de sessão, e as triggers de auditoria passam a registrar
-- o autor de cada operação.

SET @app_usuario_id = 2;

-- Todas as reservas abaixo respeitam as três regras das triggers. Se
-- alguma violasse, este script falharia - o que é, em si, uma verificação:
-- a carga de dados só completa se os dados forem válidos.
--
-- O calendário foi montado para exercitar casos interessantes:
--
--   Embaré    duas reservas encostadas, saída dia 20 e entrada dia 20.
--             Só passam por causa do intervalo semiaberto.
--   Camburi   reserva que atravessa a virada de temporada (14 para 15/12).
--   Asturias  reserva cancelada e outra ocupando o mesmo período.
--   Boracéia  reserva pendente, aguardando confirmação.
--   Dunas     reserva já concluída, de temporada passada.

INSERT INTO reserva
    (chale_id, hospede_id, data_entrada, data_saida, num_pessoas, situacao,
     valor_total, observacoes, criado_por)
VALUES
-- Embaré: duas reservas consecutivas, encostadas no dia 20
(1, 1, '2026-12-15', '2026-12-20', 4, 'confirmada', 2900.00,
 'Chegada prevista para o fim da tarde.', 2),
(1, 2, '2026-12-20', '2026-12-27', 2, 'confirmada', 4060.00,
 'Hóspede recorrente.', 2),

-- Camburi: atravessa a virada de temporada (baixa até 14/12, alta a partir de 15)
(2, 3, '2026-12-10', '2026-12-18', 4, 'confirmada', 3480.00,
 'Período cruza a virada de temporada.', 2),

-- Asturias: uma cancelada e outra ocupando o mesmo período
(3, 4, '2027-01-05', '2027-01-12', 2, 'cancelada', 4620.00,
 'Cancelada pelo hóspede por motivo de viagem.', 2),
(3, 5, '2027-01-05', '2027-01-12', 4, 'confirmada', 4620.00,
 'Assumiu o período da reserva cancelada.', 2),

-- Boracéia: aguardando confirmação
(4, 1, '2027-02-10', '2027-02-15', 3, 'pendente', 3000.00,
 'Aguardando confirmação do hóspede.', 2),

-- Dunas: temporada passada, já concluída
(5, 2, '2026-07-04', '2026-07-11', 2, 'concluida', 1960.00,
 'Estadia concluída sem intercorrências.', 2);


-- ===========================================================================
-- CONTATOS
-- ===========================================================================
-- Pedidos vindos do formulário público. Diferente de hospede, estes dados
-- não são cifrados: são fornecidos voluntariamente por quem busca
-- orçamento e ainda não é hóspede. A decisão está registrada como aberta no
-- plano e pode ser revista com a responsável.
--
-- INET6_ATON converte o endereço para a forma binária de 16 bytes, que é
-- como a coluna o guarda.

INSERT INTO contato
    (nome, email, telefone, mensagem, chale_interesse_id,
     data_entrada_desejada, data_saida_desejada, origem, ip_origem, situacao)
VALUES
('Mariana Alves', 'mariana.alves@exemplo.com', '(11) 98888-1234',
 'Gostaria de saber a disponibilidade do Asturias para o Carnaval.',
 3, '2027-02-12', '2027-02-17', 'site', INET6_ATON('192.168.0.31'), 'novo'),

('Carlos Pereira', 'carlos.pereira@exemplo.com', '(12) 99777-5678',
 'Vocês aceitam animais de estimação? Somos duas pessoas e um cachorro.',
 NULL, NULL, NULL, 'instagram', INET6_ATON('192.168.0.74'), 'em_atendimento'),

('Fernanda Lima', 'fernanda.lima@exemplo.com', NULL,
 'Qual o valor da diária do Dunas em março?',
 5, '2027-03-07', '2027-03-10', 'site', INET6_ATON('10.0.0.15'), 'respondido'),

('Contato sem retorno', 'sem.retorno@exemplo.com', NULL,
 'Mensagem automática, descartada pela equipe.',
 NULL, NULL, NULL, 'site', INET6_ATON('203.0.113.9'), 'descartado');


-- A sessão termina sem usuário definido, para não vazar o identificador
-- para a próxima conexão que reaproveitar esta.
SET @app_usuario_id = NULL;


-- ===========================================================================
-- CONFERÊNCIA
-- ===========================================================================

SELECT 'chale' AS tabela, COUNT(*) AS registros FROM chale
UNION ALL SELECT 'tarifa', COUNT(*) FROM tarifa
UNION ALL SELECT 'hospede', COUNT(*) FROM hospede
UNION ALL SELECT 'usuario', COUNT(*) FROM usuario
UNION ALL SELECT 'reserva', COUNT(*) FROM reserva
UNION ALL SELECT 'contato', COUNT(*) FROM contato
UNION ALL SELECT 'log_auditoria', COUNT(*) FROM log_auditoria;
