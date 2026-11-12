# VillaJu — Sistema de Reservas e Divulgação

Sistema web de reservas para a **VillaJu**, hospedaria com cinco chalés na Praia
da Juréia, em São Sebastião (SP). Desenvolvido como atividade de extensão do
Projeto Integrador do Módulo 4.

**Pontifícia Universidade Católica de Campinas** — Escola Politécnica · Bacharelado em Cibersegurança (21853)

Projeto Integrador — Cibersegurança em Programação Segura

Professor orientador: Prof. Dr. Felix Carvalho Rodrigues

---

## O problema

A hospedaria não possui sistema próprio. O controle de reservas é manual e
disperso entre conversas de aplicativo e anotações, o que produz três problemas
concretos:

| Problema | Consequência |
|---|---|
| **Conflito de datas** | O mesmo chalé é reservado duas vezes para o mesmo período |
| **Perda de informação** | Histórico de hóspedes e reservas se perde, sem visão de ocupação |
| **Exposição de dados pessoais** | Documento, telefone e e-mail circulam sem proteção |

O sistema elimina os três.

---

## O que o sistema faz hoje

O VillaJu tem duas faces, e a separação entre elas é decisão de
segurança, não de organização.

### Face pública — não exige login

| Tela | O que faz |
|---|---|
| **Vitrine** | os cinco chalés, com comodidades e localização |
| **Consultar disponibilidade** | diz se um chalé está livre num período, sem cadastro |
| **Criar conta** | autocadastro do hóspede, confirmado por e-mail |
| **Falar com a recepção** | pedido de contato, sem exigir documento |

**Nenhuma delas mostra preço.** O valor aparece depois do cadastro
confirmado, na tela de reserva, onde a pessoa já está identificada.
Publicar a tabela faria a hospedaria perder a margem de negociar caso a
caso.

### Face interna — exige login, com três perfis

Cadastro de chalés e tarifas, consulta de disponibilidade, criação e
gestão de reservas com valor calculado noite a noite — inclusive na
virada de temporada —, atendimento dos pedidos de contato, caixa de
saída de e-mail e log de auditoria.

**Nenhuma tela da face interna cria hóspede.** Quem informa os dados
pessoais é a própria pessoa, pelo autocadastro público. A equipe
consulta, corrige e desativa.

### O fluxo do hóspede, de ponta a ponta

```
vitrine → consulta disponibilidade → cria conta → recebe o e-mail
   → abre o link → conta ativada → entra com o próprio e-mail
```

A conta nasce **inativa**. Só a abertura do link a ativa, e abrir o link
exige ter recebido a mensagem — o que só acontece para quem é dono do
endereço. **O e-mail não avisa: ele prova.**

Cadastro que não for confirmado no prazo é **descartado**, liberando o
endereço e o documento. Sem isso, uma letra errada no e-mail trancaria um
CPF indefinidamente — e a segunda tentativa seria recusada em silêncio,
pela regra de resposta uniforme.

---

## Decisões de arquitetura

Três decisões definem o projeto e sustentam a sua defesa técnica.

### 1. A regra de não-sobreposição mora no banco, não na aplicação

A verificação de conflito de reservas é implementada como `TRIGGER` no MySQL,
que interrompe a operação com `SIGNAL SQLSTATE '45000'`. A aplicação também
valida antes de gravar, mas apenas para produzir mensagem amigável — a garantia
real está no banco.

Validação feita somente em Python falha em três situações: quando duas
requisições chegam ao mesmo tempo, quando alguém opera o banco diretamente e
quando um caminho de código esquece a validação. A trigger cobre as três.

A comparação usa **intervalo semiaberto**, de modo que a saída de um hóspede
libere o chalé para a entrada de outro no mesmo dia.

### 2. Dado pessoal nasce cifrado

Nome, documento, telefone e e-mail do hóspede são cifrados com **AES-256-GCM**
antes de qualquer `INSERT`. O identificador do hóspede é usado como dado
adicional autenticado (AAD), de modo que um registro cifrado não possa ser
movido para outro hóspede sem que a verificação falhe.

