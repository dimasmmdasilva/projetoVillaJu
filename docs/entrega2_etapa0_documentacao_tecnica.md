# Documentação Técnica — Protótipo Inicial e Plano de Testes

**Projeto VillaJu — Sistema de Reservas e Divulgação**

| | |
|---|---|
| **Instituição** | Pontifícia Universidade Católica de Campinas — Escola Politécnica |
| **Curso** | Bacharelado em Cibersegurança (21853) — Módulo 4 |
| **Disciplina** | Projeto Integrador — Cibersegurança em Programação Segura |
| **Professor orientador** | Prof. Dr. Felix Carvalho Rodrigues |
| **Integrantes** | Dimas Marcondes · Eduardo Malta · Gabriel Fuzinatto · Guilherme Leal · Murilo Dias Bartkevicius |
| **Instituição parceira** | VillaJu — hospedaria, Praia da Juréia, São Sebastião (SP) |
| **Repositório** | https://github.com/dimasmmdasilva/projetoVillaJu |
| **Escopo deste documento** | Etapa 0 — Ambiente e repositório (22/10 a 23/10 de 2026) |
| **Entrega** | Entrega 2 — Desenvolvimento e programação segura |

---

## Sumário executivo

Este documento descreve a **Etapa 0** do desenvolvimento do sistema VillaJu: a
construção do ambiente de desenvolvimento, a configuração do repositório e a
implementação de um protótipo executável que demonstra a viabilidade técnica da
solução planejada.

A Etapa 0 não implementa funcionalidade de negócio. Seu objetivo é diferente e
igualmente necessário: estabelecer, **antes de existir qualquer linha de código
de aplicação**, as condições que tornam o restante do desenvolvimento seguro e
reproduzível. Isso inclui a proteção contra vazamento de segredos no histórico
de versionamento, a padronização do ambiente entre as cinco máquinas do grupo, a
comunicação cifrada por TLS e a verificação automatizada de todas essas
propriedades.

O resultado é um protótipo que sobe uma aplicação web Flask sobre HTTPS, com
gestão de chaves criptográficas fora do código-fonte, acompanhado de uma suíte
de **145 testes automatizados** e de um repositório público com **19 commits**,
verificado por quatro ferramentas de análise de segurança sem nenhum achado
pendente.

---

# PARTE I — O PROTÓTIPO INICIAL

---

## 1. Descrição do que foi implementado

Esta seção apresenta a parte do software contemplada pelo protótipo, separando o
que já está funcional do que permanece pendente para as etapas seguintes.

### 1.1. Visão geral do que o protótipo demonstra

O protótipo entregue nesta etapa demonstra quatro propriedades do sistema
planejado:

1. **A aplicação web sobe e responde sobre HTTPS**, com certificado TLS válido,
   em ambiente local.
2. **Os segredos do sistema vivem fora do código-fonte**, carregados de variável
   de ambiente, e existe um mecanismo automatizado para gerá-los com qualidade
   criptográfica adequada.
3. **O repositório é incapaz de receber um segredo por acidente**, e essa
   incapacidade é verificada por teste automatizado, não apenas por disciplina
   dos integrantes.
4. **O ambiente é reproduzível** entre máquinas diferentes, e qualquer
   divergência é detectada automaticamente com mensagem explícita.

### 1.2. Funcionalidades já implementadas

#### 1.2.1. Aplicação web com TLS

O arquivo `run.py` implementa o ponto de entrada da aplicação. Ele:

- Carrega a configuração a partir de um arquivo `.env` local, usando a
  biblioteca `python-dotenv`.
- **Verifica a presença de toda configuração obrigatória antes de subir.** Se
  faltar a chave de sessão ou o certificado, a aplicação encerra com código de
  erro e uma mensagem que indica exatamente qual variável está ausente e qual
  comando resolve o problema.
- Constrói um contexto TLS restrito às versões **1.2 e 1.3**, recusando as
  versões 1.0 e 1.1, que estão obsoletas e possuem fraquezas conhecidas.
- Configura o cookie de sessão com os três atributos de endurecimento:
  `HttpOnly` (inacessível a JavaScript), `Secure` (trafega apenas sobre HTTPS) e
  `SameSite=Lax` (reduz exposição a CSRF).
- Expõe uma rota única, `GET /`, que responde `ok` com código HTTP 200.

A rota é propositalmente mínima. Ela não é uma funcionalidade do sistema: é a
prova de que a pilha completa — Python, Flask, TLS, certificado, configuração
por ambiente — está operante de ponta a ponta.

**Verificação executada com o servidor no ar:**

| Verificação | Resultado obtido |
|---|---|
| `GET https://127.0.0.1:5000/` | `ok`, HTTP 200 |
| Versão de TLS negociada por padrão | TLS 1.3, cifra `TLS_AES_256_GCM_SHA384` |
| Tentativa de conexão com TLS 1.0 | recusada |
| Tentativa de conexão com TLS 1.1 | recusada |
| Tentativa de conexão com TLS 1.2 | aceita |
| Tentativa de conexão com HTTP simples | conexão recusada |

#### 1.2.2. Certificado TLS local

O certificado foi gerado com OpenSSL 3.1.4 e possui as seguintes propriedades:

| Propriedade | Valor |
|---|---|
| Algoritmo de chave | RSA 4096 bits |
| Algoritmo de assinatura | SHA-256 |
| Validade | 365 dias (até 23/10/2027) |
| `subject` e `issuer` | `CN = localhost` (autoassinado) |
| `subjectAltName` | `DNS:localhost`, `DNS:villaju.local`, `IP:127.0.0.1` |

O certificado e a chave privada residem no diretório `certs/`, que está excluído
do controle de versão. São artefatos de homologação, gerados individualmente por
cada integrante e nunca compartilhados.

#### 1.2.3. Gestão de chaves criptográficas

O script `scripts/gerar_chaves.py` gera as três chaves que o sistema exige:

| Variável de ambiente | Finalidade |
|---|---|
| `VILLAJU_CHAVE_CIFRAGEM` | Chave AES-256 para cifrar os dados pessoais do hóspede |
| `VILLAJU_CHAVE_INDICE` | Chave HMAC-SHA256 para os campos de busca sobre dados cifrados |
| `VILLAJU_SECRET_KEY` | Chave de assinatura do cookie de sessão do Flask |

