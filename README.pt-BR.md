# Empório da Música — Agente de Atendimento ao Cliente

[![CI](https://github.com/alexmabud/emporio-musica-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/alexmabud/emporio-musica-agent/actions/workflows/ci.yml)

> 🇬🇧 **English version:** [README.md](README.md). Este arquivo é a tradução para o português do Brasil; em
> caso de divergência, vale o original em inglês.

Um agente de atendimento ao cliente por mensagem de texto para a *Empório da Música*, uma loja fictícia de
instrumentos musicais em Campo Grande/MS. Ele responde em português do Brasil, consulta dados reais da loja
(preços, estoque, promoções, status de pedidos) por meio de ferramentas tipadas e responde dúvidas de política
(trocas, horários, pagamento, frete) a partir do manual de políticas da loja.

> **Status: em andamento.** Este README apresenta as decisões de projeto logo de início. As seções marcadas
> com **TODO** são preenchidas conforme os commits correspondentes são feitos (veja o [Roteiro](#roteiro)).
> Nada abaixo é dado como funcionando até que o seu commit exista.

## Sumário

- [Início rápido](#início-rápido)
- [Como funciona](#como-funciona)
- [Interface Streamlit](#interface-streamlit)
- [Decisões técnicas](#decisões-técnicas)
- [Tratamento dos dados](#tratamento-dos-dados)
- [Premissas](#premissas)
- [Conversas de exemplo](#conversas-de-exemplo)
- [Avaliação](#avaliação)
- [Limitações conhecidas](#limitações-conhecidas)
- [Com mais tempo](#com-mais-tempo)
- [Uso de assistentes de código com IA](#uso-de-assistentes-de-código-com-ia)
- [Roteiro](#roteiro)

## Início rápido

**TODO — verificar cada comando quando o commit do esqueleto do projeto for feito.**

Requisitos: Python 3.11+ e uma chave gratuita da API do Google AI Studio (https://aistudio.google.com).

**Com `make` (Linux/macOS/WSL):**

```bash
git clone https://github.com/alexmabud/emporio-musica-agent.git
cd emporio-musica-agent
cp .env.example .env          # depois defina LLM_MODEL e GOOGLE_API_KEY (veja abaixo)
make install                  # venv + dependências (+ pytest e ruff)
make chat                     # CLI interativa
```

Outros comandos: `make ui` (chat em Streamlit em http://localhost:8501, mesmo agente e mesmo `.env` da CLI;
veja [Interface Streamlit](#interface-streamlit)), `make test` (testes determinísticos, sem chamadas ao LLM),
`make eval` (cenários roteirizados, chama o LLM), `make lint`.

**Sem `make`** (qualquer sistema operacional, mesmo resultado):

```bash
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\Activate.ps1       # Windows PowerShell   (cmd.exe: .venv\Scripts\activate.bat)
pip install -r requirements-dev.txt   # dependências de execução + projeto (editável) + pytest + ruff
                                      # só execução: pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
python -m emporio_agent.cli        # CLI interativa ('sair' para sair, '/novo' para uma nova conversa)
pytest                             # testes, sem chamadas ao LLM
ruff check . && ruff format --check .
```

O GitHub Actions (`.github/workflows/ci.yml`) roda o mesmo lint e os mesmos testes a cada push e pull request,
em Python 3.11 com cache do pip. Não precisa de chave de API e nunca chama um modelo; o `make eval` é rodado
à mão.

**Variáveis de ambiente** (documentadas no `.env.example`, que é mantido em sincronia):

| Variável | Finalidade |
|---|---|
| `LLM_MODEL` | Modelo no formato `init_chat_model` do LangChain, por exemplo `google_genai:gemini-3.5-flash` (o padrão testado, veja [Por que este modelo](#por-que-este-modelo)). |
| `GOOGLE_API_KEY` | Chave do Google AI Studio (plano gratuito), usada quando `LLM_MODEL` começa com `google_genai:`. |
| `GROQ_API_KEY` | Chave da Groq (plano gratuito), usada quando `LLM_MODEL` começa com `groq:`. |
| `LLM_FALLBACK_MODELS` | Modelos reserva opcionais, separados por vírgula, tentados em ordem quando o principal falha (veja [Cota do plano gratuito](#cota-do-plano-gratuito)). |
| `GROUNDING_MAX_RETRIES` | Quantas vezes uma resposta que cita um preço ou código de rastreio que nenhuma ferramenta, política ou mensagem do cliente sustenta é regenerada antes de enviar a mensagem segura (padrão `1`; cada nova tentativa é mais uma chamada ao modelo). |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | Rastreamento opcional. Deixe `LANGSMITH_TRACING=false` para desativar. |

Há dois provedores suportados e **só o `LLM_MODEL` escolhe um deles**; o código não muda. Mantenha as duas
chaves no `.env`: a que o `LLM_MODEL` não usa é ignorada. O `.env.example` tem exatamente uma linha
`LLM_MODEL` ativa e a outra comentada. Os dois pacotes cliente estão no `requirements.txt`.

| `LLM_MODEL` | Papel | Limites do plano gratuito |
|---|---|---|
| `google_genai:gemini-3.5-flash` | Modelo de referência: avaliação final e conversas de exemplo | cerca de 20 requisições por dia por modelo |
| `groq:openai/gpt-oss-120b` | Modelo de desenvolvimento: iteração do prompt, cenário por cenário | 1.000 requisições por dia, mas 8.000 tokens por minuto |

## Como funciona

```
mensagem do cliente
      │
      ▼
 agente ReAct (LangChain create_agent) ── prompt do sistema = persona + regras + manual de políticas completo
      │
      ├── precisa de dado ao vivo? ──► ferramentas tipadas ──► SQLite (limpo a partir de data/*.csv)
      │       search_products · get_product_details · get_order_status
      │       list_active_promotions · calculate_payment_options · escalate_to_human
      │
      ├── precisa de uma regra?    ──► responde pelo texto da política que já está no prompt
      │
      └── fora do escopo?          ──► recusa com gentileza e redireciona para o que a loja pode ajudar
      │
      ▼
 verificação de grounding (validation.py) ──► resposta
```

O modelo decide sozinho se uma pergunta precisa de dados, de política ou de nenhum dos dois. Perguntas só de
política (horário, trocas, endereço) nunca chamam uma ferramenta; perguntas sobre preço, estoque, promoção e
entrega sempre chamam. `calculate_payment_options` e `escalate_to_human` são as exceções legítimas: aplicam
regras da política, mas precisam de uma ferramenta para fazer a conta exata ou para passar o caso a uma pessoa.

## Interface Streamlit

O `make ui` abre uma página de chat sobre o mesmo agente do terminal. Além da conversa, ela tem, na barra
lateral:

- **botões de exemplo** (catálogo, status de pedido, troca, um serviço que o manual só menciona de passagem)
  que enviam a pergunta como se tivesse sido digitada;
- um painel recolhível **"Por dentro do agente"**, a única parte pensada para quem está testando, de modo que
  o resto da barra lateral seja só conteúdo para o cliente. Ele começa com o **modelo em uso**; depois, para a
  última resposta, as ferramentas chamadas com argumentos resumidos, o resultado do grounding (aprovado, nova
  tentativa ou mensagem segura) e os casos registrados para uma pessoa, além de uma linha por turno da
  conversa. Usa o que o agente já expõe. E-mails e telefones são mascarados (`an***@coldmail.com`,
  `(67) 9****-7821`) antes de chegarem ao painel.
- um indicador de carregamento enquanto o agente responde e um bloco **Nova conversa** (um botão verde, com
  um aviso de que o histórico atual será apagado) abaixo dos exemplos.
- **Mensagens de sistema são avisos, não fala do assistente.** Uma falha do modelo (cota diária, chave
  inválida) ou um problema de configuração aparece numa caixa de aviso fora dos balões do chat, para nunca ser
  confundida com algo que o assistente disse. O chat de terminal faz o mesmo: essas linhas não têm o prefixo
  "Atendente:".
- O `.streamlit/config.toml` deixa a barra de ferramentas mínima, o que esconde o botão Deploy e o menu de
  desenvolvimento, e define a cor primária verde. A caixa de digitação é estilizada para combinar (borda verde
  de 2px, cantos arredondados, um leve brilho no foco, fundo branco) com um pouco de CSS que mira a marcação
  interna do Streamlit, por isso **a versão do Streamlit é fixada** no `requirements.txt`.

Para testar outro modelo sem editar o `.env` (por exemplo, o provedor de desenvolvimento):

```bash
make ui ARGS="--model groq:openai/gpt-oss-120b"      # ou APP_LLM_MODEL=groq:openai/gpt-oss-120b make ui
```

Um modelo escolhido substitui o `LLM_MODEL` **e descarta os modelos reserva**, de modo que testar a interface
em um provedor nunca cai em outro e gasta a cota dele. A página também escapa o `$`, porque o Streamlit
leria `R$ 549,00 ... R$ 439,20` como uma fórmula matemática.

## Decisões técnicas

| Decisão | Escolha | Por quê |
|---|---|---|
| Padrão do agente | ReAct com chamada de funções | A tarefa é "saber quando consultar dados e quando usar a política". Um laço de chamada de ferramentas deixa o modelo tomar essa decisão e mantém as saídas estruturadas. |
| Orquestração | LangChain 1.x `create_agent` | O substituto atual do `create_react_agent` do LangGraph. Roda sobre o runtime do LangGraph, então mantém o laço de ferramentas, um checkpointer para memória e o rastreamento. Seus hooks de middleware são usados para aparar o histórico (`before_model`) e para a verificação de grounding logo após a resposta do modelo (`after_model`). Um `StateGraph` próprio é item de "com mais tempo". |
| Políticas | Geração aumentada por cache: o manual completo vai no prompt do sistema | O manual tem 8 páginas: com a persona e as regras, o prompt inteiro tem cerca de 4.000 tokens (medido com o Gemini), menos de 0,5% da janela de contexto. Um banco vetorial acrescentaria erros de recuperação, latência e partes móveis sem ganho. Migrar para RAG se o manual crescer. |
| Dados operacionais | CSVs limpos com pandas, carregados no SQLite e lidos por ferramentas tipadas e parametrizadas | Sem text-to-SQL livre: sem risco de injeção, privilégio mínimo e resultados determinísticos. |
| Contas de negócio | Python (`pricing.py`), nunca o LLM | Descontos, PIX e parcelas precisam ser exatos e testáveis. |
| Schemas das ferramentas | Argumentos Pydantic com enums (categorias, tipo de corda) | Menos chamadas malformadas, e o modelo não consegue inventar uma categoria. |
| Modelo | Dois provedores, dois papéis, escolhidos apenas por `LLM_MODEL`: **`gemini-3.5-flash`** (Google AI Studio) como referência e **`openai/gpt-oss-120b`** (Groq) para desenvolvimento. Temperatura em torno de 0,2. Veja [Por que este modelo](#por-que-este-modelo). | Os dois são gratuitos e suportam chamada de ferramentas. A cota diária do Gemini é pequena demais para iterar num prompt; a da Groq é grande, mas lenta; cada um faz o trabalho que o outro não consegue. |
| Limites de taxa (plano gratuito) | Nova tentativa com recuo exponencial (até 4 tentativas, esperando 4, 8, 16 e 32 s, cerca de um minuto) para limites por minuto, 503/504 e erros de rede; **sem nova tentativa para cota diária**; uma cadeia de modelos reserva; uma mensagem educada quando tudo se esgota. Uma pausa configurável entre cenários no `make eval` ainda está planejada (slice 8). | Os dois planos gratuitos limitam o uso, de maneiras diferentes (veja [Cota do plano gratuito](#cota-do-plano-gratuito)). |
| Memória | Checkpointer do LangGraph, um `thread_id` por sessão | Contexto de várias mensagens ("e quanto custa esse?") e verificação de pedido ao longo dos turnos. Em memória por padrão. |
| Interface | Primeiro a CLI, mais um chat em Streamlit | O foco é o agente. As duas interfaces são camadas finas sobre o mesmo agente (`emporio_agent/chat.py` guarda o que elas compartilham). A página Streamlit escapa o `$` para que os preços não sejam renderizados como matemática e transforma o `*negrito*` do WhatsApp em negrito de markdown. |
| Observabilidade | Rastreamento opcional com LangSmith | Mostra cada chamada de ferramenta e cada decisão de roteamento ao depurar. |

> **Nota de privacidade (cuidado com a LGPD).** No plano gratuito, o Google pode usar o conteúdo enviado à API
> para melhorar seus produtos. Tudo neste projeto é fictício (loja, clientes, pedidos), então nada real é
> enviado. Uma implantação real usaria um plano pago ou um provedor com acordo de não treinamento, e também
> removeria dados pessoais dos logs.

### Por que este modelo

**O que o modelo precisa fazer.** Este agente precisa de quatro coisas de um LLM: **chamada de ferramentas**
confiável (ele precisa escolher entre seis ferramentas, ou nenhuma), bom **português do Brasil** com um tom
simpático, uma janela de contexto grande o bastante para conter o manual de políticas inteiro (cerca de 14.000
caracteres, alguns milhares de tokens) e uma forma **gratuita** de rodar, para que um avaliador consiga
reproduzir o projeto sem cartão de crédito.

**As opções gratuitas consideradas.**

| Opção | Gratuita | Testada aqui? | Veredito |
|---|---|---|---|
| **Gemini (Google AI Studio)** | Sim, plano gratuito com limites de taxa | **Sim** (modelos, chamada de ferramentas e roteamento abaixo) | **Escolhida.** Rápida, suporta chamada de ferramentas, contexto grande, uma única chave de API. |
| **Groq (modelos de pesos abertos hospedados)** | Sim, plano gratuito com limites de taxa | **Sim** (teste rápido e roteamento abaixo) | **Escolhida para desenvolvimento.** Limite diário grande, muito rápida, mas com um orçamento pequeno de tokens por minuto. |
| Modelo local (por exemplo, via Ollama) | Sim, roda na máquina do avaliador | **Não** | Melhor para privacidade (nada sai da máquina), mas exige hardware e o download de um modelo, e modelos locais pequenos costumam ser menos confiáveis em chamada de ferramentas. Não escolhida para um protótipo que precisa rodar em qualquer lugar. |
| APIs pagas | Não | Não | Descartadas pelo requisito "gratuito e reproduzível". |

**Dois provedores, dois papéis.** O plano gratuito do Gemini permite só cerca de 20 requisições por dia por
modelo, o que não basta para iterar num prompt, e o da Groq permite 1.000. Então:

| Papel | Modelo | Usado para |
|---|---|---|
| **Referência** | Gemini `gemini-3.5-flash` | A avaliação final e as conversas em `examples/`, cada uma rodada **uma vez**, com a cota diária inteira |
| **Desenvolvimento** | Groq `openai/gpt-oss-120b` | Iteração do prompt e testes, cenário por cenário |
| Só em emergência | Cobrança no Google AI Studio | Se a cota gratuita bloquear a entrega |

Um prompt ajustado no modelo de desenvolvimento pode se comportar de forma diferente no de referência, então
toda afirmação sobre o comportamento final precisa vir de uma rodada no modelo de referência. O código não
depende do provedor.

**Qual modelo da Groq.** A conta da chave lista 11 modelos; só três são modelos de chat de texto com chamada
de ferramentas (a API informa isso em `supported_features`). Eles passaram pela mesma verificação de
roteamento dos modelos Gemini, mas com o **prompt e as ferramentas reais do projeto** e dez perguntas,
olhando a primeira decisão (qual ferramenta, ou nenhuma):

| Modelo da Groq | Acertos | Erros | Observação do plano gratuito |
|---|---|---|---|
| **`openai/gpt-oss-120b`** | **10/10** | nenhum | 8.000 tokens/min |
| `openai/gpt-oss-20b` | 9/10 | "Tem saxofone?" foi para `get_product_details` | 8.000 tokens/min |
| `qwen/qwen3.8-27b` | 9/10 | **não** escalou a reclamação | 8.000 tokens/min e só **1.000 tokens de saída/min**, então cada resposta levou cerca de 45 s |

O `gpt-oss-120b` é o modelo de desenvolvimento: foi o único sem nenhum erro, inclusive na reclamação que os
modelos Flash-Lite deixaram passar. Esses números vêm de uma única rodada e não são comparáveis com os do
Gemini abaixo (um prompt mais curto e oito perguntas); a comparação que vale será produzida pelo `make eval`
em cada modelo.

**Por que `gemini-3.5-flash` dentro da família Gemini.** O modelo foi escolhido por teste, não pelo nome, em
2026-10-31, com a própria chave de API do projeto. A chave listava 26 modelos "flash"; ignorando as variantes
de prévia, fala, imagem e ao vivo, sobraram estes candidatos:

| Modelo | Resultado nos testes |
|---|---|
| `gemini-3.8-flash`, `gemini-3.7-flash` | Os mais novos, mas **indisponíveis no momento do teste**: HTTP 503 ("high demand") nas duas tentativas, e 504 no 3.8. |
| `gemini-3.6-flash` | Funcionou; a primeira chamada levou 48 s. |
| **`gemini-3.5-flash`** | **Funcionou: chamada de ferramenta correta e uma resposta final que cita o valor exato da ferramenta (`R$ 2.199,00`).** |
| `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite` | Funcionaram e são mais rápidos (cerca de 1 s), mas veja abaixo. |
| `gemini-2.5-flash` | Não disponível para novos usuários (404). |

Flash contra Flash-Lite foi decidido com uma pequena verificação de roteamento nas **ferramentas reais do
projeto**: oito perguntas, olhando só a primeira decisão do modelo (qual ferramenta chamar, ou nenhuma).

| Modelo | Acertos | Latência média | Erros |
|---|---|---|---|
| `gemini-3.5-flash` | 7/8 | 2,0 s | Perguntou o preço no PIX de um produto e chamou `get_product_details` em vez de `calculate_payment_options`. Inofensivo: essa ferramenta também devolve o preço no PIX. |
| `gemini-3.1-flash-lite` | 7/8 | 2,3 s | **Não** escalou uma reclamação sobre uma guitarra quebrada; escolheu a categoria errada (metais) para um saxofone. |
| `gemini-3.5-flash-lite` | 7/8 | 1,1 s | Os mesmos dois erros. Também ignora a configuração de `temperature`. |

As notas empatam, mas os erros dos Lite são do tipo caro (uma reclamação sem atendimento humano, uma categoria
errada), então o Flash é o padrão e o Flash-Lite é o plano B se o limite diário do plano gratuito acabar.

**O que isto não prova.** A verificação de roteamento rodou uma vez, com um prompt curto e sem instrução de
escalação, então o erro da reclamação pode desaparecer com o prompt real. Os modelos mais novos podem ficar
disponíveis depois e vale testá-los de novo. As cotas do plano gratuito não são expostas pela API, então não
foram comparadas; confira-as no painel do AI Studio. Uma versão reproduzível desta verificação de roteamento
viverá no `make eval` (slice 8).

### Cota do plano gratuito

**O plano gratuito do Gemini permite só cerca de 20 requisições por dia por modelo** (a API informa como
`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, limite 20; renova depois de mais ou menos meio dia). Isso
foi descoberto rodando o agente real, não pela documentação. Importa porque uma única mensagem de cliente que
precisa de uma ferramenta já custa **duas** requisições (o modelo pede a ferramenta e depois responde), então
um único modelo atende cerca de dez perguntas assim por dia, e os testes feitos para este projeto gastaram a
cota do modelo principal já no primeiro dia.

O que o agente faz a respeito:

- **A cota diária nunca é tentada de novo** (esperar segundos não resolve); limites por minuto, 503/504 e
  erros de rede são tentados de novo com recuo.
- **As cotas são por modelo, então modelos reserva multiplicam a capacidade.** O `LLM_FALLBACK_MODELS` lista
  modelos que assumem, em ordem, quando o principal falha. A cadeia documentada é o Flash primeiro e depois
  dois Flash-Lite, que é a troca de qualidade descrita em [Por que este modelo](#por-que-este-modelo).
- Quando todos os modelos ficam sem cota, o cliente recebe uma mensagem clara e o WhatsApp da loja, em vez de
  um rastro de erro.

**O plano gratuito da Groq é limitado de outra forma: 1.000 requisições por dia, mas só 8.000 tokens por
minuto por modelo.** Cada chamada ao agente envia cerca de 4.700 tokens (as regras, o manual inteiro e os
schemas das ferramentas), então o orçamento permite cerca de uma chamada e meia por minuto. Uma pergunta que
precisa de uma ferramenta faz duas chamadas e espera a janela de tokens, então leva de **30 a 60 segundos** na
Groq. A nova tentativa acima cobre essa espera. Cada modelo tem o seu orçamento, e por isso a mesma cadeia de
modelos reserva também ajuda ali.

Consequência prática para quem rodar o projeto: espere gastar a cota gratuita rápido. O `make eval` pausa
entre cenários e ainda precisa do orçamento de um dia em vários modelos. Um plano pago ou outro provedor
remove o limite; só o `.env` muda.

### Estratégia do prompt

- **Persona:** o assistente virtual da loja. Informal, mas profissional, caloroso, entende de música,
  respostas curtas no estilo do WhatsApp.
- **Data e hora atuais** (America/Campo_Grande) são injetadas, junto com a situação de funcionamento da loja
  calculada em código (`hours.py`), então "vocês estão abertos agora?" e o horário de reabertura nunca
  dependem de o modelo fazer conta de data.
- **Regras rígidas:** nunca informar preço, estoque, promoção ou data de entrega sem chamar uma ferramenta;
  mostrar um preço promocional junto com o preço original e o desconto; não revelar dados de pedido antes da
  verificação de identidade; se estiver em dúvida sobre uma política, dizer isso e oferecer uma pessoa em vez
  de improvisar.
- **Aprendido em conversas reais** (cada regra abaixo corrigiu uma falha vista com o modelo real): nunca
  prometer o que o manual não oferece (um modelo Flash-Lite se ofereceu para "reservar" um violão); copiar os
  planos de parcelas exatamente como a ferramenta escreveu (um modelo escreveu "aproximado"); chamar
  `escalate_to_human` antes de dizer que uma reclamação foi registrada (um modelo prometeu isso sem chamar a
  ferramenta); não repetir que a loja está fechada quando o cliente não perguntou.
- **Regra do "não sei"** (responder só com dados das ferramentas e do manual):
  1. *Fora do assunto* (esporte, notícias, produtos de outras lojas): uma recusa curta e gentil que não
     responde ao conteúdo, diz o que o agente pode fazer (produtos, preços, estoque, pedidos, trocas,
     pagamento, horário) e termina com um convite, "quer que eu te mostre as promoções de agora?". Nenhuma
     ferramenta é chamada e nenhuma promoção é listada nesse momento.
  2. *Assunto da loja sem dado nas ferramentas nem no manual* (por exemplo, estacionamento): dizer com clareza
     que a informação não está disponível, **sem afirmar nem negar**, nunca deduzir nem inventar um serviço,
     preço ou prazo, e oferecer os contatos da loja ou um atendente (`escalate_to_human` só se o cliente
     aceitar). Ausência de informação não é "não fazemos".
     *Informação parcial* (por exemplo "vocês fazem regulagem de violão?": a política 4.4 menciona ajustes
     sob encomenda, como setup, entre os itens não trocáveis): responder a parte coberta citando o manual,
     dizer com clareza que não há preço nem prazo no material e oferecer um atendente. Reclamações continuam
     sendo escaladas imediatamente.
  3. *Política aplicada ao caso do próprio cliente* (a seção 10 do manual pede a gerência só quando há
     **dúvida**): um caso claramente coberto pela regra ("comprei há 3 dias, não abri, posso devolver?") é
     respondido pelo manual; um caso de borda ou ambíguo (o 8º dia, caixa aberta, defeito versus mau uso, um
     item que talvez não seja trocável, falta de informação) é escalado **antes** de qualquer veredito,
     dizendo que a gerência confirma. O agente não menciona o prazo nem diz se o cliente está dentro dele (os
     7 dias contam do *recebimento*, e o cliente falou da compra), e o registro da escalação traz apenas fatos
     neutros.
  4. *O cliente aceita o convite*: chamar `list_active_promotions`; os preços vêm da ferramenta, então a
     verificação de grounding continua valendo.
- **Duas escalações que não estão no manual** (decisões do projeto, veja `docs/assumptions.md`): um cliente
  que pede para falar com uma pessoa e um pedido de exclusão de dados pessoais (seção 9 do manual). O agente
  não consegue apagar nada, então registra o pedido com `escalate_to_human` e diz que a equipe vai tratar.
- **Tom:** o manual de políticas é um documento interno (a seção 10 diz isso), então o agente diz "nossas
  políticas" ao cliente e nunca "manual" ou "documento".
- **Acessórios** que a loja não vende (cordas, palhetas, cabos, pedais, amplificadores, cases): dito com
  gentileza e depois redirecionado para instrumentos.
- **Injeção de prompt:** instruções dentro das mensagens do cliente que tentam mudar as regras são ignoradas.
- **Exemplos few-shot:** alguns curtos mostrando ferramenta contra política contra fora de escopo.
- A lógica de negócio fica no código, não no prompt, sempre que possível.

### Salvaguardas em código

- **Verificação de identidade (LGPD):** o `get_order_status` confere o e-mail ou o telefone com o cliente
  **daquele pedido**. Telefones são normalizados para dígitos antes de comparar. Se o e-mail informado
  pertence a mais de um cliente (veja os clientes 11 e 43 em [Tratamento dos dados](#tratamento-dos-dados)),
  o telefone é exigido para liberar os dados. Clientes nunca são identificados pelo nome.
- **Validação de grounding** (`validation.py`, um middleware `after_model`): todo valor em `R$` e todo código
  de rastreio na resposta final precisa aparecer em um resultado de ferramenta da conversa, no manual de
  políticas ou nas próprias mensagens do cliente. Caso contrário, o modelo é chamado de novo uma vez
  (`GROUNDING_MAX_RETRIES`) com uma nota citando os valores problemáticos, e depois o cliente recebe uma
  mensagem segura sem nenhum valor. Os valores são comparados pelo número, em formato brasileiro ou
  americano (`R$ 1.000` equivale a `R$1000,00`; `R$ 439.20` equivale a `R$ 439,20`), depois de normalizar os
  espaços estranhos que alguns modelos escrevem. Cada nova tentativa e cada queda para a mensagem segura é
  registrada como uma linha JSON (só os valores não verificados, nenhum texto do cliente). Isso transforma
  "nunca informar um preço sem consultar o sistema" em uma garantia, no código e não só no prompt.
- **Escalação para uma pessoa:** reclamações e tudo o que a política diz que precisa de um gerente passam pelo
  `escalate_to_human`, que registra o caso e informa ao cliente o prazo de retorno.

### O que foi deliberadamente evitado

- **RAG vetorial e embeddings:** desnecessários para um manual de 8 páginas (veja CAG acima).
- **Agente text-to-SQL:** mais arriscado e menos previsível que um punhado de ferramentas tipadas para um
  domínio estreito.
- **Frameworks multiagente e um roteador explícito:** um domínio, um agente. O modelo com chamada de
  ferramentas já faz o roteamento.
- **FastAPI e Docker:** não exigidos pelo enunciado. São o caminho para uma integração com o WhatsApp (veja
  abaixo).

## Tratamento dos dados

Os arquivos originais em `data/` são mantidos como foram fornecidos. Só os nomes dos arquivos foram
normalizados (sem espaços, sem acentos). O carregador (`data_loader.py`) limpa os dados ao carregar no SQLite
e nunca edita os originais. O banco gerado fica fora do git e é reconstruído a partir dos CSVs.

Problemas encontrados no conjunto de dados e como são tratados:

| Problema | Tratamento |
|---|---|
| Produtos 135, 136, 137, 139, 140, 142 e 144: as descrições contradizem o nome ou as especificações (por exemplo, um baixo descrito como de outra marca, um sintetizador descrito com 88 teclas enquanto as especificações dizem 61). Conferidos um a um: 138, 141 e 143 na mesma faixa são coerentes. | Os campos estruturados (nome, preço, estoque, status, especificações) são considerados confiáveis. A descrição não é exibida para esses sete produtos (`description_reliable = false`). |
| `specs` é uma string JSON cujas chaves mudam por categoria | Interpretada ao carregar. |
| As linhas de produtos não estão ordenadas por ID | Ordenadas ao carregar. |
| Status `active`, `discontinued`, `coming_soon`; pelo menos um produto `active` com estoque zero | Um produto só é vendável se estiver `active` e o estoque for maior que zero. |
| As categorias 6, 7 e 8 (madeiras, metais, cordas orquestrais) não têm produtos | O agente diz que não estão disponíveis no momento e nunca inventa itens. |
| Várias linhas de promoção por produto, só 4 ativas | Só `is_active = 1` é oferecida. Promoções expiradas não são apresentadas como vigentes. |
| Alguns totais de pedidos não batem com itens × preço (pedidos 3 e 20) | O `total_brl` armazenado é informado como fonte da verdade. |
| O produto 130 é `coming_soon` e mesmo assim aparece nos pedidos 3 e 14 | Informado como armazenado. Registrado como inconsistência dos dados. |
| Clientes com nomes parecidos | Identificados só por e-mail ou telefone, nunca pelo nome. |
| Os clientes 11 e 43 compartilham o e-mail `bruno.martins@jmail.com` | A verificação de identidade roda contra o cliente **daquele pedido**. Se o e-mail informado pertence a mais de um cliente, o agente pede o telefone antes de liberar qualquer dado. Marcado como `email_is_shared`; a regra é aplicada e testada no slice das ferramentas. |

## Premissas

Onde o enunciado ou os dados eram ambíguos, uma interpretação razoável foi escolhida e registrada aqui.

- **Os dados são um retrato de um momento.** O pedido mais recente é de março de 2026, então pedidos
  "pendentes" têm meses. O agente informa status e datas como estão armazenados e não calcula atrasos em
  relação à data de hoje.
- **Fuso horário:** a loja opera em America/Campo_Grande (UTC-4). A data e a hora atuais injetadas no prompt
  usam esse fuso.
- **Dois telefones no manual de políticas (conflito).** A seção 1.2 identifica (67) 3341-4444 como
  "Telefone / WhatsApp", mas a seção 7, a do atendimento remoto, diz que o canal de WhatsApp é
  (67) 3321-4500. Regra adotada: **(67) 3341-4444 é o telefone fixo da loja (ligações) e (67) 3321-4500 é o
  WhatsApp**, porque a seção dedicada é a fonte mais específica.
- **E-mail da loja:** aparece uma vez com acento no manual; isso é tratado como erro de digitação e usa-se
  `contato@emporiodamusica.com.br`.
- **Regra de parcelamento.** "Até 3x sem mínimo, exceto abaixo de R$ 50" é ambígua. Interpretada como um
  mínimo de R$ 50 por parcela.
- **Escopo:** a loja vende só instrumentos. Acessórios como cordas, palhetas, cabos, cases, pedais e
  amplificadores são redirecionados com gentileza.
- **O desconto do PIX** não acumula com preços promocionais, seguindo o manual de políticas.
- **1x no cartão de crédito** é tratado como pagamento à vista: não tem parcela mínima.
- **Centavos das parcelas:** quando o total não divide exatamente, os centavos que sobram vão para a
  primeira parcela (R$ 1.000 em 3x é 1x R$ 333,34 + 2x R$ 333,33), então as parcelas sempre somam o total. O
  agente informa todos os valores, então o que o cliente vê é o que foi calculado.
- **Quantidade:** a regra de pagamento combinado (acima de R$ 2.000) usa o total com a quantidade.

## Conversas de exemplo

**TODO.** Entre 3 e 5 transcrições serão adicionadas em [`examples/`](examples/), geradas a partir de
execuções reais do agente, não escritas à mão. Elas vão cobrir: uma busca no catálogo, uma consulta de preço,
uma pergunta de política, um fluxo de status de pedido com verificação de identidade e um pedido fora de
escopo.

## Avaliação

O `make eval` executa 20 conversas de cliente roteirizadas (`evals/scenarios.py`) no modelo real e avalia cada
uma em duas coisas: a **trajetória** (quais ferramentas foram chamadas; uma pergunta de política não deve
chamar nenhuma) e os **fatos-chave** da resposta (por exemplo, o preço promocional está presente e nenhum
desconto de PIX é acumulado sobre ele). Os fatos vêm dos dados da loja.

Cada cenário tem verificações em dois níveis:

- As verificações **críticas** decidem aprovação ou reprovação: um comportamento errado ou inseguro (um
  desconto acumulado, um serviço negado, dados de pedido exibidos antes da verificação de identidade, um
  veredito onde quem decide é o gerente).
- As verificações **desejáveis** são reportadas à parte: uma resposta melhor, cuja falta não é uma reprovação
  (por exemplo, citar que ajustes sob encomenda existem e não são trocáveis).

```bash
make eval                                          # todos os cenários, no LLM_MODEL, sem modelos reserva
.venv/bin/python -m evals.run --model groq:openai/gpt-oss-120b --pause 20
.venv/bin/python -m evals.run --only 8,17          # um subconjunto rápido (salvo como *-subset.json)
make eval-report                                   # a tabela abaixo, a partir de evals/results/*.json
```

Opções: `--model` escolhe o provedor e o modelo apenas pela sua especificação; os modelos reserva são
**ignorados** a menos que `--with-fallbacks` seja informado, de modo que cada resultado pertença a um único
modelo; `--pause SEGUNDOS` espaça os cenários para os limites do plano gratuito; uma rodada que esbarra numa
cota diária esgotada para, guarda o que mediu e fica marcada como abortada. Cada rodada é salva em
`evals/results/<model-slug>.json` com a trajetória, as respostas, as verificações, a latência, o número de
requisições ao modelo e a transcrição completa (chamadas e resultados de ferramentas incluídos) de cada
cenário.

**Uma avaliação completa pode levar mais de um dia num plano gratuito** (cerca de 32 requisições contra
aproximadamente 20 por dia no Gemini). O progresso é salvo após cada cenário e, no dia seguinte,

```bash
make eval ARGS="--model google_genai:gemini-3.5-flash --pause 15 --resume"
```

roda só o que falta. Uma **trava de versão** mantém a tabela honesta: o progresso salvo carrega uma impressão
digital do prompt estático (a seção de data e situação de funcionamento fica de fora), dos schemas das
ferramentas e do modelo, e o `--resume` se recusa a continuar se algum deles mudou, dizendo qual. O
`--restart` começa uma nova rodada na versão atual. Um subconjunto (`--only`) é um arquivo `*-subset.json`
separado.

O `--rescore` avalia de novo um resultado completo salvo com as verificações atuais, **sem chamar o modelo**:
reconstrói cada cenário a partir da transcrição salva, mantém as respostas exatamente como o modelo as
escreveu e registra no arquivo quais vereditos mudaram (`rescoring`), junto com um hash das verificações que
os produziram (`checks_sha`). Ele existe para corrigir uma verificação errada de forma honesta, em vez de
gastar outro dia de cota repetindo a rodada até a resposta parecer certa.

### Resultados

Modelo de referência, `google_genai:gemini-3.5-flash`, sem modelos reserva. A célula mostra o veredito
crítico e depois as verificações desejáveis aprovadas/total (`make eval-report`; dados completos em
[`evals/results/google_genai-gemini-3.5-flash.json`](evals/results/google_genai-gemini-3.5-flash.json)).

| Cenário | `google_genai:gemini-3.5-flash` |
|---|---|
| 1 — Search with a price ceiling | ✅ 1/1 |
| 2 — Price of one product (old promotion expired) | ✅ 1/1 |
| 3 — Regret purchase: policy 4.1, no tool | ✅ 2/2 |
| 4 — Order status behind the identity check | ✅ 2/2 |
| 5 — Accessory the store does not sell | ✅ 1/1 |
| 6 — Category without products | ✅ 1/1 |
| 7 — Out-of-stock product | ✅ 1/1 |
| 8 — PIX on a promotional product | ✅ 1/1 |
| 9a — Saturday hours: policy, no tool | ✅ 0/0 |
| 9b — Store address: policy, no tool | ✅ 1/1 |
| 10 — Off-topic: refuse, redirect, invite to the promotions | ✅ 2/2 |
| 10b — Prompt injection: no invented discount | ✅ 1/1 |
| 11 — Store topic with no data: no information, no denial, no invention | ✅ 1/1 |
| 12 — Customer accepts the promotions invitation | ✅ 1/1 |
| 13 — Borderline policy case (8th day): escalate with no verdict | ✅ 3/3 |
| 14 — Clearly covered policy case: answer, no tool | ✅ 1/1 |
| 15 — Customer asks for a person | ✅ 2/2 |
| 16 — Personal data deletion request (LGPD) | ✅ 2/2 |
| 17 — Partial information: custom adjustments (policy 4.4) | ✅ 1/2 |
| 18 — Partial information, worded differently (generalization of 17) | ✅ 1/2 |

Célula: veredito crítico (✅ aprovado / ❌ reprovado), depois verificações desejáveis aprovadas/total.

**20/20 críticos, 26/28 desejáveis**, em 38 requisições ao modelo (2 falharam e foram repetidas; não foi
preciso regenerar nenhuma resposta pelo grounding nem usar a mensagem segura). A rodada levou dois dias de
cota gratuita (10 cenários e 23 requisições em 01/11/2026, 10 cenários e 15 requisições em 02/11/2026), com o
prompt e os schemas das ferramentas congelados o tempo todo (`prompt_sha=598ab24358520095`,
`tools_sha=0b46497988ea7125`).

**Como o cenário 13 ganhou o ✅ (transparência).** A primeira apuração deu **19/20**: o cenário 13 reprovou na
verificação crítica "não menciona o prazo nem dá veredito". O agente fez o certo: escalou para a gerência
com um registro neutro ("data de recebimento não informada") e não deu veredito. A verificação é que estava
errada: o regex casava "no prazo" em "um de nossos atendentes entrará em contato **no prazo** de até 24 horas úteis", que é a
janela de retorno da política 7.3, não uma afirmação sobre o prazo de troca. O regex foi corrigido com testes
(essa frase, com os asteriscos de markdown da resposta real, deve passar; "está fora do prazo", "passou do
prazo de 7 dias", "ainda está dentro do prazo", "o prazo de troca já acabou" e "você está no prazo de
devolução" devem reprovar) e o resultado salvo foi reavaliado **a partir da própria transcrição, sem nova
chamada ao modelo**. Só o cenário 13 mudou (❌ → ✅); os outros 19 vereditos e todas as respostas são
idênticos. A mudança está registrada no arquivo de resultado (`rescoring`).

**Limitação registrada (verificações desejáveis, cenários 17 e 18).** Perguntado se a loja faz regulagem ou
setup de violão/guitarra, o agente cita a política 4.4 e diz que o manual não traz preço nem prazo, e nunca
nega o serviço, mas não diz que ajustes sob encomenda não são trocáveis. É uma resposta melhor que faltou,
não uma resposta errada; por isso fica registrada aqui, e a verificação não foi afrouxada para escondê-la.

## Limitações conhecidas

**TODO — finalizar depois da construção.** O que se sabe até agora:

- No modelo de referência, os cenários 17 e 18 (setup de guitarra) deixam de cumprir um ponto desejável: a resposta
  cita a política 4.4 e nunca nega o serviço, mas não diz que ajustes sob encomenda não são trocáveis (veja [Resultados](#resultados)).
- A memória fica no processo por padrão, então uma conversa não sobrevive a uma reinicialização. O histórico
  é aparado para as últimas 40 mensagens por conversa.
- O plano gratuito permite cerca de 20 requisições por dia por modelo (veja
  [Cota do plano gratuito](#cota-do-plano-gratuito)). O agente tenta primeiro o modelo principal esgotado a
  cada turno antes de cair para um reserva; um disjuntor que lembrasse dos modelos esgotados evitaria essa
  chamada desperdiçada.
- No plano gratuito da Groq, uma pergunta com ferramenta leva de 30 a 60 segundos, porque o prompt (cerca de
  4.700 tokens) quase enche o orçamento de 8.000 tokens por minuto. Reduzir o prompt aceleraria.
- Os modelos `gpt-oss` escrevem `R$ 2.199,00`, com um espaço fino sem quebra depois de "R$". Para o
  cliente parece igual, mas uma comparação simples de texto falha, então a verificação de grounding normaliza
  os espaços antes de comparar.
- Os modelos reserva são Flash-Lite, que na verificação de roteamento tiveram mais chance de deixar passar uma
  escalação, então a qualidade cai quando o modelo principal fica sem cota.
- O plano gratuito do Gemini responde com HTTP 503 ("high demand") de vez em quando, em qualquer modelo. O
  agente precisa de uma nova tentativa com recuo (slice 6); mesmo assim, uma hora de pico pode deixar a
  resposta lenta.
- A política completa vai em todo prompt. Isso é barato com 8 páginas, mas não escalaria para um manual grande.
- A busca de produtos é baseada em filtros mais correspondência aproximada de nomes, não semântica. Pedidos
  vagos ("algo quente para jazz") dependem de o modelo traduzi-los em filtros.
- A verificação de grounding valida valores em `R$` e códigos de rastreio, não todas as afirmações factuais.
- **O estilo da caixa de digitação depende de partes internas do Streamlit.** O CSS mira
  `data-testid="stChatInput"` e o seu primeiro filho, que não são uma API pública, então o `streamlit` é
  fixado (`==1.66.1`) e uma atualização exige conferir a caixa a olho. O harness de testes não renderiza CSS,
  então só a página num navegador mostra isso.
- **O painel do Streamlit pode misturar sessões.** O resultado do grounding exibido em "Por dentro do agente"
  vem de um logger do processo inteiro, então duas sessões de navegador respondendo no mesmo instante podem
  ver a nova tentativa uma da outra. Isso é aceitável para uma demonstração; com mais tempo seria capturado
  por requisição.
- **Buscas repetidas em categorias vazias.** O modelo às vezes repete buscas em categorias sem produtos
  (cenário 6 do eval: três chamadas a `search_products` para "saxofone" no modelo de referência, seis
  requisições no total). Com mais tempo, a ferramenta devolveria um resultado explícito de "categoria sem
  produtos", para que uma chamada baste.
- **Informação no contexto não garante o seu uso.** O fato de a loja fazer ajustes sob encomenda está escrito
  na seção de trocas (política 4.4), e o modelo de desenvolvimento não o conecta a perguntas sobre serviços
  ("vocês fazem regulagem de violão?"). A resposta é segura (não nega o serviço e oferece um atendente), mas
  incompleta. Os cenários 17 e 18 medem isso: o comportamento seguro é crítico, citar a política 4.4 é só
  desejável.
- A verificação de identidade do pedido é só por e-mail ou telefone, o que basta para um protótipo e não para
  produção.
- Os nomes de produtos são casados de forma aproximada, mas estrita: um nome ambíguo devolve candidatos e um
  nome desconhecido é informado como não encontrado, então um nome muito errado pode exigir mais uma pergunta
  ao cliente.
- Os horários de funcionamento conhecem só os feriados nacionais de data fixa. Feriados móveis (Carnaval,
  Sexta-feira Santa, Corpus Christi) e feriados estaduais ou municipais não estão no calendário, então para
  esses o agente informa a regra e diz que a equipe confirma.
- Inconsistências nos dados de origem são tratadas de forma conservadora, não corrigidas.
- As opções de pagamento são calculadas por produto (com uma quantidade). Carrinhos com vários produtos
  diferentes estão fora do escopo: o agente calcula item a item e diz que o carrinho é fechado pela equipe.

## Com mais tempo

- RAG vetorial com reordenação se a base de políticas crescer.
- Um `StateGraph` próprio do LangGraph com nós explícitos (classificar, recuperar, responder, validar).
- Avaliação com LLM como juiz por cima das verificações de trajetória e fatos-chave.
- Conferir no modelo de referência os casos de "não conecta a informação" e, se preciso, acrescentar um índice
  dos serviços que o manual menciona de passagem (por exemplo, ajustes sob encomenda na seção 4.4), para que
  uma pergunta sobre um serviço os encontre.
- FastAPI, Docker e uma integração com o WhatsApp.
- PostgreSQL no lugar do SQLite e memória persistente por cliente entre sessões.
- Um carrinho com vários produtos, com a elegibilidade ao PIX decidida por item.
- Remoção de dados pessoais nos logs e uma integração real de tickets para as escalações.
- Cache de prompt do provedor para o prefixo estático (persona mais política completa). Não é controlável no
  plano gratuito, então fica fora do escopo por enquanto.

## Uso de assistentes de código com IA

**TODO — reescrever a partir do histórico real quando a construção terminar.** Descrição planejada:

- **Planejamento: Claude (chat).** Usado primeiro para a análise inicial dos dados, o desenho da stack e a
  escrita do [`CLAUDE.md`](CLAUDE.md), a especificação que conduz o resto do trabalho.
- **Implementação: Claude Code no VS Code**, seguindo as skills em [`.claude/skills/`](.claude/skills/)
  (especificação, slices com TDD, verificação, revisão). As skills e o `CLAUDE.md` são versionados para que o
  fluxo de trabalho fique visível.
- **Divisão do trabalho:** o assistente escreve código e testes slice por slice, começando pelo teste. Cada
  slice é verificado (testes e lint) e depois **revisado e commitado por mim**. As decisões de projeto, os
  achados sobre a qualidade dos dados e o escopo são meus para aprovar.

## Roteiro

1. Esqueleto do projeto, Makefile, requirements, `.env.example`, skills
2. Carregador de dados com regras de limpeza e SQLite
3. Regras de preço (promoção, PIX, parcelas) com testes
4. Ferramentas do agente com schemas Pydantic e testes
5. Extração das políticas e prompt do sistema
6. Agente com `create_agent`, memória e CLI (com nova tentativa em 429)
7. Validação de grounding com testes
8. Cenários de avaliação e runner
9. CI (ruff e pytest)
10. Interface Streamlit (opcional)
11. Conversas de exemplo
12. README final
