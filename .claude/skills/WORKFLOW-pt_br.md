# Workflow

> **Tradução pt-BR.** Versão canônica (em inglês): [WORKFLOW.md](WORKFLOW.md). Em caso de divergência, vale o original.

## Fases

1. **Spec** — problema, escopo, critérios de aceite
2. **Plan** — vertical slices, ordem, verificação por slice
3. **Branching** — baseline limpo, branch de trabalho
4. **Build** — a menor mudança útil (prefira TDD para novo comportamento ou bugs)
5. **Verify** — evidência de que a mudança funciona
6. **Review** — review do diff proporcional ao risco
7. **Simplify** — remover complexidade acidental (comportamento preservado)
8. **Ship** — checagens finais, resumo de entrega, integração

## Regras

- Use apenas o contexto necessário para a **fase atual**.
- Use **adapters** só quando configurados e relevantes ([ADAPTERS-pt_br.md](ADAPTERS-pt_br.md)).
- **Não** dependa de uma ferramenta ou IDE específica.
- **Não** pule a verificação.
- **Não** faça ship sem evidência mínima de qualidade ([CONVENTIONS-pt_br.md](CONVENTIONS-pt_br.md)).

## Bootstrap opcional

Use `setup-workflow` apenas para preparar um repositório que não tenha convenções, política de contexto ou Definition of Done em `docs/ai-workflow/`.

O Core Workflow **não** depende dessa etapa.

## Mapa de skills

| Fase | Skill |
|-------|-------|
| Spec | `to-spec` |
| Plan | `to-issues` |
| Branching | `git-workflow-and-versioning` |
| Build | `tdd` |
| Verify | `slice-verification` (por slice); `finishing-a-development-branch` Step 1 (branch inteira antes do ship) |
| Review | `code-review` |
| Simplify | `code-simplification` |
| Ship | `finishing-a-development-branch` |

Opcional antes de Spec/Plan: `context-discovery` (Toolbox). Depois do Review: `improve-codebase-architecture` (Toolbox) quando o problema é estrutura, não legibilidade.

## Fast path

Para mudanças **pequenas, seguras e localizadas**:

1. Entenda o pedido.
2. Implemente a menor mudança.
3. Rode a verificação relevante.
4. Faça review proporcional ao risco.
5. Entregue um resumo curto.

**Não use o fast path quando houver:** nova regra de negócio, impacto de segurança, migração de dados, mudança arquitetural, integração externa, impacto em produção ou comportamento esperado incerto.

## Leitura adicional

| Documento | Propósito |
|----------|-----------|
| [workflow/contracts.md](workflow/contracts.md) | Entrada / ação / saída por fase (em inglês) |
| [workflow/pipeline-reference.md](workflow/pipeline-reference.md) | HITL, tabelas, layout no repo alvo (em inglês) |
| [DEFINITION_OF_DONE-pt_br.md](DEFINITION_OF_DONE-pt_br.md) | Quando uma mudança está pronta |
| [CONTEXT_POLICY-pt_br.md](CONTEXT_POLICY-pt_br.md) | Contexto mínimo entre fases |
| [README-pt_br.md](README-pt_br.md) | Core vs Toolbox vs checklists vs adapters |