Cada chave tem 32 bytes (256 bits), gerados pelo módulo `secrets` da biblioteca
padrão do Python, e é apresentada em codificação base64 para colagem no arquivo
`.env`.

O script **imprime as chaves na saída padrão e não grava arquivo algum**. Essa
escolha é deliberada: um script que grava diretamente no `.env` poderia, numa
execução distraída, sobrescrever a chave de cifragem de um ambiente que já
possui dados gravados — e, como se explica na seção 2.3, a perda dessa chave
torna os dados irrecuperáveis.

#### 1.2.4. Modelo de configuração

O arquivo `.env.example` é versionado e documenta as **13 variáveis** que o
sistema exige, organizadas em quatro grupos: chaves criptográficas, credenciais
do banco de dados, caminhos do certificado TLS e parâmetros da aplicação.

Nenhum valor real aparece nele. As chaves trazem o texto
`GERE_COM_O_SCRIPT_NAO_USE_ESTE_VALOR` e a senha do banco traz
`DEFINA_UMA_SENHA_FORTE_LOCAL`. Um teste automatizado verifica que nenhum valor
desse arquivo decodifica para uma chave válida de 32 bytes — se alguém colar uma
chave real nele por engano, a suíte falha antes do commit.

#### 1.2.5. Proteção do repositório

O arquivo `.gitignore` foi o **primeiro arquivo do repositório**, criado antes de
qualquer outro. Ele bloqueia:

- O arquivo `.env` e todas as suas variantes (`.env.local`, `.env.producao`), com
  exceção explícita para o `.env.example`.
- Certificados e chaves privadas: o diretório `certs/` e os padrões `*.key`,
  `*.crt`, `*.pem`.
- Dumps de banco de dados (`*.sql.gz`, `backups/`), que podem conter dados
  pessoais mesmo sendo fictícios.
- Artefatos do Python (`__pycache__/`, `*.pyc`, `venv/`).
- Saídas das ferramentas de análise e arquivos de editor.

#### 1.2.6. Estrutura do projeto

Foi criada a estrutura de diretórios completa prevista no plano inicial, com a
aplicação dividida em três pacotes Python que correspondem às camadas do
sistema:

```
projetoVillaJu/
├── app/
│   ├── seguranca/      # cifragem, hash de senha, autorização
│   ├── dados/          # repositórios; toda consulta parametrizada
│   ├── rotas/          # face interna (autenticada) e face pública
│   ├── templates/
│   └── static/
├── banco/              # scripts SQL (Etapa 1)
├── testes/
├── docs/
├── scripts/
├── certs/              # fora do controle de versão
├── .env                # fora do controle de versão
├── .env.example
├── requirements.txt
├── requirements-ferramentas.txt
├── pytest.ini
├── run.py
└── README.md
```

Os arquivos `__init__.py` de cada pacote documentam, em sua docstring, as regras
invioláveis daquela camada. O de `app/dados/`, por exemplo, registra que toda
consulta é parametrizada e que nenhuma montagem de SQL por concatenação é
admitida — a regra fica junto do código que ela governa, não apenas no plano.

#### 1.2.7. Ambiente reproduzível

O arquivo `requirements.txt` fixa **10 dependências diretas** em versões exatas,
com o operador `==`. As versões registradas são as efetivamente instaladas e
testadas, não números escolhidos sem verificação:

| Pacote | Versão | Papel no sistema |
|---|---|---|
| Flask | 3.1.3 | Framework web |
| Flask-WTF | 1.3.0 | Token CSRF em todo formulário |
| Flask-Limiter | 4.1.1 | Limitação de taxa no login e no formulário público |
| mysql-connector-python | 26.10.0 | Driver do banco de dados |
| cryptography | 50.0.2 | Cifragem AES-256-GCM |
| argon2-cffi | 25.1.0 | Hash de senha com Argon2id |
| python-dotenv | 1.2.4 | Carga da configuração a partir do `.env` |
| pytest | 9.1.1 | Testes automatizados |
| bandit | 1.9.4 | Análise estática de padrões inseguros |
| pip-audit | 2.10.1 | Dependências com vulnerabilidade conhecida |

A reprodutibilidade foi validada: o `requirements.txt` foi instalado em um
ambiente virtual limpo e separado, e todos os módulos foram importados com
sucesso.

### 1.3. Funcionalidades ainda pendentes

O protótipo **não implementa** nenhuma funcionalidade de negócio. Os itens
abaixo estão previstos no plano e serão entregues nas etapas indicadas.

| Funcionalidade | Etapa prevista |
|---|---|
| Modelo de dados com as 7 tabelas | Etapa 1 |
| Triggers de não-sobreposição, capacidade e coerência de datas | Etapa 1 |
| Triggers de auditoria | Etapa 1 |
| Usuário de banco com privilégio mínimo | Etapa 1 |
| Cifragem AES-256-GCM dos dados do hóspede | Etapa 2 |
| Índice HMAC para busca sobre campo cifrado | Etapa 2 |
| Autenticação com Argon2id | Etapa 2 |
| Controle de acesso por perfil | Etapa 2 |
| Proteção CSRF e limitação de taxa | Etapa 2 |
| Cadastro de chalés, tarifas e hóspedes | Etapa 3 |
| Criação e gestão de reservas | Etapa 3 |
| Consulta de disponibilidade | Etapa 3 |
| Relatórios de ocupação | Etapa 4 |
| Página pública e formulário de contato | Etapa 5 |

As bibliotecas que suportam essas funcionalidades (`cryptography`,
`argon2-cffi`, `Flask-WTF`, `Flask-Limiter`, `mysql-connector-python`) **já estão
instaladas e tiveram seu funcionamento verificado** nesta etapa, conforme
descrito na seção 1.2.7 e na Parte II.

---

## 2. Decisões de projeto

Esta seção justifica as principais escolhas técnicas da Etapa 0 e as relaciona
ao plano inicial.

### 2.1. O `.gitignore` como primeiro arquivo do repositório

**Decisão.** O `.gitignore` foi criado e commitado antes de qualquer outro
arquivo do projeto, inclusive antes da estrutura de diretórios.

