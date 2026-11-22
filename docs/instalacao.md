# VillaJu — Guia de instalação

Do computador vazio até o sistema rodando, com backup diário e
restauração conferida. Escrito para Windows 10/11, que é onde o sistema
roda; os comandos são para o **PowerShell**, salvo onde está indicado.

Tempo estimado: 40 minutos na primeira vez.

| Seção | O quê |
|---|---|
| 1 | Pré-requisitos |
| 2 | Código e ambiente Python |
| 3 | Segredos: o arquivo `.env` |
| 4 | Certificado TLS |
| 5 | Banco de dados |
| 6 | Subir e conferir |
| 7 | Testes |
| 8 | Backup diário |
| 9 | Restauração |
| 10 | Como desfazer cada passo |
| 11 | Antes do uso real |
| 12 | Problemas comuns |

> **Regra que vale o guia inteiro.** Nunca rode um arquivo `.sql` com
> `Get-Content arquivo.sql | mysql`. O PowerShell 5.1 reescreve os bytes
> antes de entregá-los ao `mysql`, e os acentos viram `?` — em dados e
> até dentro dos gatilhos, sem nenhuma mensagem de erro. Use sempre
> `mysql ... -e "SOURCE arquivo.sql"` (como abaixo) ou, no `cmd`,
> `mysql ... < arquivo.sql`.

---

## 1. Pré-requisitos

| Programa | Versão | Observação |
|---|---|---|
| Python | 3.11 ou superior | marcar "Add python.exe to PATH" no instalador |
| MySQL Community Server | 8.0 | pelo MySQL Installer. O Workbench sozinho **não** basta: é só o cliente gráfico |
| Git | qualquer recente | o Git para Windows já traz o OpenSSL |

O cliente `mysql` e o `mysqldump` ficam em
`C:\Program Files\MySQL\MySQL Server 8.0\bin`. Acrescente essa pasta ao
`PATH` do Windows, ou informe os caminhos em `VILLAJU_MYSQL` e
`VILLAJU_MYSQLDUMP` (seção 3.3).

Confira:

```powershell
python --version
mysql --version
git --version
```

---

## 2. Código e ambiente Python

```powershell
git clone https://github.com/dimasmmdasilva/projetoVillaJu.git
cd projetoVillaJu

python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

Todos os comandos seguintes partem da pasta `projetoVillaJu`, com o
ambiente ativado (`(venv)` no início da linha).

---

## 3. Segredos: o arquivo `.env`

### 3.1. Criar e gerar

```powershell
copy .env.example .env
python scripts\gerar_chaves.py
```

O script imprime três chaves e duas senhas de banco, geradas pelo
computador. Cole cada valor no `.env`, no lugar do texto
`GERE_COM_O_SCRIPT_NAO_USE_ESTE_VALOR`.

> **Perder `VILLAJU_CHAVE_CIFRAGEM` torna os dados pessoais já gravados
> irrecuperáveis.** Não existe recuperação. Guarde uma cópia do `.env`
> fora do computador, **separada** dos backups do banco (seção 8.4).

O `.env` nunca entra no Git, nunca é enviado por mensagem e nunca é
anexado a entrega. Cada instalação tem o seu.

### 3.2. As variáveis do `.env.example`

| Variável | Para quê | O que pôr |
|---|---|---|
| `VILLAJU_CHAVE_CIFRAGEM` | AES-256-GCM dos dados pessoais | gerada pelo script |
| `VILLAJU_CHAVE_INDICE` | HMAC que permite buscar campo cifrado | gerada pelo script |
| `VILLAJU_SECRET_KEY` | assinatura do cookie de sessão | gerada pelo script |
| `VILLAJU_DB_HOST` | servidor MySQL | `localhost` |
| `VILLAJU_DB_PORTA` | porta do MySQL | `3306` |
| `VILLAJU_DB_NOME` | banco da aplicação | `villaju` |
| `VILLAJU_DB_USUARIO` | usuário da aplicação, sem root | `villaju_app` |
| `VILLAJU_DB_SENHA` | senha dele | gerada pelo script |
| `VILLAJU_DB_NOME_TESTE` | banco da suíte de testes | `villaju_teste` |
| `VILLAJU_DB_USUARIO_TESTE` | usuário da suíte | `villaju_teste` |
| `VILLAJU_DB_SENHA_TESTE` | senha dele | gerada pelo script |
| `VILLAJU_TLS_CERT` | certificado | `certs/villaju.crt` (seção 4) |
| `VILLAJU_TLS_CHAVE` | chave do certificado | `certs/villaju.key` |
| `VILLAJU_DEBUG` | detalhe de erro na tela | `1` para desenvolver, **`0` em uso real** |
| `VILLAJU_HOST` | endereço de escuta | `127.0.0.1` |
| `VILLAJU_PORTA` | porta da aplicação | `5000` |
| `VILLAJU_EMAIL_MODO` | `arquivo` grava em pasta; `smtp` envia | `arquivo` para demonstrar sem rede |
| `VILLAJU_EMAIL_REMETENTE` | quem assina o e-mail | nome e endereço da pousada |
| `VILLAJU_EMAIL_PASTA` | onde o modo `arquivo` grava | `instance/emails` |
| `VILLAJU_EMAIL_BASE_URL` | início dos links dos e-mails | `https://localhost:5000` — o endereço que se usa no navegador |
| `VILLAJU_SMTP_HOST` | servidor de envio (modo `smtp`) | `smtp.gmail.com` |
| `VILLAJU_SMTP_PORTA` | porta de envio | `587` |
| `VILLAJU_SMTP_USUARIO` | conta de envio | o e-mail da pousada |
| `VILLAJU_SMTP_SENHA` | **senha de app** do Google, 16 letras sem espaços | nunca a senha da conta |
| `VILLAJU_CADASTRO_MINUTOS` | prazo para confirmar o cadastro pelo e-mail | `15` na primeira prova com SMTP; depois `5` |
| `VILLAJU_PIX_CHAVE` | chave Pix mostrada ao hóspede | **a chave real da pousada** — não há valor padrão, de propósito |

