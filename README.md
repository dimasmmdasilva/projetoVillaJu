# VillaJu — Sistema de Reservas e Divulgação

Sistema web de reservas para a **VillaJu**, pousada com cinco chalés na Praia
da Juréia, em São Sebastião (SP). Desenvolvido como atividade de extensão do
Projeto Integrador do Módulo 4.

**Pontifícia Universidade Católica de Campinas** — Escola Politécnica · Bacharelado em Cibersegurança (21853)

Projeto Integrador — Cibersegurança em Programação Segura

Professor orientador: Prof. Dr. Felix Carvalho Rodrigues

---

## O problema

A pousada não possui sistema próprio. O controle de reservas é manual e
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
| **Regulamento** | as regras de pagamento, prazo e cancelamento |
| **Fale com a pousada** | os contatos da pousada, em texto, sem formulário (provisórios) |

**Nenhuma delas mostra preço.** O valor aparece depois do cadastro
confirmado, na tela de reserva, onde a pessoa já está identificada.
Publicar a tabela faria a pousada perder a margem de negociar caso a
caso.

### Face interna — exige login, com três perfis

Cadastro de chalés e tarifas, consulta de disponibilidade, criação e
gestão de reservas com valor calculado noite a noite — inclusive na
virada de temporada —, conversa com o hóspede em cada reserva, caixa de
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

Todas gratuitas e executando localmente; todas de código aberto, exceto o
Docker Desktop, usado só para rodar duas ferramentas da análise dinâmica.
As versões são as fixadas em `requirements.txt` e as instaladas na máquina
de desenvolvimento.

| Item | Escolha | Versão | Licença |
|---|---|---|---|
| Linguagem | Python | 3.12 (mínimo 3.11, conferido pela suíte) | PSF |
| Framework web | Flask, com Jinja2 nos modelos | 3.1.3 · Jinja2 3.1.6 | BSD-3 |
| Banco de dados | MySQL Community Server, motor InnoDB | 8.0.43 | GPLv2 |
| Driver do banco | mysql-connector-python | 26.10.0 | GPLv2 + exceção FOSS |
| Cifragem | cryptography (AES-256-GCM) | 50.0.2 | Apache 2.0 / BSD |
| Hash de senha | argon2-cffi (Argon2id) | 25.1.0 | MIT |
| CSRF | Flask-WTF | 1.3.0 | BSD-3 |
| Limitação de taxa | Flask-Limiter | 4.1.1 | MIT |
| Variáveis de ambiente | python-dotenv (lê o `.env`) | 1.2.4 | BSD-3 |
| TLS local | OpenSSL (certificado de homologação) | 3.1.4 | Apache 2.0 |

HMAC, SHA-256, geração de números aleatórios seguros (`secrets`) e envio
de e-mail (`smtplib`) vêm da biblioteca padrão do Python, sem dependência
a mais.

### Ferramentas de teste e análise

| Ferramenta | Versão | Onde roda | Função |
|---|---|---|---|
| pytest | 9.1.1 | ambiente da aplicação | testes automatizados |
| Bandit | 1.9.4 | ambiente da aplicação | análise estática de padrões inseguros em Python |
| pip-audit | 2.10.1 | ambiente da aplicação | dependências com vulnerabilidade conhecida |
| Semgrep | 1.182.1 | ambiente separado (`venv-ferramentas`) | análise estática por regras (OWASP Top 10, Flask) |
| gitleaks | 8.30.1 | programa no Windows | segredos no histórico do Git |
| testssl.sh | 3.2.5 | contêiner Docker | configuração do TLS: protocolos, cifras, vulnerabilidades conhecidas |
| sqlmap | 1.10.11 | ambiente separado (`venv-ferramentas`) | injeção de SQL em cada ponto de entrada |
| OWASP ZAP | 2.17.0 | contêiner Docker | varredura da aplicação web, passiva e ativa |
| Docker Desktop | 29.7 | Windows | executa os contêineres do testssl e do ZAP |

> **Regra ética.** Todas as ferramentas rodam exclusivamente contra o sistema
> desenvolvido pelo grupo, em ambiente próprio. Nenhum teste é executado contra
> sistema da parceira, de terceiros ou contra qualquer endereço que não seja a
> instância local do projeto.

---

## Documentação

O passo a passo completo de cada tarefa está nos documentos abaixo. Este
README não resume nenhum deles, de propósito: um resumo envelhece separado
do original, e quem segue a versão curta perde justamente o porquê de cada
passo.

