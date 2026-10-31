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
    return re.sub(r"\s*[|:;]\s*|\s+", " ", text)


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
    assert _flat(fact) in _flat(policy)


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


def test_tables_become_simple_lines(policy: str) -> None:
    """A table row reads as 'Dia: horário'; wide tables keep their column names in the line."""
    lines = policy.splitlines()
    assert "Segunda a Sexta-feira: 09:00 às 18:00" in lines
    assert "Sábado: 09:00 às 13:00" in lines
    assert "Domingo e Feriados: Fechado" in lines
    assert "CNPJ: 12.345.678/0001-90" in lines
    assert "Telefone / WhatsApp: (67) 3341-4444" in lines
    pix = "PIX: Condições Pagamento à vista; Observações 5% de desconto sobre o preço de tabela"
    assert pix in lines
    assert (
        "PAC (Correios): Prazo Estimado 5 a 12 dias úteis; Rastreamento Sim; Seguro Incluído"
        in lines
    )
    assert " | " not in policy
    assert "Dia: Horário" not in policy  # generic header rows add nothing once rows are labelled


def test_wrapped_lines_are_joined_back_into_one(policy: str) -> None:
    """A table cell or bullet that wrapped onto a second line reads as a single line again."""
    lines = policy.splitlines()
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


# What the cleanup may drop, and nothing else: the cover's "uso restrito" line, the footer word
# "Página", and the generic first-column headers of two tables ("Campo | Informação" and
# "Modalidade | Prazo..."), whose rows now start straight with the row name.
# No rule, value or deadline is ever allowed here.
_ALLOWED_DROPS = {"documento", "interno", "uso", "restrito", "página", "informação", "modalidade"}


def _raw_pdf_text() -> str:
    """The PDF text as pypdf extracts it by default (one word per line), for comparison."""
    from pypdf import PdfReader

    return " ".join(page.extract_text() for page in PdfReader(PDF_PATH).pages)


def test_the_cleanup_loses_no_number_and_no_word() -> None:
    """Every figure (hours, days, prices, percents) and every distinct word of the PDF survives."""
    raw = re.sub(r"\s+", " ", _raw_pdf_text())  # pypdf splits words and numbers over lines
    raw = re.sub(r" ?- ?", "-", raw)  # '3341 - 4444' is the phone '3341-4444'
    raw = re.sub(r"Página \d+", " ", raw)  # page numbers are furniture, not policy
    cleaned = load_policy_text(PDF_PATH)

    figure = r"\d[\d.,:/%\-]*\d%?|\d%?"
    raw_figures, kept_figures = re.findall(figure, raw), re.findall(figure, cleaned)
    for token in set(raw_figures):
        assert kept_figures.count(token) >= raw_figures.count(token), f"lost the figure {token!r}"

    word = r"[A-Za-zÀ-ÿ]{3,}"
    raw_words = {w.lower() for w in re.findall(word, raw) if "@" not in w}
    kept_words = {w.lower() for w in re.findall(word, cleaned)}
    assert raw_words - kept_words - _ALLOWED_DROPS - {"empóriodamusica"} == set()


def test_cover_noise_and_redundant_breaks_are_gone(policy: str) -> None:
    """No 'uso restrito' line, paragraphs on one line, and blank lines only between blocks."""
    lines = policy.splitlines()
    assert "Documento interno" not in policy
    assert (
        "Nosso catálogo abrange mais de 300 instrumentos de marcas nacionais e internacionais, "
        "incluindo guitarras, baixos, violões, baterias, teclados, instrumentos de sopro, "
        "cordas orquestrais e ukuleles."
    ) in lines
    assert sum(1 for line in lines if not line) <= 25
    assert "\n\n\n" not in policy


@pytest.mark.parametrize(
    "fact",
    [
        # hours and contacts
        "Domingo e Feriados: Fechado",
        "(67) 3341-4444",
        "(67) 3321-4500",
        "contato@emporiodamusica.com.br",
        # return, exchange and warranty deadlines
        "em até 7 (sete) dias corridos após o recebimento do produto",
        "em até 30 (trinta) dias corridos após a compra",
        "garantia legal de 90 (noventa) dias",
        "em até 10 dias úteis",
        # payment
        "5% de desconto sobre o preço de tabela",
        "Parcelamento em até 3x: sem juros, sem valor mínimo de parcela (exceto abaixo de R$ 50",
        "Parcelamento de 4x a 6x: sem juros, parcela mínima de R$ 80,00",
        "Parcelamento de 7x a 12x: sem juros, parcela mínima de R$ 100,00",
        "permitida (ex.: PIX + cartão) para compras acima de R$ 2.000,00",
        # shipping
        "Frete grátis para pedidos acima de R$ 500,00",
        "taxa fixa de R$ 35,00",
        "Prazo de entrega: 1 a 3 dias úteis",
        # items that cannot be exchanged
        "Instrumentos com personalização ou ajustes sob encomenda (setup, regulagem especial)",
        'Produtos adquiridos em promoções de liquidação com aviso explícito de "venda final"',
        "Boquilhas de instrumentos de sopro, por questões de higiene",
        # service rules
        "Prazo de retorno: 24 horas úteis",
        "nunca fornecer informações sobre preços, estoque ou prazos sem consultar o sistema",
    ],
)
def test_every_critical_fact_survives_the_cleanup(policy: str, fact: str) -> None:
    """Hours, phones, 7/30/90-day deadlines, installments, shipping, PIX, non-exchangeable items."""
    assert _flat(fact) in _flat(policy)