**Justificativa.** O Git preserva permanentemente tudo o que entra no seu
histórico. Um segredo commitado por engano não é removido por um commit
posterior que apague o arquivo: ele continua recuperável em qualquer clone do
repositório. Removê-lo exige reescrever o histórico com ferramentas como o
`git filter-repo`, operação que invalida todos os clones existentes e que, num
repositório público, chega tarde demais — qualquer pessoa pode já ter clonado.

Criar a proteção antes de existir qualquer código elimina a janela de
oportunidade para esse erro. É uma aplicação direta do princípio de que a
segurança deve ser projetada desde o início, e não acrescentada depois.

### 2.2. Verificação automatizada da proteção contra vazamento

**Decisão.** A eficácia do `.gitignore` é verificada por **36 testes
automatizados**, e não apenas pela leitura do arquivo.

**Justificativa.** Um `.gitignore` correto hoje pode ser quebrado amanhã por uma
edição distraída, e a quebra é silenciosa: nada avisa que o `.env` voltou a ser
rastreável. Com cinco integrantes editando o mesmo repositório, a probabilidade
de isso ocorrer ao longo de cinco semanas não é desprezível.

Os testes cobrem três camadas de verificação:

1. **As regras declaradas.** Onze caminhos sensíveis que precisam ser bloqueados
   e sete caminhos legítimos que precisam continuar versionáveis.
2. **O que o Git rastreia no momento.** Varredura da lista de arquivos
   rastreados em busca de extensões de chave, certificado ou dump.
3. **O histórico completo.** Um arquivo pode ter sido removido do diretório de
   trabalho e permanecer em commits antigos. Este teste varre todo o histórico,
   porque remover um segredo depois exige reescrita.

### 2.3. Três chaves criptográficas distintas

**Decisão.** O sistema usa três chaves separadas — cifragem, índice de busca e
sessão — em vez de uma única chave para todos os fins.

**Justificativa.** Reutilizar uma mesma chave para finalidades criptográficas
distintas é má prática reconhecida, por dois motivos. Primeiro, porque amplia o
impacto de um comprometimento: quem obtiver a chave de sessão passaria também a
poder decifrar dados pessoais. Segundo, porque impede a rotação independente —
se a chave de índice precisar ser trocada, não há razão para invalidar todos os
dados cifrados.

A separação também antecipa um requisito da Etapa 2: os campos de busca sobre
dados cifrados usam HMAC-SHA256 com uma chave própria, distinta da chave AES.

**Consequência aceita.** A perda da `VILLAJU_CHAVE_CIFRAGEM` torna os dados
pessoais já gravados definitivamente irrecuperáveis. Não existe mecanismo de
recuperação, e isso é uma propriedade desejada do modelo: significa que nem
mesmo quem obtiver acesso ao banco de dados consegue ler os dados sem a chave. A
mitigação é organizacional — cópia guardada fora do repositório, em local
combinado pelo grupo.

### 2.4. Uso do módulo `secrets` em vez de `random`

**Decisão.** As chaves são geradas por `secrets.token_bytes(32)`.

**Justificativa.** O módulo `random` do Python implementa um gerador
pseudoaleatório (Mersenne Twister) projetado para simulação estatística, não
para uso criptográfico. Sua sequência é determinística e previsível a partir de
observações suficientes da saída. O módulo `secrets` usa a fonte de entropia do
sistema operacional, adequada para material criptográfico.

Um teste automatizado verifica que o código do gerador não contém referência ao
módulo `random`, impedindo que essa escolha seja revertida por engano.

### 2.5. TLS obrigatório inclusive em ambiente local

**Decisão.** A aplicação só aceita conexões HTTPS, mesmo em desenvolvimento, com
certificado autoassinado gerado por OpenSSL.

**Justificativa.** O ambiente de referência do projeto é local, e a publicação na
internet é desejável mas não é premissa. Exigir TLS também localmente mantém o
requisito de protocolo seguro de comunicação independente da hospedagem, e
garante que o código da aplicação nunca seja escrito assumindo um canal em texto
claro — o que exigiria adaptação posterior e criaria oportunidade de erro.

O certificado é autoassinado, então o navegador exibe um alerta. **Esse alerta é
esperado e correto:** ele informa que nenhuma autoridade certificadora atesta a
identidade do servidor, não que a conexão esteja desprotegida. O tráfego é
cifrado da mesma forma.

### 2.6. Restrição de TLS às versões 1.2 e 1.3

**Decisão.** O contexto TLS define a versão mínima como TLS 1.2.

**Justificativa.** TLS 1.0 e 1.1 possuem fraquezas conhecidas e foram formalmente
depreciados. Restringir desde já significa que a verificação com `testssl.sh`,
prevista para a Etapa 6, não encontrará esse achado — a correção foi aplicada
antes de a vulnerabilidade existir.

### 2.7. Inclusão de `subjectAltName` no certificado

**Decisão.** O comando de geração do certificado inclui a extensão
`subjectAltName`, além do `CN` previsto no plano inicial.

**Justificativa.** Navegadores modernos ignoram o campo `CN` para validação de
nome de servidor desde 2017 e exigem o `subjectAltName`. Sem essa extensão, o
Chrome e o Firefox recusam a conexão mesmo após o usuário aceitar o alerta de
certificado, com o erro `ERR_CERT_COMMON_NAME_INVALID`.

Esta é uma **correção de compatibilidade**, não uma mudança de decisão
arquitetural: o plano previa certificado autoassinado por OpenSSL, e é
exatamente isso que foi gerado.

### 2.8. Falha ruidosa na ausência de configuração

**Decisão.** A aplicação encerra com erro se faltar qualquer variável
obrigatória, em vez de adotar um valor padrão interno.

**Justificativa.** O padrão alternativo — usar uma chave de sessão fixa embutida
no código quando a variável está ausente — é uma das falhas mais comuns e mais
difíceis de detectar em aplicações web. O sistema funciona normalmente, os testes
passam, e só muito depois se descobre que toda sessão de produção foi assinada
com uma chave pública e conhecida.

Parar a execução torna o problema imediatamente visível e impossível de ignorar.

### 2.9. Separação do Semgrep em ambiente virtual próprio

