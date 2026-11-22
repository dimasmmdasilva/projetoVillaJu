# Relatório 1 — Análise estática e revisão de código

**Projeto:** VillaJu — Sistema de Reservas e Divulgação
**Disciplina:** Projeto Integrador 4 — Bacharelado em Cibersegurança — PUC-Campinas
**Equipe:** Dimas Marcondes · Eduardo Malta · Gabriel Fuzinatto · Guilherme Leal · Murilo Dias Bartkevicius
**Data da análise:** 22/11/2026 · **Versão analisada:** commit `3133d00`; correções no commit `4c853d9` (`main`)

> **Situação deste documento:** em elaboração. Seções marcadas com
> **[pendente]** aguardam as correções em andamento e a revisão por pares.

---

## 1. Introdução e objetivo

Este relatório registra a análise estática do código do VillaJu: o exame
do código-fonte, das dependências e do histórico do repositório **sem
executar o sistema**. O objetivo é encontrar falhas de segurança antes que
alguém as encontre em uso, corrigi-las ou justificar por escrito as que
forem aceitas.

A análise combina duas fontes, como pede o plano da disciplina:

1. **Ferramentas automáticas**, que varrem todo o código com regras
   conhecidas;
2. **Revisão por pares**, em que integrantes que não escreveram cada
   módulo o leem com uma lista de verificação.

Uma terceira fonte, própria do projeto, complementa as duas: a **suíte
de testes validada por sabotagem** (seção 3.3), que encontrou falhas que
as ferramentas não viram.

---

## 2. Escopo

| Incluído | Quantidade |
|---|---|
| Código Python da aplicação e dos scripts (`app/`, `scripts/`, `run.py`) | 10.028 linhas, 102 arquivos varridos pelo Semgrep |
| Templates Jinja2 (`app/templates/`) | varridos pelo Semgrep (leitura parcial, ver 4.2) |
| Scripts SQL (`banco/`) | revisão por pares (módulos B e C) |
| Dependências (`requirements.txt`) | 55 pacotes |
| Histórico do Git | 125 commits, todos os ramos |

**Fora do escopo:** a suíte de testes (`testes/`), que é a ferramenta de
verificação e não o alvo; o comportamento do sistema em execução, que é
objeto do Relatório 2 (análise dinâmica).

---

## 3. Metodologia e ferramentas

### 3.1. Ambiente

| Item | Versão |
|---|---|
| Sistema operacional | Windows 11 |
| Python | 3.12.3 |
| Flask | 3.1.3 |
| mysql-connector-python | 26.10.0 |
| cryptography | 50.0.2 |
| argon2-cffi | 25.1.0 |

### 3.2. Ferramentas automáticas

| Ferramenta | Versão | O que verifica | Comando |
|---|---|---|---|
| **Bandit** | 1.9.4 | padrões inseguros em Python: SQL montado com texto, `subprocess`, segredos no código, criptografia fraca | `bandit -r app/ scripts/ run.py` |
| **Semgrep** | 1.182.1 | regras OWASP Top 10 e regras específicas de Flask | `semgrep --config=p/owasp-top-ten --config=p/flask --metrics=off app/ run.py scripts/` |
| **pip-audit** | 2.10.1 | dependências com vulnerabilidade conhecida (base PyPA/OSV) | `pip-audit -r requirements.txt` |
| **gitleaks** | 8.30.1 | segredos nos arquivos e **em todo o histórico do Git** | `gitleaks git . --log-opts="--all"` e `gitleaks dir .` |

**Isolamento.** O Semgrep roda num ambiente Python separado
(`venv-ferramentas`), porque traz cerca de 45 dependências próprias que
fariam o pip-audit acusar vulnerabilidades da ferramenta, e não do
sistema. Bandit e gitleaks rodam localmente sem enviar nada; o Semgrep
baixa as regras mas não envia o código (`--metrics=off`); o pip-audit
consulta a base pública com nome e versão dos pacotes.

