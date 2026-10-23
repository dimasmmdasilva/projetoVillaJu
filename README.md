# VillaJu — Sistema de Reservas e Divulgação

Sistema web de reservas para a **VillaJu**, hospedaria com cinco chalés na Praia
da Juréia, em São Sebastião (SP). Desenvolvido como atividade de extensão do
Projeto Integrador do Módulo 4.

**Pontifícia Universidade Católica de Campinas** — Escola Politécnica
Bacharelado em Cibersegurança (21853) · Projeto Integrador — Cibersegurança em
Programação Segura
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

Todo teste de proteção foi validado por sabotagem deliberada: o alvo é quebrado
de propósito e se confirma que o teste falha. Teste que nunca falha não protege
nada.

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
│   ├── seguranca/      # cifragem, hash de senha, autorização
│   ├── dados/          # repositórios; toda consulta parametrizada
│   ├── rotas/          # face interna (autenticada) e face pública
│   ├── templates/
│   └── static/
├── banco/              # schema, triggers, usuário de aplicação, dados fictícios
├── testes/
├── docs/
├── scripts/            # geração de chaves, backup, restauração
├── certs/              # certificado local (fora do Git)
├── .env                # segredos locais (fora do Git)
├── .env.example        # modelo versionado, sem valor real
├── requirements.txt
└── run.py
```

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
| Interceptação | HTTPS com TLS 1.2 ou superior, em qualquer ambiente. |

O cookie de sessão usa `HttpOnly`, `Secure` e `SameSite=Lax`.

---

## Ambientes do grupo

Versões registradas por integrante, para rastrear divergência.

| Integrante | Python | MySQL | Sistema |
|---|---|---|---|
| Dimas Marcondes | 3.12.3 | 8.0.43 | Windows 11 |
| Eduardo Malta | — | — | — |
| Gabriel Fuzinatto | — | — | — |
| Guilherme Leal | — | — | — |
| Murilo Dias Bartkevicius | — | — | — |

A suíte de testes falha com mensagem explícita quando a versão de uma
dependência diverge do `requirements.txt`.

---

## Situação do desenvolvimento

| Etapa | Período | Situação |
|---|---|---|
| 0 — Ambiente e repositório | 22/10 a 23/10 | concluída |
| 1 — Banco de dados e triggers | 26/10 a 28/10 | a iniciar |
| 2 — Núcleo de segurança | 28/10 a 31/10 | — |
| 3 — Reservas e disponibilidade | 31/10 a 04/11 | — |
| 4 — Relatórios e análise estática | 05/11 a 11/11 | — |
| 5 — Página pública | 11/11 a 17/11 | — |
| 6 — Análise dinâmica | 17/11 a 21/11 | — |
| 7 — Validação e defesa | 21/11 a 25/11 | — |

**Entrega 2:** 04/11/2026 · **Entrega final:** 25/11/2026

---

## Equipe

Dimas Marcondes · Eduardo Malta · Gabriel Fuzinatto · Guilherme Leal ·
Murilo Dias Bartkevicius

---

## Instituição parceira

**VillaJu** — Alameda Jubéris, 48, Praia da Juréia, São Sebastião (SP)
Responsável pela parceria: Vania Silva, anfitriã
Chalés: Embaré, Camburi, Asturias, Boracéia e Dunas