| Documento | Para quem | O que traz |
|---|---|---|
| [`docs/instalacao.md`](docs/instalacao.md) | quem vai instalar e manter o sistema | pré-requisitos; ambiente Python; o `.env` com todas as variáveis; o envio de e-mail (modo arquivo e Gmail); o certificado TLS; o banco, passo a passo; subir e conferir; testes; backup diário; restauração; como desfazer cada passo; o que fazer antes do uso real; problemas comuns |
| [`docs/roteiro_demonstracao.md`](docs/roteiro_demonstracao.md) | quem vai apresentar o sistema | a demonstração completa, passo a passo, com as contas e o que mostrar em cada tela |
| Página **Como usar**, dentro do sistema | a equipe da pousada | o uso do dia a dia, tarefa por tarefa; aparece no menu para recepção e administrador |
| [`docs/modelo_entidade_relacionamento.md`](docs/modelo_entidade_relacionamento.md) | quem vai estudar ou alterar o banco | o diagrama das 11 tabelas, as relações, os índices e o porquê de cada decisão de modelagem |

---

## Testes

Como rodar: [`docs/instalacao.md`](docs/instalacao.md), seção 7.

A suíte cobre, além das regras do sistema, a integridade do próprio ambiente:
versão do Python, divergência de versão de dependência entre máquinas, proteção
do `.gitignore` contra vazamento de segredo, validade do certificado TLS e
ausência de acento em identificadores e caminhos de arquivo.

Todo teste de proteção foi validado por **sabotagem deliberada**: o alvo é
quebrado de propósito e se confirma que o teste falha. Teste que nunca
falha não protege nada.

O método encontrou seis famílias de teste que não testavam nada — cinco
renderam um teste novo, e uma rendeu a remoção de código que não protegia
nada:

| Família | Exemplo encontrado |
|---|---|
| Verifica a presença do certo, não a ausência do errado | um `flash` que não aparecia na tela mas ficava na sessão |
| Verifica no lugar errado | o argumento de `execute()` e não a constante; a tela e não a sessão; o primeiro nível e não a cadeia de importações; o texto somado do log e não cada linha |
| Verifica só o resultado, com proteção redundante | duas condições fazendo o mesmo trabalho: remover qualquer uma não mudava o resultado |
| Verifica presença quando o problema é **ordem** | a varredura rodava, mas depois das consultas — e quem pagava era quem errava uma letra no e-mail |
| Verifica uma proteção **que não existe** | a "renovação" de sessão na troca de senha: a sessão do Flask vive no cookie, e o cookie antigo continuava valendo. O código saiu, e um teste passou a demonstrar a limitação — que a análise dinâmica depois transformou em correção, com a versão de sessão no banco |
| Passa **pelo motivo errado** | na fase final: o caso de abuso que tentava cancelar uma reserva alheia já cancelada; o teste do banco ocupado numa página que já tratava o erro sozinha; uma expressão regular gravada com um caractere de controle, que procurava um texto que nunca existe |

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
│   ├── servicos/            # regras de negócio: pedido, Pix, cadastro, correio...
│   ├── rotas/               # face interna autenticada e face pública
│   ├── templates/           # Jinja2, escape automático, sem `|safe`
│   └── static/              # CSS próprio; a pasta js/ está vazia por decisão
├── banco/                   # bancos, schema, triggers, usuário de aplicação, massa
├── instance/                # e-mails, backups, evidências (fora do Git)
├── testes/                  # conftest.py e 56 arquivos de teste
├── scripts/                 # chaves, massa cifrada, backup e restauração
├── docs/                    # guia de instalação, roteiro da demonstração, modelo de dados
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

`.env` · `certs/` · `*.key` · `*.crt` · `*.pem` · dumps de banco · `venv/` ·
`instance/` (e-mails do modo arquivo, backups e o `backup.cnf` com a senha
do backup)

---

## Proteções implementadas