**Decisão.** O Semgrep foi instalado em um ambiente virtual separado
(`venv-ferramentas/`), com seu próprio arquivo de dependências, em vez de
conviver com as bibliotecas da aplicação.

**Justificativa.** O Semgrep traz aproximadamente 45 dependências próprias —
entre elas `uvicorn`, `starlette`, `opentelemetry` e `mcp` — que a aplicação
VillaJu não utiliza. Instalado no mesmo ambiente, ele elevava a contagem de
pacotes de **57 para 105**.

A consequência foi medida e é concreta: o `pip-audit`, executado nesse ambiente
inflado, passou a reportar **duas vulnerabilidades conhecidas** (`CVE-2026-59951`
em `mcp` e `CVE-2026-114603` em `opentelemetry-api`). Nenhuma delas afeta o
VillaJu, porque nenhuma dessas bibliotecas é usada pelo sistema — mas ambas
apareceriam no Relatório 1 de análise estática, exigindo justificativa para
vulnerabilidades que não pertencem ao software analisado.

Com a separação, o ambiente da aplicação voltou a 57 pacotes e o `pip-audit`
voltou a reportar nenhuma vulnerabilidade. O `pytest`, o `bandit` e o
`pip-audit` permaneceram no ambiente principal, por serem leves e por
precisarem enxergar as dependências reais da aplicação.

**Observação.** Durante a investigação verificou-se que o Semgrep **funciona
nativamente no Windows**, contrariando a suposição inicial do grupo de que
exigiria WSL ou contêiner. A varredura executou 154 regras dos conjuntos
`p/owasp-top-ten` e `p/flask` sem nenhum achado.

### 2.10. Normalização de fim de linha por `.gitattributes`

**Decisão.** Um arquivo `.gitattributes` versionado define `* text=auto` e fixa
fim de linha LF para arquivos `.sh` e `.sql`.

**Justificativa.** A configuração `core.autocrlf` é local de cada máquina e varia
conforme as opções escolhidas na instalação do Git. Com cinco integrantes, essa
divergência produz diferenças falsas no `git diff`, em que arquivos inteiros
aparecem como alterados sem nenhuma mudança real de conteúdo. Regras versionadas
valem igualmente para todos.

A fixação de LF para `.sh` previne um problema específico: os scripts de backup e
restauração previstos para a Etapa 7 falham em sistemas Linux se gravados com
terminador CRLF.

**Nota de honestidade metodológica.** A justificativa inicialmente apresentada
pelo grupo era mais forte do que os fatos sustentavam. Testou-se explicitamente
a hipótese de que um script com CRLF falharia no ambiente de desenvolvimento, e
**ela não se confirmou**: o Git Bash no Windows tolera o caractere `\r`. A
medida foi mantida como prevenção de baixo custo e pela consistência entre
máquinas, não como correção de um defeito ativo.

### 2.11. Separação entre texto e código quanto à acentuação

**Decisão.** Comentários, docstrings e mensagens ao usuário são escritos em
português correto e acentuado. Identificadores, caminhos de arquivo e strings
usadas em comparação permanecem em ASCII. A separação é verificada por **39
testes automatizados**.

**Justificativa.** A acentuação melhora a legibilidade da documentação embutida
no código, que será lida por avaliadores. Entretanto, acento em identificador ou
em caminho de arquivo causa falhas de diagnóstico difícil: o nome de um
parâmetro deixa de corresponder ao argumento da função, um caminho deixa de
existir no sistema de arquivos, uma chave de dicionário nunca encontra seu
valor.

Esse problema **ocorreu de fato** durante a Etapa 0, ao aplicar a acentuação ao
código já escrito. Três defeitos foram introduzidos e corrigidos: dois nomes de
parâmetro de teste e um caminho de diretório. Os testes foram criados
precisamente para impedir a repetição.

### 2.12. Decisão de não versionar o documento de contexto

**Decisão.** O documento interno de especificação do projeto (`docs/contexto.md`)
foi incluído no `.gitignore`.

**Justificativa.** Trata-se de material interno de trabalho do grupo, não de
entregável. A regra foi escrita sem barra inicial, o que a faz valer em qualquer
diretório — mesmo que o arquivo seja movido.

**Consequência.** O `README.md` passa a ser a única documentação disponível a
quem clona o repositório. Por isso ele foi escrito com esse peso: cobre o
problema real da instituição parceira, as decisões de arquitetura com suas
justificativas técnicas, a instalação passo a passo e a tabela de proteções
implementadas.

---

## 3. Estado do que foi planejado

Esta seção compara o previsto no plano inicial com o que o protótipo entrega.

### 3.1. Comparação entre o previsto e o entregue

| Tarefa prevista no plano | Situação | Observação |
|---|---|---|
| Instalar Python 3.11+, MySQL Server e Workbench | **Concluída** | Python 3.12.3, MySQL 8.0.43 |
| Criar e ativar ambiente virtual | **Concluída** | — |
| Criar `requirements.txt` com versões fixadas | **Concluída** | 10 dependências diretas |
| Criar a estrutura de diretórios | **Concluída** | Conforme a seção 8 do plano |
| Criar `.gitignore` antes de qualquer arquivo | **Concluída** | Primeiro commit do repositório |
| Criar `.env.example` sem segredo real | **Concluída** | 13 variáveis documentadas |
| Escrever `scripts/gerar_chaves.py` | **Concluída** | Com 38 testes de verificação |
| Gerar o `.env` local de cada integrante | **Parcial** | Feito por 1 de 5 integrantes |
| Gerar certificado autoassinado | **Concluída** | RSA 4096, com `subjectAltName` |
| Inicializar o repositório e configurar o remoto | **Concluída** | 19 commits publicados |
| Escrever `run.py` mínimo com TLS | **Concluída** | Responde `ok` em `/` |
| Validar acesso a `https://localhost:5000` | **Parcial** | Validado em 1 de 5 máquinas |
| Instalar e testar bandit, semgrep, pip-audit, gitleaks | **Concluída** | As quatro executadas sem achados |

### 3.2. O que foi além do planejado

Três entregas não constavam do plano original e foram acrescentadas por se
mostrarem necessárias durante a execução.