As saídas completas, em JSON, estão guardadas como evidência em
`instance/evidencias/relatorio1/` (fora do repositório).

### 3.3. A suíte de testes validada por sabotagem

O projeto tem uma suíte de cerca de 2.500 testes automatizados. Cada teste
de proteção foi validado por **sabotagem deliberada**: o código protegido
é quebrado de propósito e confirma-se que o teste falha. Teste que nunca
falha não protege nada. Só a partir do Bloco 7 da Etapa 5, mais de 400
sabotagens foram aplicadas e registradas, cada uma com o teste que a
detectou.

Essa prática encontrou, entre outros, o **ponto cego** descrito em 4.2: uma
injeção de SQL que os testes e o Bandit detectaram e o Semgrep não.

### 3.4. Revisão por pares

Roteiro em [`docs/revisao_por_pares.md`](revisao_por_pares.md): o código
foi dividido em quatro módulos (A — segurança e configuração; B — acesso ao
banco; C — regras de negócio e pagamentos; D — rotas e telas), cada um com
uma lista de 6 a 7 perguntas objetivas e um formulário de devolução. Cada
módulo é lido por um integrante que não o escreveu.

---

## 4. Achados por ferramenta

### 4.1. Bandit

**Resultado final: 0 achados, 0 avisos.** Na primeira rodada, também 0
achados ativos — mas o "zero" escondia três problemas, achados ao
verificar uma a uma as **7 exceções** (`# nosec`) declaradas no código.
Exceção é um comentário que manda o Bandit ignorar um alerta numa linha;
sem verificação, é achado escondido. Cada uma foi conferida rodando o
Bandit **ignorando todas as exceções** (`--ignore-nosec`):

| Local | Alerta suprimido | Avaliação | Depois da correção |
|---|---|---|---|
| `scripts/backup.py:31` | B404 — importa `subprocess` | **Aceito**: o backup chama o `mysqldump`, argumentos em lista, sem shell | mantida, justificada |
| `scripts/restaurar.py:35` | B404 — importa `subprocess` | **Aceito**: idem, com o `mysql` | mantida, justificada |
| `scripts/configurar_backup.py:21` | B404 — importa `subprocess` | **Aceito**: idem, com o `icacls` | mantida, justificada |
| `scripts/configurar_backup.py:52` | B603 + **B607** — programa chamado sem caminho completo | **Achado R1-01** | B607 corrigido; B603 mantida, justificada |
| `scripts/restaurar.py:59` | B105 — "senha escrita no código" (falso positivo: a palavra "token" no nome da tabela) | valia para o bloco inteiro de 11 linhas | **removida**: o código foi reescrito sem o que disparava o alerta |
| `scripts/backup.py:116` | — nenhum | **Achado R1-02**: não suprimia nada | **removida** |
| `scripts/restaurar.py:109` | — nenhum | **Achado R1-02**: idem | **removida** |

Das 7 exceções, ficaram **4, todas necessárias e justificadas** — e um
teste automatizado passou a garantir isso (seção 5).

### 4.2. Semgrep

**Resultado: 0 achados** em 102 arquivos, com as regras OWASP Top 10 e
Flask.

**Limitações registradas:**

1. **Templates lidos só em parte.** Em 36 templates Jinja2 o Semgrep
   registrou leitura parcial (`PartialParsing`): ele analisa o HTML, mas
   não entende a sintaxe do Jinja. Os templates foram cobertos pela revisão
   por pares (módulo D) e pelos testes de escape de saída.
2. **Ponto cego confirmado por sabotagem (Etapa 2, Bloco 4).** A consulta
   de login foi trocada de propósito por concatenação de texto — uma
   injeção de SQL clássica:

   | Ferramenta | Detectou? |
   |---|---|
   | Testes de injeção do projeto | **sim**, três falharam |
   | Bandit, regra B608 | **sim** |
   | Semgrep, 155 regras OWASP Top Ten e Flask | **não**, zero achados |

   O Semgrep gratuito não rastreou que o parâmetro vinha da entrada do
   usuário, porque a função vulnerável estava duas camadas acima da rota. **Conclusão:** nenhuma ferramenta isolada basta;
   o projeto usa várias e não trata "zero achados" como prova de ausência.