Como o AES-GCM não é determinístico, buscar ou garantir unicidade sobre o campo
cifrado é impossível. A solução é guardar ao lado um **HMAC-SHA256** do valor
normalizado, com chave de índice separada da chave de cifragem. O HMAC é
determinístico, permite busca e restrição de unicidade, e não é reversível.

Hash simples não serviria: um CPF sem chave é quebrável por força bruta em
segundos.

### 3. Nenhum segredo no código-fonte

As três chaves criptográficas e a senha do banco vêm de variável de ambiente,
carregadas de um `.env` que nunca é versionado. O `.gitignore` foi o primeiro
arquivo do repositório, criado antes de qualquer código.

---

### 4. A face pública não tem como ler dado de pessoa

A face pública é o alvo mais exposto do projeto: não exige senha, é
indexada por buscadores e recebe tráfego automatizado o tempo todo. Se
uma rota dela tiver motivo para ler `hospede` ou `reserva`, qualquer
defeito nela passa a ser um defeito com acesso a dado pessoal.

Por isso ela alcança **dois módulos de porta**, e cada um tem uma
direção só:

| Módulo | Direção | Para quê |
|---|---|---|
| `app/servicos/cadastro.py` | escreve, não lê | criar a conta do hóspede |
| `app/dados/vitrine.py` | lê, não escreve | dizer se um chalé está livre |

Nenhuma função dos dois devolve registro de pessoa: o máximo que sai é um
booleano, uma contagem e uma lista de nomes de chalé — que já estão
impressos na própria vitrine.

**A regra é verificada, não prometida.** Um teste percorre a árvore de
importações inteira e **bloqueia a porta**, para fazer a pergunta certa:
*com ela fechada, o repositório ainda é alcançável?* Se não for, ela é o
único caminho — e isso vale para todos os caminhos, não para o primeiro
que a busca encontrar. Outro teste prova que o bloqueio funciona, para
que o anterior não passe de graça.

### 5. O e-mail prova a posse do endereço

O autocadastro cria a conta inativa e envia um link. Se o link aparecesse
na tela, bastaria digitar o endereço de outra pessoa para criar conta no
nome dela.

É também o que torna o autocadastro seguro contra enumeração: **a
resposta é sempre a mesma**, exista ou não o cadastro. Quem já tem conta
recebe um aviso de tentativa; quem não tem recebe o convite. Do lado de
fora, nada distingue os dois casos — nem a resposta, nem o código de
estado, nem o tempo.

O banco guarda o **hash** do token, nunca o token.

---

## Tecnologias

Todas de código aberto e gratuitas, executando localmente.

| Item | Escolha | Licença |
|---|---|---|
| Linguagem | Python 3.11+ | PSF |
| Framework web | Flask | BSD-3 |
| Banco de dados | MySQL Community Server 8.0 | GPLv2 |
| Driver | mysql-connector-python | GPLv2 + FOSS exception |
| Cifragem | cryptography (AES-256-GCM) | Apache 2.0 / BSD |
| Hash de senha | argon2-cffi (Argon2id) | MIT |
| CSRF | Flask-WTF | BSD-3 |
| Limitação de taxa | Flask-Limiter | MIT |
| TLS local | OpenSSL | Apache 2.0 |

### Ferramentas de teste e análise

| Ferramenta | Função |
|---|---|
| pytest | Testes automatizados |
| Bandit | Análise estática de padrões inseguros em Python |
| Semgrep OSS | Análise estática por regras (OWASP Top Ten, Flask) |
| pip-audit | Dependências com vulnerabilidade conhecida |
| gitleaks | Varredura de segredos no histórico do Git |

> **Regra ética.** Todas as ferramentas rodam exclusivamente contra o sistema
> desenvolvido pelo grupo, em ambiente próprio. Nenhum teste é executado contra
> sistema da parceira, de terceiros ou contra qualquer endereço que não seja a
> instância local do projeto.

