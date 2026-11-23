# Relatório 2 — Análise dinâmica

**Projeto:** VillaJu — Sistema de Reservas e Divulgação
**Disciplina:** Projeto Integrador 4 — Bacharelado em Cibersegurança — PUC-Campinas
**Equipe:** Dimas Marcondes · Eduardo Malta · Gabriel Fuzinatto · Guilherme Leal · Murilo Dias Bartkevicius
**Período da análise:** 22 e 23/11/2026 · **Versão inicial:** commit `68d7ad2` · **Versão final:** commit `6fc4842` (`main`)

---

## 1. Introdução e objetivo

O Relatório 1 examinou o código **sem executá-lo**. Este relatório faz o
contrário: ataca o sistema **em funcionamento**, pela rede, como faria
alguém de fora — com o servidor real, o TLS real, o banco real e as
proteções ligadas como em uso.

A análise dinâmica encontra o que a estática não vê: configuração do
servidor, comportamento sob carga, a interação entre camadas e as regras
de negócio quando alguém tenta contorná-las. Foram usadas três ferramentas
consagradas e um roteiro de abuso escrito pela equipe:

| Ferramenta | O que ataca |
|---|---|
| **testssl.sh** | a configuração do TLS: protocolos, cifras, vulnerabilidades conhecidas |
| **sqlmap** | injeção de SQL em cada ponto de entrada |
| **OWASP ZAP** | varredura geral da aplicação web, passiva e ativa |
| **Roteiro de abuso** (próprio) | regras de negócio e controle de acesso: o que as ferramentas genéricas não conhecem |

**Regra do relatório:** nenhum alerta foi aceito ou descartado pela
palavra da ferramenta. Todo achado foi reproduzido, e todo falso positivo
foi **provado** falso no sistema real (seção 6).

## 2. Escopo e autorização

| Item | Valor |
|---|---|
| Alvo | o VillaJu rodando **na máquina do Dimas** (`127.0.0.1`); dos contêineres, `host.docker.internal` |
| Autorização | escopo validado pelo Dimas em 22/11: só a máquina local, nada fora dela |
| Instância de varredura | porta **5001**, `DEBUG` desligado, TLS ligado, limites de tentativa elevados para as ferramentas não esbarrarem neles |
| Instância de limites | porta **5002**, limites normais, para testar o próprio limite de tentativas (caso F1) |
| E-mail | modo `arquivo`, forçado por variável de ambiente numa pasta de evidências: nenhuma mensagem real podia sair |
| Banco | o banco local do Dimas, com cópia de segurança antes de cada mudança |
| Fora do escopo | a varredura **ativa autenticada** do ZAP: ela enviaria "excluir hóspede" e "cancelar reserva" no banco com as contas de teste. A área autenticada foi coberta pelo ZAP passivo, pelo sqlmap com sessão e pelo roteiro de abuso |

## 3. Metodologia e ferramentas

### 3.1. Ferramentas e versões

| Ferramenta | Versão | Onde rodou |
|---|---|---|
| testssl.sh | 3.2.5 | contêiner Docker (`drwetter/testssl.sh:3.2`) |
| sqlmap | 1.10.11 | Python, ambiente separado `venv-ferramentas` |
| OWASP ZAP | 2.17.0 | contêiner Docker (`zaproxy/zap-stable`) |
| Roteiro de abuso | — | Python (`requests`), `instance/evidencias/relatorio2/abuso.py` |

Configuração do sqlmap: `--level 3 --risk 1 --dbms=mysql`, uma linha de
execução, sessão de administrador para os pontos internos.

### 3.2. Toda correção com teste e sabotagem

Como no Relatório 1, cada correção ganhou teste automatizado, e cada
teste foi validado por **sabotagem**: a correção desfeita de propósito,
para confirmar que o teste acusa. Foram **33 sabotagens, todas
detectadas** — depois de três correções no próprio teste, contadas na
seção 5.3.

