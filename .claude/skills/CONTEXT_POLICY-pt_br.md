# Política de Contexto

> **Tradução pt-BR.** Versão canônica (em inglês): [CONTEXT_POLICY.md](CONTEXT_POLICY.md). Em caso de divergência, vale o original.

## Objetivo

Reduzir contexto e uso de tokens desnecessários, mantendo decisões, riscos e evidência rastreáveis.

## Regras

- Carregue **apenas a skill da fase atual** (mais [CONVENTIONS-pt_br.md](CONVENTIONS-pt_br.md) ao implementar, verificar ou revisar).
- **Não** carregue todas as checklists de review por padrão — o `code-review` seleciona os eixos por risco.
- Prefira **resumos estruturados** a transcrições longas.
- Preserve **decisões, riscos e evidência** — descarte enrolação narrativa.
- **Não** repita a spec inteira em toda fase; linke ou cite bullets.
- Passe **artefatos curtos** entre fases (veja abaixo).
- Mantenha **specs e planos curtos** por padrão; expanda só quando risco, ambiguidade ou o usuário exigir.

Alinhado com [CONVENTIONS-pt_br.md § Comunicação concisa](CONVENTIONS-pt_br.md).

## Adapters e contexto

Carregue adapters só quando:

- forem relevantes para a fase atual;
- estiverem configurados no projeto;
- o benefício justificar o custo de contexto.

Veja [ADAPTERS-pt_br.md](ADAPTERS-pt_br.md). Checklists não são carregadas por padrão.

## Artefatos mínimos entre fases

### Spec

- problema;
- dentro / fora de escopo;
- critérios de aceite;
- riscos conhecidos.

### Plan

- tarefas ou slices;
- ordem;
- método de verificação por slice.

### Build

- arquivos tocados;
- decisão técnica (uma linha cada);
- testes adicionados ou atualizados.

### Verify

- comandos ou métodos rodados;
- pass/fail com evidência (não só afirmações).

### Review

- achados;
- severidade;
- bloqueadores.

### Ship

- resumo;
- riscos;
- próximo passo.

## Toolbox

As skills da Toolbox **não** estão no caminho padrão. Carregue-as só quando o problema exigir bootstrap opcional, diagnóstico, ensino, handoff, exploração de arquitetura ou criação de novas skills. Veja [README-pt_br.md](README-pt_br.md).