**Suíte de 145 testes automatizados.** O plano previa que a Etapa 0 terminasse
com o `pytest` executando sem erro, ainda que sem testes. Optou-se por
implementar uma suíte que verifica a integridade do próprio ambiente e do
repositório, pelos motivos expostos nas seções 2.2 e 2.11. Sem ela, as
propriedades de segurança da Etapa 0 dependeriam apenas da disciplina dos
integrantes.

**Separação do Semgrep em ambiente próprio.** Descrita na seção 2.9. A
necessidade só se revelou após a medição do impacto da instalação.

**Arquivo `.gitattributes`.** Descrito na seção 2.10.

### 3.3. O que mudou em relação ao plano, e por quê

| Mudança | Motivo |
|---|---|
| Inclusão de `subjectAltName` no certificado | Navegadores atuais recusam certificado sem essa extensão (seção 2.7) |
| Inclusão de `python-dotenv` entre as dependências | Meio padrão de carregar o `.env` sem embutir segredo no código; licença BSD, compatível com o critério de software livre |
| Criação de `requirements-ferramentas.txt` | Separação do Semgrep (seção 2.9) |
| `docs/contexto.md` excluído do versionamento | Decisão do grupo (seção 2.12) |
| Abandono do padrão Conventional Commits | Decisão do grupo durante a execução; os 4 primeiros commits mantêm o padrão original, os 15 seguintes usam descrição curta em português |
| Etapa 1 adiada de 24/10 para 26/10 | Decisão de calendário do grupo; a etapa passa de 5 para 3 dias |

**Observação sobre o padrão de commits.** A seção 9 do plano inicial estabelece o
formato Conventional Commits (`tipo(escopo): descrição`). O grupo optou, durante
a Etapa 0, por descrições curtas sem prefixo. A divergência está registrada aqui
para que o histórico do repositório e a documentação acadêmica não se
contradigam. Recomenda-se atualizar a seção 9 do plano inicial.

### 3.4. Riscos identificados e seu tratamento

| Risco previsto no plano | Tratamento aplicado |
|---|---|
| Divergência de versão de Python ou MySQL entre as máquinas | Versões fixadas no `requirements.txt` e **verificadas por teste automatizado** que compara o instalado com o exigido e indica o comando de correção |

**Risco não previsto que se materializou.** Durante a aplicação da acentuação ao
código, três identificadores e caminhos foram corrompidos. O problema foi
detectado pela suíte de testes existente, corrigido, e um novo conjunto de 39
testes foi criado para impedir a recorrência (seção 2.11).

---

# PARTE II — O PLANO DE TESTES

---

## 1. Escopo e estratégia

### 1.1. O que é testado nesta etapa

Como a Etapa 0 não implementa funcionalidade de negócio, o escopo dos testes é
diferente do habitual. Testam-se as **propriedades do ambiente e do repositório**
das quais toda a segurança posterior depende:

| Área | O que se verifica |
|---|---|
| **Ambiente de execução** | Versão do Python, presença e versão exata de cada dependência |
| **Higiene do repositório** | Eficácia do `.gitignore`, ausência de segredo entre os arquivos rastreados e no histórico |
| **Gestão de chaves** | Qualidade criptográfica, unicidade e correção do gerador; ausência de valor real no modelo público |
| **Canal de comunicação** | Validade do certificado, correspondência entre chave e certificado, recusa de versões obsoletas de TLS |
| **Configuração da aplicação** | Endurecimento do cookie de sessão, origem da chave de sessão, recusa de subir sem configuração |
| **Integridade do código** | Ausência de acento em identificadores e caminhos; codificação UTF-8 |

### 1.2. Estratégia e tipos de teste empregados

| Tipo | Aplicação nesta etapa |
|---|---|
| **Testes unitários** | Funções isoladas: geração de chave, criação do contexto TLS, verificação de configuração |
| **Testes de integração** | Aplicação Flask respondendo pela sua interface de teste; execução do gerador de chaves como subprocesso |
| **Testes de segurança** | Eficácia do `.gitignore`, ausência de segredo no histórico, recusa de TLS obsoleto, atributos do cookie |
| **Testes de ambiente** | Divergência de versão entre máquinas, codificação de arquivo |
| **Análise estática** | Bandit, Semgrep, pip-audit sobre o código e as dependências |
| **Varredura de segredos** | gitleaks sobre o histórico completo do Git |

### 1.3. Ordem de prioridade

A prioridade seguiu o custo de uma falha não detectada:

1. **Vazamento de segredo** — prioridade máxima, porque é irreversível num
   repositório público. Um segredo commitado não é removível por commit
   posterior.
2. **Falha de canal seguro** — certificado inválido ou TLS obsoleto comprometem
   toda a comunicação.
3. **Qualidade do material criptográfico** — uma chave fraca anula a cifragem
   dos dados pessoais prevista para a Etapa 2.
4. **Divergência de ambiente** — não compromete segurança, mas consome prazo em
   diagnóstico de erro obscuro.
5. **Integridade do código** — acento em identificador quebra execução.

### 1.4. Método de validação dos próprios testes

Um teste que nunca falha não oferece proteção alguma: ele produz a aparência de
verificação sem a verificação. Por isso, **todo teste de proteção criado nesta
etapa foi validado por sabotagem deliberada** — o alvo foi quebrado de propósito
e se confirmou que o teste correspondente falha, com mensagem que identifica o
problema.

| Sabotagem aplicada | Teste que detectou | Resultado |
|---|---|---|
| Remoção das regras `.env` e `.env.*` do `.gitignore` | `test_gitignore_bloqueia_arquivo_sensivel` | 3 falhas, nomeando cada variante |
| Remoção das regras `certs/` e `*.key` | `test_gitignore_bloqueia_arquivo_sensivel` | 1 falha |
| Chave real de 32 bytes plantada no `.env.example` | `test_env_example_nao_contem_chave_real` | detectada |
| Placeholder não substituído no `.env` | `test_env_local_tem_chave_valida` | detectada |
| Versão mínima de TLS rebaixada para 1.0 | `test_contexto_tls_recusa_versoes_obsoletas` | detectada |
| Cookie de sessão sem o atributo `Secure` | `test_cookie_de_sessao_endurecido` | detectada |
| Certificado gerado sem `subjectAltName` | `test_certificado_cobre_localhost` | detectada |
| Caminho de arquivo acentuado | `test_caminhos_de_arquivo_sao_ascii` | detectada |
| Nome de parâmetro de teste acentuado | `test_nomes_de_parametrize_sao_ascii` | detectada |
| Nome de função acentuado | `test_identificadores_sao_ascii` | detectada |
| Valor acentuado no `.env.example` | `test_configuracao_so_tem_acento_em_comentario` | detectada |