### 3.3. Problemas de ambiente encontrados no caminho

Quatro problemas fizeram ferramentas medirem a coisa errada. Ficam
registrados porque cada um teria produzido um resultado falso — de
"seguro" ou de "vulnerável":

| Problema | Efeito | Solução |
|---|---|---|
| No Windows, `localhost` resolve primeiro para o IPv6 (`::1`), e o servidor só escuta no IPv4 | cada requisição do sqlmap levava **12,5 s**; em 2 horas, nem o primeiro alvo terminou | alvo sempre `127.0.0.1` (0,02 s) |
| A sessão de administrador do sqlmap foi criada às 13h09 e o VillaJu encerra toda sessão em **8 horas** | às 21h09 a sessão venceu; todo teste do painel caiu na tela de login e não valeu | o painel foi refeito com sessão nova; e a expiração ficou **provada na prática** |
| O login responde **401** à senha errada e o "esqueci a senha" responde **400** ao e-mail inválido | o sqlmap tratava as respostas corretas como bloqueio e abortava | `--ignore-code`, com o motivo registrado |
| O modo automático da varredura passiva do ZAP (`zap-baseline.py`) **enviou o formulário "Sair"**, apesar de configurado para não enviar formulários | a varredura do administrador perdeu a sessão no meio | plano próprio do ZAP, sem envio de formulário e com `/logout` excluído |

## 4. Resultados por ferramenta

### 4.1. testssl.sh — configuração do TLS

| Item | Antes | Depois |
|---|---|---|
| Protocolos | só TLS 1.2 e 1.3 | igual |
| Cifras CBC obsoletas no TLS 1.2 | **oferecidas** | não oferecidas |
| LUCKY13 (CVE-2013-0169) | **potencialmente vulnerável** | não vulnerável |
| Cabeçalho do servidor | **`Werkzeug/3.1.9 Python/3.12.3`** | `VillaJu` |
| Heartbleed, CCS, ROBOT, CRIME, BREACH, POODLE, SWEET32, FREAK, DROWN, LOGJAM, BEAST, RC4 | não vulnerável | igual |
| Nota geral | T | T (por causa do certificado autoassinado — seção 7) |

Os dois achados (R2-01 e R2-02) foram corrigidos no commit `fe68a50`.

### 4.2. sqlmap — injeção de SQL

**Resultado: 10 de 10 pontos de entrada sem injeção.**

| Ponto de entrada | Parâmetros | Sessão | Resultado |
|---|---|---|---|
| `/disponibilidade` (pública) | entrada, saida, chale | — | não injetável |
| `/chale/<nome>?foto=` | foto | — | não injetável |
| `/login` | login, senha | — | não injetável |
| `/esqueci-senha` | email | — | não injetável |
| `/hospedes/?busca=` | busca | administrador | não injetável |
| `/relatorios` | de, ate | administrador | não injetável |
| `/reservas/?situacao=` | situacao | administrador | não injetável (só redireciona ao painel) |
| `/reservas/disponibilidade` | chale_id, entrada, saida, pessoas | administrador | não injetável |
| `/pagamentos/controle` | ano, mes | administrador | não injetável |
| `/painel` | situacao, pagina, limite | administrador | não injetável |

O sqlmap emitiu **8 alarmes** de "parece injetável" em testes por tempo
de resposta (`SLEEP`) e um "aceita UNION com 32 colunas" em `saida`, e
**descartou todos** ao conferir. A explicação está no próprio log da
ferramenta: *"considerable lagging has been detected"*. O servidor de
desenvolvimento atende um pedido por vez; sob a carga da varredura, o
`/login` chegou a 13,9 s (0,01 s sem carga) — e um teste que mede atraso
confunde fila com injeção. O alarme mais forte, em `saida`, foi também
**provado falso à mão** (seção 6).

### 4.3. Roteiro de abuso — regras de negócio e controle de acesso