| Ameaça | Proteção |
|---|---|
| SQL Injection | Consultas parametrizadas, sempre. Nenhuma f-string montando SQL. |
| XSS | Escape automático do Jinja2, sem desativação em conteúdo de usuário. |
| CSRF | Token em todo formulário, inclusive no público, com conferência da origem do envio. |
| Quebra de controle de acesso | Verificação de perfil no servidor, em cada rota; o dono da reserva vai na própria condição do SQL, e trocar o número no endereço não acha nada. |
| Força bruta | Limitação de taxa no login e nos formulários públicos (5 tentativas de login por minuto). |
| Fixação de sessão | A sessão é limpa e recriada no login. |
| Cookie de sessão copiado | Sair, trocar ou redefinir a senha invalidam **todos** os cookies anteriores da conta, por uma versão de sessão guardada no banco. |
| Reserva dupla | Regra de não-sobreposição no gatilho do banco, com a linha do chalé travada: nem dois pedidos no mesmo instante passam juntos. |
| Enumeração de usuários | Mensagem de erro idêntica para usuário inexistente e senha errada. |
| Enumeração pelo cadastro | Resposta idêntica para e-mail novo e já cadastrado. Quem é dono do endereço recebe um aviso; quem submeteu não aprende nada. |
| Envio automatizado | Campo-armadilha no cadastro, com descarte **em silêncio**. |
| Sondagem de token | Resposta idêntica para token válido, vencido, inexistente e de outro tipo. Uso único. |
| Vazamento de e-mail pelo banco | O login do hóspede é o HMAC do endereço, nunca o endereço. A coluna é única e em texto claro. |
| Retenção indevida | Cadastro não confirmado no prazo é apagado, liberando endereço e documento. |
| Rastreamento por e-mail | Mensagens em texto puro: sem HTML, sem imagem remota, sem pixel de rastreio. |
| Interceptação | HTTPS com TLS 1.2 ou superior, em qualquer ambiente, só com cifras autenticadas e troca de chave efêmera; HSTS. |
| Adulteração do histórico | Log de auditoria e pagamentos só aceitam acréscimo: a conta da aplicação não tem UPDATE nem DELETE neles, e gatilhos barram até quem tem. Mensagem enviada não se reescreve. |
| Dado pessoal no log | O log não guarda nome, documento, e-mail, senha nem token; de um destinatário de e-mail, só o domínio. |
| Pico de acessos | Sem conexão livre com o banco, o pedido espera; se não houver, a pessoa vê "sistema ocupado", e não um erro. |
| Página embutida ou abusada por outro site | Cabeçalhos `X-Frame-Options`, `Permissions-Policy` e isolamento de origem (COOP, CORP, COEP); o servidor não anuncia a própria versão. |

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
| 5 — Autoatendimento do hóspede | 10 a 22/11 | concluída |
| 6 — Relatórios e análise estática | 21 e 22/11 | concluída; a revisão por pares está com o grupo |
| 7 — Análise dinâmica | 22 e 23/11 | concluída |
| 8 — Validação e defesa | 23 a 25/11 | **em andamento** |

### Etapa 5, bloco a bloco

| Bloco | O que é | Situação |
|---|---|---|
| 1 | Envio de e-mail, por SMTP ou arquivo | concluído |
| 2 | Tokens de confirmação, guardando só o hash | concluído |
| 3 | Autocadastro público com confirmação | concluído |
| 4 | Caminho único e descarte do não confirmado | concluído |
| 5 | Consulta pública de disponibilidade | concluído |
| 6 | Portal do hóspede — perfil, troca de senha, reservas separadas | concluído |
| 7 | Reserva pelo hóspede, com o prazo do Pix | concluído |
| 8 | Controle de pagamentos: cards da equipe, linha do tempo do hóspede, controle do ano | concluído |
| 9 | Cancelamento pelo hóspede, com a regra dos 7 dias, e a devolução pela equipe | concluído |
| 10 | Página de cada chalé, com galeria | concluído |
| 12 | Mensagem em HTML | opcional |
| 13 | Ajustes de interface | concluído |
| 14 | Recuperação de senha pelo link de e-mail | concluído |
| 15 | Quem vai se hospedar: número de pessoas e acompanhantes, com o titular no topo | concluído |
| 15b | Edição da lista de quem vai, só pelo administrador | concluído |
| 16 | Painel da equipe em cards, com os selos de pagamento e de estadia | concluído |
| 16b | Reserva registrada pela recepção no mesmo controle do Pix, com prazo de 24 horas | concluído |
| 17 | Conversa da reserva entre hóspede logado e equipe, cifrada; sai o formulário público de contato | concluído |
| 17b | Aviso de "nova resposta da pousada" no Painel e no card do hóspede; campo da conversa com tamanho fixo | concluído |
| 17c | Botões em três tipos (principal, secundário, destrutivo) com efeito de mouse, clique e teclado; terceiro botão nos cards; links soltos viram botões | concluído |
| 17d | A reserva se conclui sozinha quando a saída chega com o pagamento quitado; sai o "Marcar como concluída" | concluído |
| 17e | A equipe cancela dizendo o motivo: a pedido do hóspede (regra dos 7 dias) ou pela pousada (devolve tudo); o hóspede vê quem cancelou | concluído |
| 18a | Relatórios de ocupação por chalé, reservas por situação e volume de mensagens, por período (RF06 e RF12) | concluído |
| 18b | Página "Como usar" para a equipe, por tarefa, com os prazos tirados do regulamento | concluído |
| 18c | Backup diário automático com usuário só de leitura, aviso no Painel e restauração testada (RNF06) | concluído |
| 18e | Logo na barra e no banner, fotos da pousada passando na página inicial (só CSS, sem metadado), barra só com o perfil, "Nada foi pago" no cancelamento | concluído |
| 18f | Banner branco com o logo maior; o detalhe da reserva diz por que, quando e quem cancelou | concluído |
| 18d | Guia de instalação completo e roteiro da demonstração, conferidos pela suíte | concluído |
| 18g | A paleta do logo no sistema inteiro (variáveis CSS, contraste calculado por teste), banner só com o logo e pausa das fotos só pelo botão | concluído |