As onze sabotagens foram detectadas pelo teste correto, e o ambiente foi
restaurado ao estado original em cada caso.

### 1.5. Cobertura atual

| Arquivo de teste | Casos | Área coberta |
|---|---|---|
| `testes/test_ambiente.py` | 21 | Versão do Python, dependências, divergência entre máquinas |
| `testes/test_repositorio.py` | 36 | Estrutura de diretórios e proteção contra vazamento |
| `testes/test_configuracao.py` | 38 | Chaves criptográficas e modelo de configuração |
| `testes/test_codigo_sem_acento.py` | 39 | Separação entre texto e código |
| `testes/test_tls.py` | 11 | Certificado e configuração segura da aplicação |
| **Total** | **145** | |

---

## 2. Casos de teste

A notação abaixo descreve, para cada cenário, a entrada, a execução e o
resultado esperado.

### 2.1. Ambiente de execução

| ID | Cenário | Entrada | Resultado esperado |
|---|---|---|---|
| AMB-01 | Versão do Python adequada | Interpretador em uso | Versão igual ou superior a 3.11 |
| AMB-02 | Dependência instalada | Nome de cada um dos 7 módulos de aplicação | Importação bem-sucedida |
| AMB-03 | Algoritmo AES-GCM disponível | — | Geração de chave de 256 bits bem-sucedida |
| AMB-04 | Variante correta do Argon2 | Instância padrão de `PasswordHasher` | Tipo é `Argon2id`, não `Argon2i` nem `Argon2d` |
| AMB-05 | Toda dependência com versão fixada | `requirements.txt` | Toda linha usa `==`; pelo menos 10 pacotes |
| AMB-06 | Versão instalada corresponde à exigida | Cada um dos 10 pacotes | Versão instalada idêntica à do `requirements.txt` |

### 2.2. Higiene do repositório

| ID | Cenário | Entrada | Resultado esperado |
|---|---|---|---|
| REP-01 | Diretório previsto existe | 12 diretórios da estrutura | Todos presentes |
| REP-02 | Pacote Python tem `__init__.py` | 4 pacotes | Todos presentes |
| REP-03 | Arquivo sensível é bloqueado | 11 caminhos (`.env`, `certs/*.key`, `*.pem`, dumps, `venv/`) | Todos ignorados pelo Git |
| REP-04 | Arquivo legítimo não é bloqueado | 7 caminhos (`.env.example`, `README.md`, scripts SQL) | Nenhum ignorado |
| REP-05 | Nenhum arquivo sensível rastreado | Lista de arquivos rastreados | Nenhuma extensão de chave, certificado ou dump |
| REP-06 | Nenhum segredo no histórico | Todos os commits de todas as referências | Nenhum arquivo sensível em nenhum commit |

### 2.3. Gestão de chaves e configuração

| ID | Cenário | Entrada | Resultado esperado |
|---|---|---|---|
| CFG-01 | Modelo declara toda variável exigida | `.env.example` | As 10 variáveis obrigatórias presentes |
| CFG-02 | Modelo não contém chave real | Valor de cada uma das 3 chaves | Nenhum decodifica para 32 bytes de base64 |
| CFG-03 | Modelo está versionado | `.env.example` | Rastreado pelo Git |
| CFG-04 | Gerador produz as três chaves | Execução do script | Exatamente as 3 variáveis esperadas |
| CFG-05 | Chave tem 256 bits | Cada chave gerada | Decodifica para exatamente 32 bytes |
| CFG-06 | Chaves da mesma execução são distintas | Uma execução | 3 valores diferentes entre si |
| CFG-07 | Execuções diferentes produzem chaves diferentes | Duas execuções | Conjuntos disjuntos |
| CFG-08 | Gerador não usa `random` | Código-fonte do script | Nenhuma referência ao módulo `random`; usa `secrets.token_bytes` |
| CFG-09 | Gerador não grava arquivo | Código-fonte do script | Nenhuma chamada de escrita em arquivo |
| CFG-10 | `.env` local está protegido | `.env` do integrante | Ignorado pelo Git |
| CFG-11 | `.env` local define toda variável | `.env` do integrante | As 10 obrigatórias presentes |
| CFG-12 | `.env` local tem chave válida | Cada chave do `.env` | Sem placeholder; decodifica para 32 bytes |
| CFG-13 | `.env` local tem três chaves distintas | `.env` do integrante | 3 valores diferentes |

### 2.4. Canal de comunicação

| ID | Cenário | Entrada | Resultado esperado |
|---|---|---|---|
| TLS-01 | Certificado e chave formam par | `villaju.crt` e `villaju.key` | Carregamento no contexto TLS bem-sucedido |
| TLS-02 | Certificado não está vencido | Certificado local | Data atual dentro do período de validade |
| TLS-03 | Certificado cobre o projeto inteiro | Certificado local | Mais de 60 dias de validade restantes |
| TLS-04 | Chave tem tamanho adequado | Chave privada | 2048 bits ou mais |
| TLS-05 | Certificado cobre `localhost` | Extensão `subjectAltName` | Contém `localhost` e `127.0.0.1` |
| TLS-06 | Certificado não está versionado | Conteúdo de `certs/` | Nenhum arquivo rastreado pelo Git |
| TLS-07 | Versões obsoletas recusadas | Contexto TLS da aplicação | Versão mínima igual ou superior a TLS 1.2 |

### 2.5. Configuração segura da aplicação

| ID | Cenário | Entrada | Resultado esperado |
|---|---|---|---|
| APP-01 | Cookie de sessão endurecido | Configuração do Flask | `HttpOnly` e `Secure` verdadeiros; `SameSite` igual a `Lax` |
| APP-02 | Chave de sessão vem do ambiente | Código de `run.py` | Lê de variável de ambiente; nenhum valor embutido |
| APP-03 | Aplicação responde | `GET /` | HTTP 200 com corpo `ok` |
| APP-04 | Recusa subir sem chave de sessão | Ambiente sem `VILLAJU_SECRET_KEY` | Encerra com código diferente de zero e nomeia a variável ausente |

