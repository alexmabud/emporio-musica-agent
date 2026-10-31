"""Behavior tests for turning the policy manual PDF into clean prompt text."""

import re
from pathlib import Path

import pytest

from emporio_agent.policies import load_policy_text

PDF_PATH = Path(__file__).resolve().parents[1] / "data" / "politicas_da_loja.pdf"


@pytest.fixture(scope="module")
def policy() -> str:
    """Clean text of the real policy manual."""
    return load_policy_text(PDF_PATH)


def _flat(text: str) -> str:
    """Collapse table separators and line breaks so a fact can be searched as one sentence."""
    return re.sub(r"\s*\|\s*|\s+", " ", text)


@pytest.mark.parametrize(
    "fact",
    [
        "Segunda a Sexta-feira 09:00 às 18:00",
        "Sábado 09:00 às 13:00",
        "Domingo e Feriados Fechado",
        "Rua 14 de Maio, 3200",
        "até 7 (sete) dias corridos após o recebimento",
        "5% de desconto sobre o preço de tabela",
        "Frete grátis para pedidos acima de R$ 500,00",
        "BR seguido de 9 caracteres alfanuméricos e BR",
        "Prazo de retorno: 24 horas úteis",
    ],
)
def test_the_manual_facts_the_agent_needs_are_readable(policy: str, fact: str) -> None:
    """Hours, address, return window, PIX, shipping, tracking format and complaint deadline."""
    assert fact in _flat(policy)


def test_page_furniture_is_removed(policy: str) -> None:
    """Footers ('Página 3') and the header repeated on every page add noise, not policy."""
    assert not re.search(r"^\s*Página \d+\s*$", policy, re.MULTILINE)
    assert "Empório da Música Manual de Políticas e Procedimentos" not in policy
    assert "\n\n\n" not in policy


def test_every_numbered_section_title_is_on_its_own_line(policy: str) -> None:
    """The ten sections keep their titles as lines, so the model can tell topics apart."""
    lines = policy.splitlines()
    for title in [
        "1. Sobre a Empório da Música",
        "2. Horário de Funcionamento",
        "3. Formas de Pagamento",
        "4. Política de Trocas e Devoluções",
        "5. Política de Frete e Entregas",
        "6. Promoções e Descontos",
        "7. Atendimento via WhatsApp",
        "8. Garantia",
        "9. Privacidade e Proteção de Dados",
        "10. Disposições Finais",
    ]:
        assert title in lines


def test_table_columns_are_separated_by_a_bar(policy: str) -> None:
    """A table row reads as 'Forma | Condição | Observação' instead of far-apart columns."""
    assert "Cartão de Débito | Pagamento à vista | Todas as bandeiras aceitas" in policy
    assert "Segunda a Sexta-feira | 09:00 às 18:00" in policy


def test_wrapped_lines_are_joined_back_into_one(policy: str) -> None:
    """A table cell or bullet that wrapped onto a second line reads as a single line again."""
    lines = policy.splitlines()
    assert "PIX | Pagamento à vista | 5% de desconto sobre o preço de tabela" in lines
    assert any(
        line.endswith("sem valor mínimo de parcela (exceto abaixo de R$ 50,00).") for line in lines
    )


def test_known_typos_are_fixed_and_phones_stay_whole(policy: str) -> None:
    """The e-mail written with an accent is corrected; both phone numbers stay readable."""
    assert "contato@emporiodamusica.com.br" in policy
    assert "empóriodamusica" not in policy
    assert "(67) 3341-4444" in policy
    assert "(67) 3321-4500" in policy


def test_subsection_titles_stay_apart_from_their_bullets(policy: str) -> None:
    """A '3.1 ...' title is a line of its own and each bullet becomes a '- ' line under it."""
    lines = policy.splitlines()
    for title in [
        "3.1 Regras de Parcelamento",
        "4.1 Direito de Arrependimento (Compras Online)",
        "5.1 Entregas na Região Metropolitana de Campo Grande",
        "7.3 Situações Especiais",
    ]:
        assert title in lines
    assert "- Parcelamento de 4x a 6x: sem juros, parcela mínima de R$ 80,00." in lines
    assert "•" not in policy


def test_numbered_steps_are_separate_lines_under_their_title(policy: str) -> None:
    """The five steps of the standard service flow (7.2) read as '1. ...' ... '5. ...' lines."""
    lines = policy.splitlines()
    assert "7.2 Fluxo de Atendimento Padrão" in lines
    greeting = "cumprimentar o cliente pelo nome (se disponível) e perguntar como pode ajudar."
    assert f"1. Saudação: {greeting}" in lines
    step = r"^[1-5]\. (Saudação|Entendimento|Consulta|Resposta|Fechamento):"
    assert len([line for line in lines if re.match(step, line)]) == 5