### 3.3. Variáveis opcionais

Não estão no `.env.example` porque o padrão do código serve. Só as
acrescente ao `.env` se precisar mudar.

| Variável | Padrão | Para quê |
|---|---|---|
| `VILLAJU_SESSAO_SEGUNDOS` | 28800 (8 h) | duração da sessão |
| `VILLAJU_CSRF_SEGUNDOS` | 3600 | validade do token dos formulários |
| `VILLAJU_MAX_CORPO_BYTES` | 2097152 (2 MB) | maior requisição aceita |
| `VILLAJU_DB_POOL` | 5 | conexões abertas com o banco |
| `VILLAJU_LIMITE_LOGIN` | `5 per minute` | tentativas de login |
| `VILLAJU_LIMITE_PUBLICO` | `3 per minute` | formulários públicos |
| `VILLAJU_LIMITE_PADRAO` | `200 per hour` | demais páginas |
| `VILLAJU_PASTA_BACKUP` | `instance/backups/automaticos` | onde ficam as cópias |
| `VILLAJU_MYSQL` | o `mysql` do `PATH` | caminho do cliente, para a restauração |
| `VILLAJU_MYSQLDUMP` | o `mysqldump` do `PATH` | caminho do `mysqldump`, para o backup |

---

## 4. Certificado TLS

O sistema só fala HTTPS, inclusive no computador local. Gere um
certificado autoassinado, válido por um ano. **No Git Bash:**