45 casos, escritos pela equipe para o que as ferramentas genéricas não
conhecem: perfis, donos de reserva, regras de conflito, sessão.

| Categoria | Casos | Resultado |
|---|---|---|
| Controle de acesso (A1–A11): recepção e hóspede abrindo área alheia pelo endereço | 11 | todos bloqueados (403 ou login) |
| Acesso a dado de outra pessoa (I1–I3) | 3 | todos bloqueados |
| Reserva conflitante e campos forjados (R0–R3) | 4 | todos bloqueados |
| XSS armazenado e refletido (X1–X5) | 5 | todos escapados |
| Injeção de SQL manual (S1–S3) | 3 | sem efeito |
| CSRF e Referer (C1–C6) | 6 | todos recusados |
| Sessão e contas (K1–K4) | 4 | **K1 falhou** — achado; os outros três passaram |
| Cabeçalhos de segurança (H1–H5) | 5 | todos presentes |
| Entrada malformada (E1–E3) | 3 | recusada sem rastro interno |
| Limite de tentativas (F1) | 1 | 6ª tentativa seguida recebe **429** |

**Três casos não testavam o que diziam testar**, e foram refeitos
(`abuso_conferencia.py`):

- **I3** ("hóspede cancela a reserva de outra pessoa") usava uma reserva
  **já cancelada** — o teste não tinha como falhar. Refeito com uma
  reserva viva de outro hóspede: continua pendente. **Bloqueado.**
- **R1** (reserva no mesmo chalé e período) e **R3** (formulário com
  `valor_total=1,00` e `situacao=confirmada` acrescentados) eram barrados
  por **outra regra** — "um pedido pendente por hóspede". Refeitos com
  hóspedes sem pedido: R1 recebeu **409** "O chalé Dunas já está
  reservado de 01/12/2027 a 05/12/2027"; R3 gravou **R$ 1.020,00,
  pendente** — os campos forjados foram ignorados. **Bloqueados.**

**Resultado final: 44 de 45 bloqueados; 1 achado real (K1), corrigido.**

### 4.4. OWASP ZAP — varredura da aplicação

| Varredura | Antes das correções | Depois |
|---|---|---|
| Pública, passiva | 0 alto · 0 médio · **4 baixos** · 4 informativos | 0 · 0 · **0** · 4 informativos |
| Pública, ativa | **1 alto** (falso positivo) · 0 médio · 4 baixos | — (o alto foi provado falso; os baixos são os mesmos da passiva) |
| Administrador, passiva | **14 respostas 500**, 4 baixos | **0 respostas 500**; 1 baixo e 1 informativo, ambos falsos positivos |

- O alerta **alto**, "SQL Injection" no `login`, foi provado falso
  (seção 6).
- As **14 respostas 500** não eram alerta do ZAP: apareceram no log do
  servidor durante a varredura. Era o achado **R2-03**.
- Os **4 baixos** eram cabeçalhos HTTP opcionais ausentes: o achado
  **R2-04**.
- **Nenhum dado mudou.** Na tentativa que perdeu a sessão, o rastreador
  também tentou mudar a situação dos 5 chalés e registrar pagamento em 4
  reservas; todos os envios foram recusados com 400, e a conferência no
  banco confirmou que nada mudou.

## 5. Achados consolidados e correções

