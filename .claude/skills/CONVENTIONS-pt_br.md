# Convenções transversais

> **Tradução pt-BR.** Versão canônica (em inglês): [CONVENTIONS.md](CONVENTIONS.md). Em caso de divergência, vale o original.

Regras canônicas para todas as skills deste pacote. Cada skill aponta para cá em vez de repetir o texto completo.

## Formato do arquivo de skill

As skills do Core Workflow usam esta estrutura no `SKILL.md`:

- YAML `name` e `description` curta (o trigger na primeira frase)
- **When to use** / **When not to use**
- **Expected input** / **Process** / **Expected output**
- **Minimal checklist**
- Exemplos longos → `REFERENCE.md`, `EXAMPLES.md` ou checklists em subpastas

Aponte para cá quando precisar de sessão, evidência e comunicação concisa, em vez de repetir o texto completo.

## Sessão de agente único

Toda skill roda em **um** agente conversacional na sessão **atual**. Complete o trabalho você mesmo — não crie sub-agentes, workers paralelos ou execuções em background no meio de uma skill.

Quando uma skill precisa de várias passadas (ex.: alternativas de design), rode-as **sequencialmente** na mesma sessão.

Uma sessão **nova** é só para um handoff explícito (`handoff`) ou um follow-up iniciado pelo usuário — não para workers paralelos no meio de uma skill.

## Comunicação concisa

**Padrão: respostas curtas.** Prefira o mínimo de texto que ainda responde totalmente ao pedido — bullets em vez de parágrafos, uma recomendação clara em vez de uma comparação longa, sem enrolação, repetição ou postâmbulo.

**Só vá mais longo quando valer a pena**, por exemplo:

- Trade-offs, riscos ou requisitos ambíguos que precisam de explicação real
- Achados que precisam ser acionáveis (relatórios de review, diagnóstico, verificação)
- Procedimentos passo a passo que o usuário deve seguir
- O usuário pede explicitamente profundidade, exemplos ou ensino

**Nunca encurte às custas de:**

- [Evidência antes de afirmação](#evidência-antes-de-afirmação) (output de comando, pass/fail, bloqueadores)
- Templates obrigatórios de skills (ex.: seções de relatório de verificação ou review)
- Detalhe crítico de segurança (segurança, perda de dados, breaking changes)

Compressão extra opcional: `caveman` — só quando o usuário invocar; não dispensa evidência nem relatórios obrigatórios.

## Evidência antes de afirmação

Nada de afirmar "verificado", "passando", push/PR ou merge sem **output de comando fresco nesta sessão**.

1. **Identifique** — o comando que prova a afirmação
2. **Rode** — o comando completo aqui (não só um relatório anterior)
3. **Leia** — o output e os exit codes
4. **Verifique** — o output sustenta a afirmação
5. **Só então afirme**

**Aplicado em:**

| Escopo | Skill |
|-------|-------|
| Slice atual | `slice-verification` |
| Branch inteira (antes de push/PR) | `finishing-a-development-branch` Step 1 |

Procedimento da branch e template de relatório: [finishing-a-development-branch/verification-report.md](finishing-a-development-branch/verification-report.md). Relatório do slice: [slice-verification/verification-report.md](slice-verification/verification-report.md).

## Disciplina de implementação

Diretrizes de comportamento ao escrever, revisar ou refatorar código. Enviesado para cautela em vez de velocidade; use bom senso em tarefas triviais. Complementa a [evidência antes de afirmação](#evidência-antes-de-afirmação) (gates operacionais) com hábitos gerais de código.

### Pense antes de codar

- Declare premissas explicitamente; pergunte quando estiver incerto.
- Se houver múltiplas interpretações, apresente-as — não escolha em silêncio.
- Se existe uma abordagem mais simples, diga; discorde quando fizer sentido.
- Se algo não está claro, pare, nomeie o que está confuso e pergunte.

### Simplicidade primeiro

- O mínimo de código que resolve o problema; nada especulativo.
- Sem features, abstrações, flexibilidade ou tratamento de erro além do que foi pedido.
- Se a solução ficou muito maior que o necessário, simplifique.

### Mudanças cirúrgicas

- Toque só no que o pedido exige; siga o estilo existente.
- Não "melhore" código, comentários ou formatação adjacentes; não refatore código não relacionado.
- Mencione dead code não relacionado; não delete a menos que peçam.
- Remova órfãos que **suas** mudanças criaram (imports, variáveis, funções sem uso).
- Toda linha alterada deve rastrear diretamente ao pedido do usuário.

### Execução orientada a objetivo

- Transforme tarefas em objetivos verificáveis (ex.: testes de entrada inválida antes da validação; reprodutor antes do fix; testes verdes antes/depois de refatorar).
- Para trabalho multi-etapa, declare um plano curto: `passo → verify: checagem` por etapa.
- Critérios fracos ("faz funcionar") precisam de esclarecimento antes de uma implementação grande.

## Descoberta de padrões do repo

Ao implementar, simplificar ou revisar, dê uma olhada nos padrões aplicáveis do repo: `AI_WORKFLOW.md`, `docs/ai-workflow/conventions.md`, `CONTRIBUTING.md`, `STYLE.md`, `STANDARDS.md`, `STYLEGUIDE.md`, `docs/adr/`. Espelhe os padrões próximos. Arquivos específicos de host (`AGENTS.md`, `CLAUDE.md`) são adapters opcionais — veja [ADAPTERS-pt_br.md](ADAPTERS-pt_br.md).