```bash
mkdir -p certs
MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout certs/villaju.key -out certs/villaju.crt \
  -days 365 -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

O `MSYS_NO_PATHCONV=1` impede o Git Bash de transformar `/CN=localhost`
num caminho do Windows. A pasta `certs/` nunca entra no Git.

---

## 5. Banco de dados

São cinco scripts, em ordem, em `banco/`. Dois deles pedem uma senha
colada antes de rodar.

| Script | O que faz | Roda em |
|---|---|---|
| `banco/00_bancos.sql` | cria os bancos `villaju` e `villaju_teste` e o usuário de testes | servidor (único que exige root) |
| `banco/01_schema.sql` | as tabelas | `villaju` |
| `banco/02_triggers.sql` | os gatilhos (sobreposição de reservas, auditoria, regras) | `villaju` |
| `banco/03_usuario_app.sql` | o usuário `villaju_app`, com privilégio mínimo | `villaju` e `villaju_teste` |
| `banco/04_dados_ficticios.sql` | chalés, tarifas e as contas de demonstração | `villaju` |

### 5.1. Colar as senhas

Há **duas senhas diferentes** em jogo, e confundi-las é o erro mais comum:

- no prompt `Enter password:` vai sempre a senha do **root** do MySQL,
  definida na instalação do servidor;
- **dentro** dos scripts vão as senhas dos usuários que eles criam.

Abra num editor e troque:

- em `banco/00_bancos.sql`, `COLE_AQUI_A_SENHA_DE_TESTE` pelo valor de
  `VILLAJU_DB_SENHA_TESTE` do seu `.env`;
- em `banco/03_usuario_app.sql`, `COLE_AQUI_A_SENHA_DO_APP` pelo valor de
  `VILLAJU_DB_SENHA`.

### 5.2. Rodar

```powershell
mysql -u root -p -e "SOURCE banco/00_bancos.sql"
mysql -u root -p villaju -e "SOURCE banco/01_schema.sql"
mysql -u root -p villaju -e "SOURCE banco/02_triggers.sql"
mysql -u root -p villaju -e "SOURCE banco/03_usuario_app.sql"
mysql -u root -p villaju -e "SOURCE banco/04_dados_ficticios.sql"
mysql -u root -p villaju_teste -e "SOURCE banco/03_usuario_app.sql"
```

O banco de testes recebe só o `03`: a suíte cria e apaga as tabelas
sozinha a cada execução.

### 5.3. Desfazer a edição das senhas

As senhas não podem ficar nos scripts — há teste que falha se ficarem:

```powershell
git checkout -- banco/00_bancos.sql banco/03_usuario_app.sql
git status
```

O `git status` não deve listar nenhum arquivo de `banco/`.

### 5.4. Cifrar os dados de demonstração

O `04` grava bytes aleatórios nas colunas cifradas, porque a chave de
cada instalação não pode estar num arquivo do Git. Este script troca
esses bytes por pessoas fictícias cifradas com a **sua** chave:

```powershell
python scripts\regerar_dados_pessoais.py
```

### 5.5. Mudanças de esquema depois de instalado

Nunca recrie o banco em uso: ele tem as reservas e as contas. Uma
mudança de esquema entra nos scripts `01` a `03` (para instalações novas)
e, no banco existente, por `ALTER`, sempre depois de um `mysqldump`.

---

## 6. Subir e conferir

```powershell
python run.py
```

Abra `https://localhost:5000`. O navegador avisa que o certificado não é
reconhecido. **O aviso é esperado:** quer dizer que ninguém atesta a
identidade do servidor, não que a conexão está aberta — o tráfego é
cifrado do mesmo jeito. Siga por "Avançado" e prossiga.

Use sempre `localhost`, e não `127.0.0.1`: a exceção do certificado e o
cookie de sessão não passam de um endereço para o outro.

Contas de demonstração, criadas pelo `04`:

| Login | Senha | Perfil |
|---|---|---|
| `admin` | `Villa@Admin2026` | administrador |
| `recepcao` | `Villa@Recep2026` | recepcionista |
| `joao` | `Villa@Hospede2026` | hóspede |

Essas senhas estão publicadas no repositório: servem **só** para
demonstração (seção 11).

---

## 7. Testes

```powershell
pytest
```

A suíte usa só o banco `villaju_teste`, que ela zera a cada caso; nunca
toca o `villaju`. Leva cerca de 25 minutos. Dois testes são pulados de
propósito quando dependem de um `.env` real.

Rode **uma bateria por vez**: todas usam o mesmo banco de testes, e duas
ao mesmo tempo atrapalham uma à outra.

---

## 8. Backup diário

### 8.1. Preparar (uma vez, pede a senha do root)

```powershell
python scripts\configurar_backup.py
```

Cria o usuário `villaju_backup`, que **só lê** o banco `villaju`, gera a
senha dele sozinho e a grava em `instance/backup.cnf`, legível só pelo
seu usuário do Windows. A senha nunca vai à linha de comando.

### 8.2. Fazer uma cópia agora

```powershell
python scripts\backup.py
```

A cópia vai para `instance/backups/automaticos/villaju_AAAA-MM-DD_HHMM.sql`
e só ganha esse nome depois de completa. Ficam as **14** mais novas.
Cada execução deixa uma linha em `backup.log`, na mesma pasta.

### 8.3. Agendar (uma linha só, no PowerShell, na pasta do projeto)

```powershell
$a = New-ScheduledTaskAction -Execute "$PWD\venv\Scripts\pythonw.exe" -Argument "scripts\backup.py" -WorkingDirectory "$PWD"; $t = New-ScheduledTaskTrigger -Daily -At 3am; $s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; Register-ScheduledTask -TaskName "VillaJu - backup diario" -Action $a -Trigger $t -Settings $s -Description "Backup diario do banco da VillaJu (scripts/backup.py)"
```

Roda todo dia às 3h; se o computador estiver desligado nessa hora, roda
assim que ligar. Para testar na hora:

```powershell
Start-ScheduledTask -TaskName "VillaJu - backup diario"
```