| ID | Achado | Fonte | Severidade | Correção | Commit | Testes / sabotagens |
|---|---|---|---|---|---|---|
| R2-01 | Cifras CBC no TLS 1.2 (LUCKY13 em potencial) | testssl | Baixa | TLS 1.2 restrito a `ECDHE+AESGCM:ECDHE+CHACHA20` | `fe68a50` | `test_tls.py` · 5 (com o R2-02) |
| R2-02 | Servidor anunciava a versão exata (`Werkzeug/3.1.9 Python/3.12.3`) | testssl | Baixa | cabeçalho `Server: VillaJu`; tratador sem versão | `fe68a50` | `test_tls.py`, `test_aplicacao.py` |
| **K1** | **Cookie de sessão copiado continuava entrando depois de "Sair"** (até vencer nas 8 h) | roteiro de abuso | **Média** | coluna `usuario.versao_sessao`, conferida a cada requisição; sair, trocar e redefinir a senha somam 1 | `145ea2f` | `test_sessao_revogada.py` (13) · 11 |
| R2-03 | Com mais de 5 pedidos simultâneos, o pool de conexões esgotava e a pessoa via "erro inesperado" (500) | ZAP (log do servidor) | Média (disponibilidade) | o pedido espera até 2 s por uma conexão; sem ela, 503 "sistema ocupado"; pool de 10 | `459294c` | `test_pool_ocupado.py` (7) · 6 |
| R2-04 | Faltavam Permissions-Policy, COOP, CORP e COEP | ZAP | Baixa | os quatro cabeçalhos, no mesmo ponto que já manda CSP e HSTS | `dea2964` | `test_aplicacao.py` · 5 |
| R2-05 | O log do servidor registrava o **endereço de e-mail** do destinatário (e o caminho do arquivo, que o leva no nome) | revisão, ao escrever o guia de e-mail | Baixa (privacidade) | o log guarda só o domínio (`@gmail.com`), que basta ao diagnóstico | `6fc4842` | `test_correio.py`, `test_cadastro.py`, `test_recuperacao_de_senha.py` · 6 |

### 5.1. K1: a limitação que o próprio código já anunciava

A sessão do Flask não fica guardada no servidor: ela **é** o cookie,
assinado com a chave secreta. "Sair" apaga o cookie do navegador — mas
uma **cópia** dele continua bem assinada e dentro do prazo, e o servidor
a aceitava. Quem copiasse o cookie (por acesso ao computador ou ao
navegador da pessoa; ele é `HttpOnly` e `Secure`, então não sai por
script nem por rede aberta) continuaria dentro por até 8 horas depois de
a vítima sair — ou trocar a senha.

O projeto já sabia disso pela metade: um teste antigo **demonstrava** que
trocar a senha não derrubava as outras sessões, e dizia que, no dia em
que a invalidação fosse construída, ele falharia — "e falhar aqui é a
notícia de que o problema foi resolvido". A análise dinâmica mostrou o
mesmo defeito no "Sair", e a correção resolveu os dois.

**Como funciona:** cada conta tem um número de versão. O login grava esse
número no cookie; toda requisição compara o número do cookie com o do
banco — sem custo extra, porque o VillaJu já relê a conta em toda
requisição. Sair, trocar ou redefinir a senha somam 1, e todo cookie
anterior daquela conta deixa de valer. Quem troca a própria senha
continua dentro, porque o cookie dele recebe a versão nova.

**Efeito colateral aceito:** sair num aparelho encerra a conta em todos.

**Prova:** o roteiro repetido no banco local, com a conta real de
administrador — a cópia do cookie abria `/auditoria` (200) antes de sair
e foi para o login (302) depois. E durante a varredura do ZAP, quando o
rastreador enviou o "Sair" por conta própria, o mesmo cookie passou a ser
recusado em todas as páginas seguintes: a correção funcionando contra uma
ferramenta que não sabia dela.

**Banco local:** a coluna nova foi aplicada por `ALTER TABLE`, com
`mysqldump` antes e conferência coluna a coluna contra o banco de testes
(zero diferenças).

### 5.2. R2-03: um achado que nenhuma ferramenta apontou

O ZAP não emitiu alerta de pool esgotado. O achado apareceu porque o
**log do servidor** era lido depois de cada varredura: 14 rastros de
`PoolError: pool exhausted`. O pool do conector MySQL não espera — com as
5 conexões emprestadas, o sexto pedido falhava na hora. As páginas
públicas também usam o banco, então qualquer visitante abrindo várias
abas rápidas derrubava, por um instante, os pedidos de outras pessoas.
Nada vazava — a página de erro é genérica —, mas é disponibilidade, um
dos três pilares da segurança.