Falta só o **12** (mensagem em HTML), opcional.

### A fase final, de 22 a 25/11

| Data | O que foi feito | Situação |
|---|---|---|
| 22/11 | **Análise estática** — Bandit, Semgrep, pip-audit e gitleaks sobre todo o código e o histórico: 6 achados, todos corrigidos com teste. O principal: a regra do Bandit contra senha escrita no código não reconhece nomes em português, e o projeto ganhou uma verificação própria | concluída |
| 22 e 23/11 | **Análise dinâmica** — testssl, sqlmap, OWASP ZAP e 45 casos de abuso escritos pela equipe, contra o sistema em funcionamento: 7 achados, todos corrigidos com teste e sabotagem. Os dois mais sérios: um cookie de sessão copiado continuava valendo depois de "Sair", e duas reservas do mesmo chalé feitas no mesmo instante passavam as duas | concluída |
| 23/11 | Barra e botões em marrom de madeira, logo ocupando a primeira tela e a pausa das fotos corrigida; guia completo do envio de e-mail; o README passa a apontar para a documentação completa | concluído |
| 23/11 | Banco local limpo e povoado com contas novas, para gerar o conteúdo da demonstração com o sistema completo | concluído |
| até 25/11 | Revisão por pares, ensaio e gravação da apresentação | em andamento |

> **Alteração de escopo declarada no Bloco 17 (21/11).** O RF11 (receber
> pedidos de contato e registrá-los para atendimento) passa a ser atendido
> pela conversa da reserva, com usuário verificado por e-mail, e pelos
> contatos da pousada em texto — o formulário anônimo saiu, por aceitar
> dado pessoal sem verificação nenhuma. O RF12 (relatório da origem e do
> volume dos contatos) vira, na Etapa 6, o volume de mensagens por período.

> **O Bloco 11 — contas da equipe — saiu do escopo em 13/11.** Contas de
> administrador e recepcionista são provisionadas uma vez, na instalação,
> por `banco/04_dados_ficticios.sql` — são três pessoas numa pousada
> de cinco chalés. O RF07 e o RF08 continuam atendidos: eles exigem
> autenticação e os três perfis operando, e os três operam.
>
> Conta de **hóspede**, essa sim, é criada pela própria pessoa, com
> confirmação por e-mail — que era o buraco que deu origem a esta etapa.

**Entrega 2:** 11/11/2026 · **Entrega final:** 25/11/2026

> **As regras de pagamento**, decididas em 13/11 e publicadas no
> Regulamento da pousada, na página inicial:
>
> | Antecedência da entrada | Pagamento por Pix | Cancelamento |
> |---|---|---|
> | 7 dias ou mais | sinal de 50% em **90 minutos**; o restante até **23h59 de 2 dias antes da entrada** | com 7 dias ou mais, devolve tudo |
> | menos de 7 dias | **integral** em 90 minutos | sem devolução |
>
> Sem o "Fiz o Pix" do hóspede em 90 minutos, o pedido é cancelado
> automaticamente e o chalé volta a ficar disponível. Quem confirma o
> pagamento é a equipe, depois de conferir o extrato.
>
> **Sobre o controle de pagamento.** Os Blocos 7 a 9 registram o sinal e
> contam os prazos, a pedido do professor. O controle aparece
> como **uma seção do painel do administrador**, e não como módulo
> financeiro: o sistema cuida do aluguel dos chalés, e o dinheiro é o
> estado de uma reserva. Faturamento, contas a receber e fechamento não
> estão no escopo e não vão estar.
>
> **O sistema não transaciona.** O Pix acontece inteiramente fora dele, entre o
> aplicativo do banco do hóspede e o da pousada; o que o sistema faz é
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
