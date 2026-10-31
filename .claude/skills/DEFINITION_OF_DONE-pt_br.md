# Definition of Done

> **Tradução pt-BR.** Versão canônica (em inglês): [DEFINITION_OF_DONE.md](DEFINITION_OF_DONE.md). Em caso de divergência, vale o original.

Uma mudança está **pronta** quando:

- [ ] **Objetivo está claro** — problema, escopo e critérios de aceite entendidos.
- [ ] **Escopo bate com o pedido** — o trabalho feito mapeia no que foi pedido; o que ficou fora de escopo é sinalizado.
- [ ] **Verificação relevante rodou** — com evidência fresca nesta sessão ([CONVENTIONS-pt_br.md](CONVENTIONS-pt_br.md)).
- [ ] **Evidência disponível está registrada** — ou o sucesso é declarado com evidência.
- [ ] **Código foi revisado** — proporcional ao risco (`code-review` ou risk review do fast path).
- [ ] **Complexidade acidental reduzida quando prático** — depois que o comportamento está protegido (`code-simplification` quando fizer sentido).
- [ ] **Riscos conhecidos comunicados** — segurança, dados, breaking changes, impacto operacional.
- [ ] **Próximo passo explícito** — merge, PR, handoff, patch, tarefa de follow-up ou re-rodar a verificação.

A entrega pode ser merge, publicação, handoff, patch ou review humano — nenhuma ferramenta ou issue tracker específico é obrigatório.

## Por fase do workflow

| Fase | "Pronto" significa |
|-------|------------|
| Spec | Spec curta + critérios de aceite + riscos conhecidos |
| Plan | Slices/tarefas + ordem + como cada slice será verificado |
| Branching | Baseline limpo + branch de trabalho registrada |
| Build | Código + testes para comportamento novo/alterado |
| Verify (slice) | Relatório do [slice-verification](slice-verification/verification-report.md) com evidência |
| Verify (branch) | [finishing Step 1](finishing-a-development-branch/verification-report.md) antes da integração |
| Review | Achados por severidade; bloqueadores resolvidos ou aceitos |
| Simplify | Testes ainda passam; comportamento inalterado |
| Ship | Caminho de integração escolhido; HITL do usuário em remotos quando aplicável |

## Fast path

Para mudanças pequenas elegíveis (veja [WORKFLOW-pt_br.md § Fast path](WORKFLOW-pt_br.md)), um checklist mais leve é aceitável: entender → implementar → verificar → risk review → resumo — mas **evidência antes de afirmação** continua valendo.