A confirmação: a mesma varredura do administrador, repetida com o código
corrigido, teve **142 páginas com 200 e nenhuma com 500**.

### 5.3. Os testes que passavam pelo motivo errado

Duas vezes nesta fase, um teste novo passou **sem testar o que dizia**,
e a sabotagem acusou:

- **R2-03:** o primeiro teste usava a página do chalé, que já trata a
  falha do banco por conta própria com um 503. Sem a correção, o teste
  passava igual. Duas sabotagens não foram detectadas; o alvo virou o
  login, que não trata o erro localmente, e as seis passaram a ser.
- **R2-04:** a expressão regular que procura recursos externos nos
  modelos foi gravada com um caractere de controle no lugar de `\b` — o
  teste procurava um texto que nunca existe e passaria sempre.
- **R2-05:** os testes conferiam o log do envio para arquivo, mas não o
  do envio por SMTP. Devolver o endereço à linha "mensagem enviada para
  ..." não quebrava nada; ganhou teste próprio.

### 5.4. R2-05: uma decisão antiga revista

O endereço no log **não era descuido**: um teste de um bloco anterior
exigia que ele aparecesse — "é o que permite diagnosticar". A decisão
foi revista ao escrever o guia de e-mail: e-mail é dado pessoal pela
LGPD, e o projeto se compromete a não guardar dado pessoal em log. O
domínio preserva o diagnóstico (para qual provedor o envio falhou) sem
identificar a pessoa. O teste antigo foi invertido, com a história
registrada nele.

## 6. Falsos positivos — e como foram provados

| Alerta | Ferramenta | Por que parecia | Prova de que é falso |
|---|---|---|---|
| "SQL Injection" no campo `login` (risco **alto**, confiança média) | ZAP | `ZAP AND 1=1 -- ` e `ZAP AND 1=2 -- ` produziram páginas diferentes | `prova_login.py`: a aplicação real, com toda consulta ao MySQL gravada. A carga apareceu **0 vezes no texto do SQL** e só como valor ligado ao marcador `%s`; as duas páginas diferem **só no token CSRF**, que é novo a cada página |
| "UNION com 32 colunas" e `SLEEP` em `saida` | sqlmap | atraso e página diferente | `prova_saida.py`: 4 cargas (SLEEP, RLIKE, UNION, booleana) → todas **400 "data de saída inválida"**, **0 consultas com a carga**; com data válida, a data aparece nas consultas gravadas (prova de que a gravação grava) |
| Mais 8 alarmes por tempo de resposta | sqlmap | atraso | descartados pelo próprio sqlmap; o log registra o atraso do servidor sob carga |
| "Timestamp Disclosure" em `/reservas/10` | ZAP | número de 10 dígitos lido como data Unix (2031) | é o **número de documento de um acompanhante**, dado fictício, mostrado à equipe na lista de quem vai |
| "Potential XSS" no `ano` de `/pagamentos/controle` | ZAP | o valor volta num atributo `value` | com `ano=2026"><script>...`, a carga **não volta**: o valor é convertido em número, e texto inválido vira o ano corrente |

## 7. Achados aceitos e por quê