### 4.3. pip-audit

**Resultado: 0 vulnerabilidades conhecidas** nas 55 dependências.

### 4.4. gitleaks

**Resultado: nenhum segredo** nos arquivos versionados e **em nenhum dos
125 commits** do histórico.

Na pasta de trabalho (fora do Git), o gitleaks encontra, como esperado, o
`.env` (chaves e senhas), a chave privada do certificado TLS (`certs/`) e
o arquivo de opções do usuário de backup (`instance/backup.cnf`). Os três
estão no `.gitignore` desde o primeiro commit, e um teste automatizado
falha se o `.gitignore` deixar de protegê-los.

---

## 5. Achados consolidados e correções

Todas as correções estão no commit
[`4c853d9`](https://github.com/dimasmmdasilva/projetoVillaJu/commit/4c853d9),
cada uma com teste automatizado e **sabotagem** — a correção desfeita de
propósito, para confirmar que o teste acusa (10 sabotagens, 10
detectadas).

| ID | Achado | Fonte | Severidade | Correção | Teste que guarda |
|---|---|---|---|---|---|
| R1-01 | `icacls` chamado pelo nome, procurado no `PATH`: outro `icacls.exe` posto antes no `PATH` rodaria no lugar | Bandit B607 | Baixa | caminho da pasta do sistema perguntado ao Windows (`GetSystemDirectoryW`) | `test_backup.py::test_o_icacls_e_a_conta_vem_do_windows_e_nao_do_ambiente` |
| R1-02 | Exceções `# nosec` que não suprimiam nada (2) ou valiam para um bloco inteiro (1): esconderiam em silêncio um problema futuro | Bandit (avisos) | Baixa | removidas; o dicionário que disparava o falso positivo passou a ser montado a partir de pares | `test_analise_estatica.py::test_toda_excecao_suprime_um_alerta_de_fato` |
| R1-03 | Justificativas das exceções num formato que o Bandit lia como nomes de teste (dezenas de avisos) | Bandit (avisos) | Observação | formato `# nosec BXXX  # justificativa` | `test_analise_estatica.py::test_toda_excecao_diz_por_que` |
| R1-04 | 36 templates Jinja2 lidos só em parte pelo Semgrep | Semgrep | Limitação | aceita (seção 7) | — |
| R1-05 | **A regra de senha escrita no código do Bandit não reconhece nomes em português** | sabotagem | Média (método) | verificação própria do projeto, complementar ao Bandit | `test_analise_estatica.py::test_nenhum_segredo_escrito_no_codigo_nem_em_portugues` |
| R1-06 | A conta que recebe permissão no `backup.cnf` vinha da variável `USERNAME`: alterá-la daria a leitura da senha de backup a outra conta | Semgrep, ao revisar a correção do R1-01 | Baixa | conta perguntada ao Windows (`GetUserNameW`) | o mesmo teste do R1-01, com o ambiente sabotado |

### 5.1. R1-05: o ponto cego do Bandit em português

O teste do achado R1-02 foi validado por sabotagem — e uma delas, que
escreveu `SENHA_PADRAO = "villaju-2026"` no código, **passou pelo Bandit
em silêncio**. A hipótese foi comprovada num arquivo à parte, com o mesmo
valor em quatro nomes:

| Linha | Bandit (B105) | gitleaks |
|---|---|---|
| `SENHA_PADRAO = "villaju-2026"` | **não acusa** | não acusa |
| `CHAVE_SECRETA = "villaju-2026"` | **não acusa** | não acusa |
| `PASSWORD = "villaju-2026"` | acusa | não acusa |
| `SECRET_KEY = "villaju-2026"` | acusa | não acusa |

A regra B105 só reconhece nomes de variável em inglês (`password`,
`secret`, `token`...). Num projeto escrito em português, ela fica
**praticamente desligada sem que ninguém perceba**. O gitleaks também não
cobre, porque procura segredos com cara de chave aleatória.

**Mitigação:** um teste do projeto lê o código (pela árvore sintática do
Python) e reprova qualquer texto atribuído a um nome que fale de senha,
chave, segredo ou token, em português ou inglês — em atribuição, em
argumento de função e em chave de dicionário. Três ocorrências atuais são
falsos positivos e estão numa lista de exceções nomeadas, cada uma com o
motivo; outro teste reprova a lista se uma exceção deixar de existir no
código. **Nenhuma senha real** foi encontrada no código.

### 5.2. A correção que revelou outro achado (R1-01 → R1-06)

A primeira correção do R1-01 montou o caminho do `icacls` com a variável
de ambiente `SystemRoot`. O Semgrep, que estava em zero, passou a acusar
um achado de severidade ERROR (*subprocess com dado controlável pelo
ambiente*, OWASP A03 — Injeção): variável de ambiente é tão controlável
quanto o `PATH`, e a correção tinha trocado um ponto de controle externo
por outro. Investigando, o mesmo comando tinha um segundo uso do
ambiente, anterior à correção e que o Semgrep não acusava antes: a conta
que recebe a permissão, lida de `USERNAME` (R1-06).

A correção final não confia no ambiente para nada: a pasta do sistema e
a conta em uso vêm da API do Windows. O teste põe valores falsos em
`PATH`, `SystemRoot` e `USERNAME` e exige que nenhum chegue ao comando.

**Lição de método:** rodar **de novo todas as ferramentas depois de cada
correção**, e não só a que apontou o achado. A correção de um alerta do
Bandit gerou um alerta do Semgrep — e foi esse que levou ao achado mais
sério do lote.

## 6. Resultado da revisão por pares

**[pendente]** — aguardando as devoluções dos módulos A, B, C e D (prazo
do roteiro: 23/11).

| Módulo | Revisor(a) | Perguntas conferidas | Achados | Encaminhamento |
|---|---|---|---|---|
| A — Segurança e configuração | | | | |
| B — Acesso ao banco | | | | |
| C — Regras e pagamentos | | | | |
| D — Rotas e telas | | | | |

---

## 7. Achados aceitos e por quê

| Achado | Justificativa |
|---|---|
| B404 (importar `subprocess`) nos três scripts de backup | O backup e a restauração precisam chamar `mysqldump`, `mysql` e `icacls`. Os argumentos vão em lista fixa, nunca por shell, e a senha nunca vai à linha de comando (arquivo de opções restrito ao dono) |
| B603 em `scripts/configurar_backup.py` | O `subprocess.run` recebe o caminho absoluto do `icacls`, vindo da API do Windows, e argumentos em lista, sem shell |
| R1-04 | Limitação da ferramenta; a cobertura dos templates vem da revisão por pares (módulo D) e dos testes de escape de saída |

## 8. Recomendações remanescentes

**[pendente]** — a completar com o resultado da revisão por pares e da
análise dinâmica. Já registradas:

- **Rodar todas as ferramentas depois de cada correção**, não só a que
  apontou o achado (seção 5.2).
- **Não tratar "zero achados" como prova de ausência.** Os dois pontos
  cegos documentados aqui — o Semgrep que não segue o dado entre camadas
  (4.2) e o Bandit que não lê nomes em português (5.1) — só apareceram
  porque o projeto testa as próprias ferramentas por sabotagem.
- **Em projeto escrito em outro idioma, conferir se as regras das
  ferramentas reconhecem os nomes usados** — e completar com regras
  próprias onde não reconhecem.
- **Exceção (`# nosec`) só com justificativa na mesma linha**, conferida
  por teste a cada execução da suíte.
- **Em produção, trocar o servidor de desenvolvimento** do Flask por um
  servidor de aplicação (Waitress ou Gunicorn) atrás de um proxy — a ser
  detalhado no Relatório 2.

## 9. Conclusão

**[pendente]** — a escrever depois das correções e da revisão por pares.