---

## Instalação

### Pré-requisitos

- **Python 3.11 ou superior**
- **MySQL Community Server 8.0** — o MySQL Workbench sozinho **não** basta, ele
  é apenas o cliente gráfico. Instale pelo MySQL Installer, que traz o servidor.
- **Git**
- **OpenSSL** — já vem com o Git para Windows

### 1. Clonar e criar o ambiente virtual

```powershell
git clone https://github.com/dimasmmdasilva/projetoVillaJu.git
cd projetoVillaJu

python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Gerar as chaves e configurar o ambiente

```powershell
copy .env.example .env
python scripts\gerar_chaves.py
```

Cole a saída do script no `.env`, substituindo os três placeholders.

> **Atenção.** Perder `VILLAJU_CHAVE_CIFRAGEM` torna os dados já gravados
> irrecuperáveis. Não existe recuperação. Guarde cópia fora do repositório.

### 3. Gerar o certificado TLS local

O TLS é obrigatório em qualquer ambiente, inclusive local.

```bash
openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout certs/villaju.key -out certs/villaju.crt \
  -days 365 -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

No Git Bash, prefixe o comando com `MSYS_NO_PATHCONV=1`, senão o `/CN=localhost`
é convertido em caminho do Windows.

### 4. Subir a aplicação

```powershell
python run.py
```

Acesse `https://localhost:5000`.

O navegador vai alertar sobre o certificado. **O aviso é esperado e correto:**
ele informa que ninguém atesta a identidade do servidor, não que a conexão está
aberta. O tráfego é cifrado do mesmo jeito. Siga em "Avançado" e prossiga.

---

## Testes

```powershell
.\venv\Scripts\activate
pytest
```

A suíte cobre, além das regras do sistema, a integridade do próprio ambiente:
versão do Python, divergência de versão de dependência entre máquinas, proteção
do `.gitignore` contra vazamento de segredo, validade do certificado TLS e
ausência de acento em identificadores e caminhos de arquivo.

Todo teste de proteção foi validado por **sabotagem deliberada**: o alvo é
quebrado de propósito e se confirma que o teste falha. Teste que nunca
falha não protege nada.

O método encontrou, até aqui, quatro famílias de teste que não testavam
nada — e cada uma delas rendeu um teste novo:

| Família | Exemplo encontrado |
|---|---|
| Verifica a presença do certo, não a ausência do errado | um `flash` que não aparecia na tela mas ficava na sessão |
| Verifica no lugar errado | o argumento de `execute()` e não a constante; a tela e não a sessão; o primeiro nível e não a cadeia de importações |
| Verifica só o resultado, com proteção redundante | duas condições fazendo o mesmo trabalho: remover qualquer uma não mudava o resultado |
| Verifica presença quando o problema é **ordem** | a varredura rodava, mas depois das consultas — e quem pagava era quem errava uma letra no e-mail |

Quando uma sabotagem é indetectável porque **o banco já garante a
condição**, o teste honesto prende a invariante da trigger em vez de
inventar um cenário que o banco não aceita.

---

## Análise de segurança

```powershell
# Padrões inseguros em Python
bandit -r app/ scripts/ run.py

# Dependências com vulnerabilidade conhecida
pip-audit

# Segredos no histórico completo do Git
gitleaks git . --log-opts="--all" --redact
```

Achados declarados como falsos positivos ficam em `.gitleaksignore`, cada
um com a razão escrita e preso à impressão digital de um achado
específico — commit, arquivo, regra e linha. Não silenciam a regra nem o
arquivo: um vazamento de verdade no mesmo arquivo continua aparecendo.

A razão de declarar em vez de apagar: um achado falso faz *"gitleaks sem
achados"* deixar de ser um sinal utilizável, e reescrever histórico
publicado exigiria `push --force`.