O Painel do administrador mostra "Último backup: ..." e fica em vermelho
se a última cópia tiver mais de **48** horas.

### 8.4. Tirar as cópias do computador

O backup no mesmo disco não sobrevive a um disco perdido. Uma vez por
semana, copie a pasta `instance/backups/automaticos` para um pen drive
ou nuvem. **A chave (`.env`) vai para outro lugar:** os dados pessoais
saem cifrados no backup, e quem tiver os dois junto lê tudo.

---

## 9. Restauração

Restaurar é sempre manual, porque sobrescreve dados.

**Para conferir uma cópia** (não toca o banco em uso):

```powershell
python scripts\restaurar.py instance\backups\automaticos\villaju_AAAA-MM-DD_HHMM.sql
```

Pede a senha do root, restaura num banco separado, `villaju_restaurado`,
e confere contra o `villaju`: tabelas e contagens, gatilhos, e se os
dados pessoais restaurados **se decifram** com a chave do `.env`. O fim
da saída diz **RESTAURAÇÃO CONFERIDA** ou o que divergiu.

**Depois de uma perda real** (por cima do banco em uso):

```powershell
python scripts\restaurar.py instance\backups\automaticos\villaju_AAAA-MM-DD_HHMM.sql --banco-em-uso
```

Exige digitar o nome do banco, `villaju`, para confirmar.

---

## 10. Como desfazer cada passo

| Para desfazer | Faça |
|---|---|
| O agendamento | `Unregister-ScheduledTask -TaskName "VillaJu - backup diario"` |
| O usuário de backup | `mysql -u root -p -e "DROP USER 'villaju_backup'@'localhost'"` e apague `instance/backup.cnf` |
| O banco da conferência | `mysql -u root -p -e "DROP DATABASE villaju_restaurado"` |
| A instalação inteira do banco | `mysql -u root -p -e "DROP DATABASE villaju; DROP DATABASE villaju_teste; DROP USER 'villaju_app'@'localhost'; DROP USER 'villaju_teste'@'localhost'"` — **apaga todos os dados**; faça um backup antes |
| O ambiente Python | apague a pasta `venv` |

Nenhum dos três primeiros toca os dados.

---

## 11. Antes do uso real

A instalação deste guia é de demonstração. Para a pousada usar de
verdade:

- [ ] `VILLAJU_DEBUG=0`.
- [ ] `VILLAJU_PIX_CHAVE` com a chave real da pousada, conferida com ela.
- [ ] `VILLAJU_EMAIL_MODO=smtp`, com a senha de app, e um cadastro de
      prova recebido de ponta a ponta.
- [ ] As contas de demonstração removidas ou com senhas trocadas: as
      senhas da seção 6 estão publicadas no repositório.
- [ ] Banco sem as reservas e pessoas fictícias da demonstração.
- [ ] Backup configurado, agendado e **uma restauração conferida**
      (seções 8 e 9).
- [ ] Cópia do `.env` guardada fora do computador, longe dos backups.

---

## 12. Problemas comuns

| Sintoma | Causa | Solução |
|---|---|---|
| Acentos viram `?` ou símbolos estranhos no banco | script rodado por `Get-Content \| mysql` | rode de novo com `SOURCE` (seção 5.2) |
| `Access denied for user 'root'` | senha do root errada no prompt | é a senha da instalação do MySQL, não a do `.env` |
| `Access denied for user 'villaju_app'` ao subir | senha do `03` diferente da do `.env` | repita a 5.1 e a 5.2 só para o `03` |
| `mysql` não é reconhecido | pasta `bin` do MySQL fora do `PATH` | seção 1, ou `VILLAJU_MYSQL`/`VILLAJU_MYSQLDUMP` |
| O navegador recusa a página | certificado ausente ou vencido | refaça a seção 4 |
| Entrou, mas a sessão "cai" ao clicar no link do e-mail | navegando em `127.0.0.1` e o link aponta para `localhost` | use `localhost` nos dois (seção 6) |
| O link de confirmação já expirou | e-mail demorou mais que `VILLAJU_CADASTRO_MINUTOS` | aumente para 15 e cadastre de novo |
| Hóspedes aparecem como "registro não legível" | faltou a seção 5.4, ou o `.env` tem outra chave | rode `regerar_dados_pessoais.py` |
| O Painel diz "Nenhum backup encontrado" | agendamento não registrado ou falhando | `Start-ScheduledTask` e leia `backup.log` |
| `Can't connect to MySQL server` | serviço do MySQL parado | Serviços do Windows → MySQL80 → Iniciar |