### 2.6. Integridade do código

| ID | Cenário | Entrada | Resultado esperado |
|---|---|---|---|
| COD-01 | Identificadores em ASCII | Cada arquivo `.py` | Nenhum caractere não-ASCII fora de comentário e string |
| COD-02 | Caminhos em ASCII | Cada arquivo `.py` | Nenhuma string com aparência de caminho contendo acento |
| COD-03 | Nomes de parâmetro em ASCII | Cada arquivo `.py` | Nenhum nome de `parametrize` acentuado |
| COD-04 | Configuração com acento só em comentário | 5 arquivos de configuração | Nenhuma regra ou valor acentuado |
| COD-05 | Codificação UTF-8 | Todos os arquivos | Leitura em UTF-8 bem-sucedida |

### 2.7. Análise estática e varredura de segredos

| ID | Ferramenta | Alvo | Resultado obtido |
|---|---|---|---|
| EST-01 | Bandit 1.9.4 | `app/`, `scripts/`, `run.py` | Nenhum achado |
| EST-02 | Semgrep 1.182.1 (154 regras: OWASP Top Ten e Flask) | Todo o código | Nenhum achado |
| EST-03 | pip-audit 2.10.1 | Ambiente da aplicação | Nenhuma vulnerabilidade conhecida |
| EST-04 | gitleaks 8.30.1 | Histórico completo do Git | Nenhum vazamento em 19 commits |

**Validação do gitleaks.** Para confirmar que a ausência de achados reflete um
repositório limpo e não uma ferramenta inoperante, plantou-se um conjunto de
segredos realistas num diretório de teste: chave de acesso AWS, token do GitHub
e chave privada RSA. A ferramenta detectou os três.

Registra-se também que a chave de exemplo da documentação da AWS
(`AKIAIOSFODNN7EXAMPLE`) **não** é detectada, por constar da lista de exceções da
própria ferramenta — comportamento correto e documentado.

---

## 3. Critérios de aceitação

### 3.1. Critérios da Etapa 0

A etapa é considerada concluída quando todas as condições abaixo são
verdadeiras.

| # | Critério | Situação |
|---|---|---|
| 1 | A suíte de testes executa integralmente sem falha | **Atendido** — 145 aprovados |
| 2 | A aplicação sobe com TLS e responde em `/` | **Atendido** — `ok`, HTTP 200 sobre TLS 1.3 |
| 3 | Nenhum segredo presente no histórico do Git | **Atendido** — gitleaks sem achados em 19 commits |
| 4 | Nenhum achado de severidade alta nas ferramentas de análise estática | **Atendido** — Bandit, Semgrep e pip-audit sem achados |
| 5 | O `requirements.txt` reproduz o ambiente em instalação limpa | **Atendido** — validado em ambiente virtual separado |
| 6 | Toda proteção verificada por teste tem sua detecção comprovada por sabotagem | **Atendido** — 11 sabotagens, 11 detecções |
| 7 | O repositório contém documentação suficiente para instalação a partir do zero | **Atendido** — `README.md` |
| 8 | Os cinco integrantes conseguem subir o ambiente | **Pendente** — validado em 1 de 5 máquinas |

### 3.2. Critério geral de aceitação de funcionalidade

Para as etapas seguintes, uma funcionalidade é considerada aprovada quando:

1. Todos os seus casos de teste automatizados passam.
2. Os caminhos de erro são testados, não apenas o caminho feliz.
3. Nenhuma ferramenta de análise estática aponta achado de severidade alta no
   código novo.
4. Toda proteção de segurança introduzida tem sua detecção comprovada por
   sabotagem deliberada.
5. A funcionalidade foi revisada por um integrante que não a escreveu.

---

## 4. Trabalho futuro

### 4.1. Pendências imediatas da Etapa 0

| Pendência | Responsável | Prazo |
|---|---|---|
| Os quatro demais integrantes clonarem o repositório e montarem o ambiente | Grupo | Antes da Etapa 1 |
| Preencher a tabela de ambientes do `README.md` com as versões de cada máquina | Grupo | Antes da Etapa 1 |
| Definir a senha do usuário de banco `villaju_app` | Grupo | Etapa 1 |
| Confirmar com a responsável a capacidade exata de cada chalé | Dimas Marcondes | Etapa 1 |
| Confirmar os horários de entrada e saída | Dimas Marcondes | Etapa 1 |

### 4.2. Testes previstos para as etapas seguintes

**Etapa 1 — Banco de dados e triggers.** Treze casos obrigatórios sobre a regra
de não-sobreposição de reservas, entre os quais: reserva idêntica a uma
existente, reserva que começa no meio de outra, reserva que engloba outra por
completo, reserva que começa exatamente no dia de saída de outra (que **deve** ser
aceita, por se usar intervalo semiaberto), reserva conflitante com outra já
cancelada (que **deve** ser aceita), reserva acima da capacidade do chalé,
`UPDATE` que move uma reserva para cima de outra, e `UPDATE` que altera apenas a
observação da própria reserva sem gerar falso positivo de conflito consigo
mesma. Também: geração correta de registro em `log_auditoria` e verificação de
que nenhuma linha do log contém conteúdo de campo cifrado.

**Etapa 2 — Núcleo de segurança.** Ida e volta da cifragem para texto com
acento, emoji e string vazia; rejeição de ciphertext adulterado em um bit;
rejeição de tag de autenticação adulterada; rejeição de dado adicional
autenticado incorreto; ausência de repetição de nonce em dez mil operações de
cifragem; determinismo e normalização do índice HMAC; verificação de senha
correta e rejeição de senha incorreta; retorno de código 403 e registro de
`ACESSO_NEGADO` ao acessar rota administrativa com sessão de recepcionista;
rejeição de formulário enviado sem token CSRF.

**Etapa 3 — Reservas e disponibilidade.** Todos os casos de sobreposição da
Etapa 1, agora pela aplicação e com mensagem compreensível; consulta de
disponibilidade em período livre, parcialmente ocupado e totalmente ocupado;
cálculo de valor total em período de tarifa única e em período que atravessa a
virada de temporada; liberação do período após cancelamento; restrições por
perfil de acesso.