O **Semgrep** vive em ambiente separado, porque traz cerca de 45 dependências
próprias que a aplicação não usa. Instalá-lo junto inflaria o ambiente de 57
para 105 pacotes e faria o `pip-audit` acusar vulnerabilidades da ferramenta, e
não do VillaJu.

```powershell
python -m venv venv-ferramentas
.\venv-ferramentas\Scripts\activate
pip install -r requirements-ferramentas.txt

semgrep --config=p/owasp-top-ten --config=p/flask --metrics=off app/ run.py scripts/
```

---

## Estrutura do repositório

```
projetoVillaJu/
├── app/
│   ├── __init__.py          # fábrica da aplicação Flask
│   ├── config.py            # configuração vinda do ambiente, validada na partida
│   ├── extensoes.py         # CSRF e limitação de taxa
│   ├── seguranca/           # cifragem AES-GCM, Argon2id, autorização por perfil
│   ├── dados/               # repositórios; toda consulta parametrizada
│   ├── servicos/            # correio (smtplib) e cadastro público
│   ├── rotas/               # face interna autenticada e face pública
│   ├── templates/           # Jinja2, escape automático, sem `|safe`
│   └── static/              # CSS próprio; a pasta js/ está vazia por decisão
├── banco/                   # bancos, schema, triggers, usuário de aplicação, massa
├── instance/emails/         # caixa de saída em modo arquivo (fora do Git)
├── testes/                  # conftest.py e 32 arquivos de teste
├── scripts/                 # geração de chaves e de massa cifrada
├── docs/                    # diagrama ER
├── certs/                   # certificado local (fora do Git)
├── .env                     # segredos locais (fora do Git)
├── .env.example             # modelo versionado, sem valor real
├── .gitleaksignore          # falsos positivos declarados, com a razão escrita
├── requirements.txt         # 10 dependências com versão fixada
├── requirements-ferramentas.txt
├── pytest.ini
├── run.py
└── README.md
```

A pasta `app/static/js/` existe e está vazia de propósito: a aplicação não
usa JavaScript nem recurso externo algum. Sem JavaScript não há superfície
de XSS baseada em DOM; sem CDN não há terceiro capaz de injetar código nas
páginas.

### O que nunca entra no repositório

`.env` · `certs/` · `*.key` · `*.crt` · `*.pem` · dumps de banco · `venv/`

---

## Proteções implementadas

| Ameaça | Proteção |
|---|---|
| SQL Injection | Consultas parametrizadas, sempre. Nenhuma f-string montando SQL. |
| XSS | Escape automático do Jinja2, sem desativação em conteúdo de usuário. |
| CSRF | Token em todo formulário, inclusive no público. |
| Quebra de controle de acesso | Verificação de perfil no servidor, em cada rota. |
| Força bruta | Limitação de taxa no login e no formulário público. |
| Fixação de sessão | Regeneração do identificador após o login. |
| Enumeração de usuários | Mensagem de erro idêntica para usuário inexistente e senha errada. |
| Enumeração pelo cadastro | Resposta idêntica para e-mail novo e já cadastrado. Quem é dono do endereço recebe um aviso; quem submeteu não aprende nada. |
| Envio automatizado | Campo-armadilha no formulário público e no de cadastro, com descarte **em silêncio**. |
| Sondagem de token | Resposta idêntica para token válido, vencido, inexistente e de outro tipo. Uso único. |
| Vazamento de e-mail pelo banco | O login do hóspede é o HMAC do endereço, nunca o endereço. A coluna é única e em texto claro. |
| Retenção indevida | Cadastro não confirmado no prazo é apagado, liberando endereço e documento. |
| Rastreamento por e-mail | Mensagens em texto puro: sem HTML, sem imagem remota, sem pixel de rastreio. |
| Interceptação | HTTPS com TLS 1.2 ou superior, em qualquer ambiente. |

O cookie de sessão usa `HttpOnly`, `Secure` e `SameSite=Lax`.