| Achado | Justificativa |
|---|---|
| Certificado autoassinado (nota T no testssl), sem CRL/OCSP, sem registro CAA | ambiente de homologação local. Em produção, um certificado de autoridade reconhecida (Let's Encrypt, por exemplo) resolve os três |
| Nome do certificado diferente do acessado (`host.docker.internal`) | efeito do acesso por contêiner; o certificado vale para `localhost`, `villaju.local` e `127.0.0.1` |
| Informativos do ZAP sobre cache e gerenciamento de sessão | não são falhas: o ZAP apenas identifica o cookie de sessão e páginas armazenáveis |
| Sair num aparelho encerra todos (efeito do K1) | aceito pelo Dimas: é o comportamento mais seguro para uma pousada com poucas contas |
| Servidor de desenvolvimento do Flask | serve à homologação; produção na seção 9 |

## 8. Lições de método

1. **Nenhum alerta se aceita nem se descarta pela palavra da
   ferramenta.** O alerta mais grave da análise (injeção de SQL, risco
   alto) era falso, e o achado mais relevante de disponibilidade (R2-03)
   não gerou alerta nenhum.
2. **Ler o log do servidor depois de cada varredura.** Foi assim que o
   R2-03 apareceu.
3. **Conferir que a ferramenta mediu o que devia** antes de confiar no
   resultado: sessão válida até o fim, alvo respondendo rápido, códigos
   de resposta esperados. Quatro resultados quase saíram errados por isso
   (seção 3.3).
4. **Um caso de teste que não pode falhar não testa nada.** Três casos do
   roteiro de abuso e dois testes automatizados passavam pelo motivo
   errado; a conferência e a sabotagem os pegaram.
5. **Ferramenta automática não conhece a regra de negócio.** O K1 — o
   achado mais sério — veio do roteiro escrito pela equipe, não das
   ferramentas.
6. **Toda varredura ativa em banco com dados reais exige fronteira
   explícita.** A ativa autenticada ficou de fora de propósito, e a
   passiva ainda assim tentou enviar formulários.

## 9. Recomendações para produção

- **Servidor de aplicação no lugar do de desenvolvimento:** Waitress (ou
  Gunicorn) atrás de um proxy reverso, com o número de linhas de execução
  **igual ou menor que o pool de conexões** — o que impede o esgotamento
  do R2-03 na origem.
- **Certificado de autoridade reconhecida**, com renovação automática.
- **Repetir esta análise a cada versão**: testssl e ZAP passivo cabem numa
  rotina automática; o roteiro de abuso já é um programa.
- **Rever os limites de tentativa** (hoje 5 por minuto no login) com o
  uso real da pousada.

## 10. Conclusão

A análise dinâmica atacou o VillaJu em funcionamento com três
ferramentas e 45 casos de abuso escritos pela equipe. Encontrou **seis
achados reais** — dois na configuração do TLS, um na sessão (K1), um de
disponibilidade (R2-03), um de cabeçalhos (R2-04) e um de privacidade no
log (R2-05) —, **todos corrigidos**, cada um com teste automatizado e
sabotagem (33 sabotagens, todas detectadas). Não encontrou injeção de SQL em nenhum dos 10 pontos
de entrada, nem XSS, CSRF ou acesso indevido entre perfis.

O resultado mais importante talvez seja de método: as ferramentas
erraram nas duas direções — o alerta mais grave era falso, e um problema
real passou sem alerta —, e foi a verificação de cada resultado no
sistema real que separou uma coisa da outra.

---

## Anexo — Evidências

Ficam em `instance/evidencias/relatorio2/`, fora do Git (contêm dados da
massa de testes e cookies de sessão de homologação):

| Arquivo | Conteúdo |
|---|---|
| `testssl.txt` / `.json`, `testssl_depois.txt` / `.json` | TLS antes e depois das correções |
| `sqlmap/`, `sqlmap_resumo.txt`, `sqlmap_resumo_23-11.txt` | um log por ponto de entrada |
| `prova_saida.py` / `_resultado.json` | prova do falso positivo em `saida` |
| `prova_login.py` / `_resultado.json` | prova do falso positivo no `login` |
| `abuso.py`, `abuso_resultado.md` / `.json` | os 45 casos |
| `abuso_conferencia.py` | I3, R1 e R3 refeitos |
| `zap/` | relatórios HTML e JSON de cada varredura e o plano `plano_admin.yaml` |
| `servidor_varredura.log` | o log do servidor durante as varreduras (onde apareceu o R2-03) |
