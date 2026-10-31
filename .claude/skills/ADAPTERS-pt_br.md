# Adapters

> **Tradução pt-BR.** Versão canônica (em inglês): [ADAPTERS.md](ADAPTERS.md). Em caso de divergência, vale o original.

Adapters conectam o AI-Workflow a ferramentas, MCPs, IDEs, agent hosts, issue trackers ou padrões específicos da organização.

**Adapters são opcionais.** Este pacote de workflow pessoal não traz adapters específicos de organização (busca em base de conhecimento, leitores de docs internas, padrões Java da org, etc.).

## Regras

- O Core Workflow não depende de adapters.
- Adapters não devem mudar as fases principais.
- Adapters podem fornecer comandos, atalhos, integrações ou convenções locais.
- Se um adapter estiver ausente, use o comportamento neutro documentado no workflow.

## Exemplos (opcionais, por repositório)

| Tipo de adapter | Propósito |
|--------------|-----------|
| Publicador de issue tracker | GitHub, GitLab, Jira, Linear, etc. |
| Leitor de docs do repo | Docs internas ou geradas, antes de busca ampla |
| Padrões da organização | Políticas de linguagem ou segurança além dos arquivos do repo |
| IDE / agent host | `.cursor/`, `.github/`, registro de skills específico do host |
| Arquivo de agent host | `AGENTS.md`, `CLAUDE.md` — espelho opcional do `AI_WORKFLOW.md` |

## Defaults neutros

Quando nenhum adapter está configurado:

- Escreva specs e planos no **local de planejamento configurado**, ou em `.scratch/<feature>/` como fallback local.
- Leia os padrões do repo em `AI_WORKFLOW.md`, `docs/ai-workflow/*`, `CONTRIBUTING.md`, `STYLE.md`, ADRs.
- Faça review com as checklists do `code-review` só sob demanda.