A política de conteúdo é restritiva — `default-src 'self'`, sem
`unsafe-inline` — e isso é aplicado também ao estilo. Uma versão anterior
escrevia cor em atributo `style=` e o navegador a bloqueou, deixando
blocos vazios na tela; a correção foi tirar o estilo do HTML, **não**
afrouxar a política.

---

## Situação do desenvolvimento

| Etapa | Período | Situação |
|---|---|---|
| 0 — Ambiente e repositório | 22/10 a 23/10 | concluída |
| 1 — Banco de dados e triggers | 26/10 | concluída |
| 2 — Núcleo de segurança | 04/11 a 05/11 | concluída |
| 3 — Reservas e disponibilidade | 05/11 a 06/11 | concluída |
| 4 — Página pública e contato | 06 e 07/11 | concluída |
| 5 — Autoatendimento do hóspede | 10 a 20/11 | **em andamento** |
| 6 — Relatórios e análise estática | a definir | a iniciar |
| 7 — Análise dinâmica | a definir | — |
| 8 — Validação e defesa | a definir | — |

### Etapa 5, bloco a bloco

| Bloco | O que é | Situação |
|---|---|---|
| 1 | Envio de e-mail, por SMTP ou arquivo | concluído |
| 2 | Tokens de confirmação, guardando só o hash | concluído |
| 3 | Autocadastro público com confirmação | concluído |
| 4 | Caminho único e descarte do não confirmado | concluído |
| 5 | Consulta pública de disponibilidade | concluído |
| 6 | Portal do hóspede — perfil, troca de senha, reservas separadas | concluído |
| 7 | Reserva com sinal de 50% e prazo de 24 h | a fazer |
| 8 | Controle de pagamentos no painel do administrador | a fazer |
| 9 | Cancelamento com cálculo da devolução | a fazer |
| 10 | Página de cada chalé, com galeria | concluído |
| 12 | Mensagem em HTML | opcional |
| 13 | Ajustes de interface | concluído |
| 14 | Recuperação de senha pelo link de e-mail | concluído |

> **O Bloco 11 — contas da equipe — saiu do escopo em 13/11.** Contas de
> administrador e recepcionista são provisionadas uma vez, na instalação,
> por `banco/04_dados_ficticios.sql` — são três pessoas numa hospedaria
> de cinco chalés. O RF07 e o RF08 continuam atendidos: eles exigem
> autenticação e os três perfis operando, e os três operam.
>
> Conta de **hóspede**, essa sim, é criada pela própria pessoa, com
> confirmação por e-mail — que era o buraco que deu origem a esta etapa.

**Entrega 2:** 11/11/2026 · **Entrega final:** 25/11/2026

> **Sobre o controle de pagamento.** Os Blocos 7 a 9 registram o sinal de
> 50% e contam o prazo de 24 horas, a pedido do professor. Ele aparece
> como **uma seção do painel do administrador**, e não como módulo
> financeiro: o sistema cuida do aluguel dos chalés, e o dinheiro é o
> estado de uma reserva. Faturamento, contas a receber e fechamento não
> estão no escopo e não vão estar.
>
> **O sistema não transaciona.** O Pix acontece inteiramente fora dele, entre o
> aplicativo do banco do hóspede e o da hospedaria; o que o sistema faz é
> registrar que alguém da equipe conferiu a entrada, contar o tempo e
> calcular a elegibilidade da devolução. Não há intermediário de
> pagamento, geração de cobrança, conciliação automática nem estorno
> programático — se um intermediário saísse do ar, nada aqui pararia,
> porque não existe intermediário.

---

## Equipe

Dimas Marcondes · Eduardo Malta · Gabriel Fuzinatto · Guilherme Leal ·
Murilo Dias Bartkevicius

---

## Instituição parceira

**VillaJu** — Alameda Jubéris, 48, Praia da Juréia, São Sebastião (SP)
Responsável pela parceria: Vania Silva, anfitriã
Chalés: Embaré, Camburi, Asturias, Boracéia e Dunas