**Etapa 4 — Relatórios e análise estática.** Produção do Relatório 1, com os
achados das ferramentas de análise estática, da varredura de segredos e da
verificação de dependências, somados à revisão por pares.

**Etapa 5 — Página pública.** Gravação de envio válido do formulário; rejeição de
envio sem token CSRF; descarte silencioso de envio com campo-armadilha
preenchido; bloqueio por limitação de taxa; escape de conteúdo com `<script>` na
tela interna; ausência de exposição de dados de hóspede ou de reserva nas rotas
públicas.

**Etapa 6 — Análise dinâmica.** Produção do Relatório 2, com varredura
automatizada pelo OWASP ZAP contra a aplicação em execução, teste de injeção com
`sqlmap`, verificação da configuração TLS com `testssl.sh` e testes manuais de
abuso: injeção de SQL, XSS, CSRF, quebra de controle de acesso por URL direta,
acesso a reservas de outro hóspede por alteração de identificador, e tentativa de
reserva conflitante por manipulação de campo oculto.

**Etapa 7 — Validação e empacotamento.** Teste de restauração completa do backup
em banco limpo, requisito não funcional que não pode ser omitido; instalação do
sistema do zero em máquina limpa seguindo apenas a documentação.

### 4.3. Limitações conhecidas do protótipo atual

| Limitação | Natureza |
|---|---|
| O protótipo não acessa o banco de dados | Previsto — o modelo de dados é da Etapa 1 |
| Não há autenticação nem controle de acesso | Previsto — Etapa 2 |
| O servidor é o de desenvolvimento do Flask | Aceito — o ambiente de referência é local; servidor WSGI de produção está fora do escopo obrigatório |
| O certificado é autoassinado | Aceito — certificado emitido por autoridade certificadora paga está explicitamente fora do escopo |
| O modo de depuração está ativo por padrão | Controlado por variável de ambiente; deve ser desativado antes da varredura da Etapa 6 e da apresentação |
| O ambiente foi validado em uma única máquina | A ser resolvido antes da Etapa 1 |

---

# ANEXOS

## Anexo A — Ambiente de desenvolvimento validado

| Componente | Versão | Licença |
|---|---|---|
| Sistema operacional | Windows 11 | proprietária |
| Python | 3.12.3 | PSF |
| MySQL Community Server | 8.0.43 | GPLv2 |
| Git | 2.43.0 | GPLv2 |
| OpenSSL | 3.1.4 | Apache 2.0 |
| Semgrep OSS | 1.182.1 | LGPL 2.1 |
| gitleaks | 8.30.1 | MIT |

O serviço do MySQL foi configurado com inicialização automática e o diretório de
binários foi acrescentado à variável de ambiente `PATH`, para que os comandos
`mysql` e `mysqldump` fiquem disponíveis — este último é requisito dos scripts
de backup previstos para a Etapa 7.

**Observação registrada para os demais integrantes.** O MySQL Workbench é apenas
o cliente gráfico e **não** inclui o servidor de banco de dados. A instalação
deve ser feita pelo MySQL Installer, que entrega os dois.

## Anexo B — Comandos de verificação

```powershell
# Suíte completa de testes
.\venv\Scripts\activate
pytest

# Análise estática de padrões inseguros
bandit -r app/ scripts/ run.py

# Dependências com vulnerabilidade conhecida
pip-audit

# Segredos no histórico completo do Git
gitleaks git . --log-opts="--all" --redact

# Análise por regras (ambiente separado)
.\venv-ferramentas\Scripts\activate
semgrep --config=p/owasp-top-ten --config=p/flask --metrics=off app/ run.py scripts/
```

## Anexo C — Histórico de commits da Etapa 0

| # | Identificador | Descrição |
|---|---|---|
| 1 | `c90a576` | adiciona .gitignore antes de qualquer outro arquivo |
| 2 | `43bf666` | exclui contexto.md do versionamento |
| 3 | `e696045` | define normalização de fim de linha |
| 4 | `c46cbdb` | cria estrutura de diretórios do projeto |
| 5 | `570b1e3` | fixa dependências em versões testadas |
| 6 | `ae23666` | configura pytest e valida ambiente de desenvolvimento |
| 7 | `e6972da` | verifica versões das dependências contra o requirements |
| 8 | `83b0682` | testa estrutura e protege gitignore contra vazamento |
| 9 | `6ec45ef` | adiciona gerador das três chaves criptográficas |
| 10 | `4e8e6eb` | adiciona modelo de configuração sem valores reais |
| 11 | `36a6a2a` | testa geração de chaves e modelo de configuração |
| 12 | `d271670` | sobe aplicação Flask mínima com TLS local |
| 13 | `8aca29f` | testa certificado TLS e configuração segura da aplicação |
| 14 | `44e17e1` | corrige acentuação em comentários e mensagens |
| 15 | `8622a62` | corrige nome de arquivo acentuado por engano no teste |
| 16 | `55eb96b` | impede acento em identificadores e caminhos de arquivo |
| 17 | `9ff782f` | separa Semgrep em ambiente próprio de ferramentas |
| 18 | `184f1aa` | escreve README com instalação e decisões do projeto |
| 19 | `791140e` | atualiza cronograma com início da Etapa 1 em 26/10 |

## Anexo D — Declaração de conduta ética nos testes

Todas as ferramentas de análise de segurança empregadas neste projeto — Bandit,
Semgrep, pip-audit, gitleaks e, nas etapas seguintes, OWASP ZAP, sqlmap e
testssl.sh — são executadas **exclusivamente contra o sistema desenvolvido pelo
grupo, em ambiente próprio e local**.

Nenhum teste foi ou será executado contra sistema da instituição parceira, de
terceiros, ou contra qualquer endereço que não seja a instância local do
projeto.

Os segredos usados para validar a ferramenta de varredura (chave de acesso AWS,
token do GitHub, chave privada RSA) foram **gerados aleatoriamente para esse
fim**, em diretório temporário fora do repositório, e descartados em seguida.
Nenhum deles corresponde a credencial real de qualquer serviço.
