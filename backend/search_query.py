"""A database-neutral model of Boolean search strings.

PubMed strings are parsed into a tree (PubMed evaluates operators left to right) and rendered for other databases with
explicit parentheses, so the logic survives translation. Anything that can't be carried across, such as MeSH headings
in databases with their own vocabularies, is reported as a warning rather than dropped silently.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

FieldName = Literal[
    "all",
    "title",
    "abstract",
    "title_abstract",
    "text_word",
    "heading",
    "heading_noexp",
    "major_heading",
    "publication_type",
]
HEADING_FIELDS = {"heading", "heading_noexp", "major_heading"}


@dataclass
class Term:
    text: str
    field: FieldName = "all"
    phrase: bool = False


@dataclass
class Near:
    """Words within `distance` words of each other, as in PubMed's "aspirin bleeding"[tiab:~3]."""

    words: list[str]
    distance: int
    field: FieldName = "title_abstract"


@dataclass
class Group:
    operator: Literal["AND", "OR", "NOT"]
    children: list["Node"]


Node = Term | Near | Group


class QuerySyntaxError(Exception):
    """The search string can't be parsed. The message is safe to show users."""


_PUBMED_FIELDS: dict[str, FieldName] = {
    "all": "all",
    "all fields": "all",
    "tiab": "title_abstract",
    "title/abstract": "title_abstract",
    "ti": "title",
    "title": "title",
    "ab": "abstract",
    "abstract": "abstract",
    "tw": "text_word",
    "text word": "text_word",
    "mh": "heading",
    "mesh": "heading",
    "mesh terms": "heading",
    "mh:noexp": "heading_noexp",
    "mesh:noexp": "heading_noexp",
    "majr": "major_heading",
    "mesh major topic": "major_heading",
    "pt": "publication_type",
    "publication type": "publication_type",
}

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<lparen>\() |
        (?P<rparen>\)) |
        "(?P<phrase>[^"]*)"(?:\[(?P<phrase_tag>[^\]]*)\])? |
        (?P<word>[^\s()"\[\]]+)(?:\[(?P<word_tag>[^\]]*)\])?
    )""",
    re.VERBOSE,
)


@dataclass
class _Token:
    kind: Literal["(", ")", "OP", "TERM"]
    value: str | Node
    position: int


def _field(tag: str | None) -> tuple[FieldName, int | None]:
    if tag is None:
        return "all", None
    name = tag.strip().lower()
    proximity = re.fullmatch(r"(tiab|ti|ab):~(\d+)", name)
    if proximity:
        return _PUBMED_FIELDS[proximity.group(1)], int(proximity.group(2))
    if name not in _PUBMED_FIELDS:
        raise QuerySyntaxError(f"Unknown PubMed field tag [{tag}]")
    return _PUBMED_FIELDS[name], None


def _tokenize(query: str) -> list[_Token]:
    tokens: list[_Token] = []
    position = 0
    while position < len(query):
        if not query[position:].strip():
            break
        match = _TOKEN.match(query, position)
        if match is None or match.end() == position:
            character = query[position:].lstrip()[:1]
            if character == '"':
                raise QuerySyntaxError("A quotation mark isn't closed")
            if character == "[":
                raise QuerySyntaxError("A field tag appears without a term before it")
            raise QuerySyntaxError(f"Unexpected character {character!r}")
        start = match.start() + (len(match.group(0)) - len(match.group(0).lstrip()))
        if match.group("lparen"):
            tokens.append(_Token("(", "(", start))
        elif match.group("rparen"):
            tokens.append(_Token(")", ")", start))
        elif match.group("phrase") is not None:
            text = " ".join(match.group("phrase").split())
            if not text:
                raise QuerySyntaxError("Empty quotation marks")
            field, distance = _field(match.group("phrase_tag"))
            node: Node = Near(text.split(), distance, field) if distance is not None else Term(text, field, True)
            tokens.append(_Token("TERM", node, start))
        else:
            word = match.group("word")
            if word in ("AND", "OR", "NOT"):
                if match.group("word_tag") is not None:
                    raise QuerySyntaxError(f"{word} can't have a field tag")
                tokens.append(_Token("OP", word, start))
            else:
                field, distance = _field(match.group("word_tag"))
                if distance is not None:
                    raise QuerySyntaxError("Proximity searching needs a quoted phrase")
                tokens.append(_Token("TERM", Term(word, field), start))
        position = match.end()
    return tokens


def _combine(operator: Literal["AND", "OR", "NOT"], left: Node, right: Node) -> Node:
    if operator != "NOT" and isinstance(left, Group) and left.operator == operator:
        left.children.append(right)
        return left
    return Group(operator, [left, right])


def _parse_operand(tokens: list[_Token], index: int) -> tuple[Node, int]:
    if index >= len(tokens):
        raise QuerySyntaxError("The search string ends too early")
    token = tokens[index]
    if token.kind == "(":
        if index + 1 < len(tokens) and tokens[index + 1].kind == ")":
            raise QuerySyntaxError("Empty parentheses")
        node, index = _parse_sequence(tokens, index + 1)
        if index >= len(tokens) or tokens[index].kind != ")":
            raise QuerySyntaxError("A parenthesis isn't closed")
        return (Group(node.operator, node.children) if isinstance(node, Group) else node), index + 1
    if token.kind == "TERM":
        assert not isinstance(token.value, str)
        return token.value, index + 1
    if token.kind == "OP":
        raise QuerySyntaxError(f"{token.value} needs a term before it")
    raise QuerySyntaxError("A closing parenthesis has no opening parenthesis")


def _parse_sequence(tokens: list[_Token], index: int) -> tuple[Node, int]:
    node, index = _parse_operand(tokens, index)
    while index < len(tokens) and tokens[index].kind != ")":
        operator: Literal["AND", "OR", "NOT"] = "AND"
        if tokens[index].kind == "OP":
            operator = tokens[index].value  # type: ignore[assignment]
            index += 1
            if index >= len(tokens) or tokens[index].kind in (")", "OP"):
                raise QuerySyntaxError(f"{operator} needs a term after it")
        right, index = _parse_operand(tokens, index)
        # A parenthesized group stays a separate node, so its logic isn't merged with the outer operator.
        node = _combine(operator, node, right) if not isinstance(right, Group) else Group(operator, [node, right])
    return node, index


def parse_pubmed(query: str) -> Node:
    tokens = _tokenize(query)
    if not tokens:
        raise QuerySyntaxError("The search string is empty")
    node, index = _parse_sequence(tokens, 0)
    if index < len(tokens):
        raise QuerySyntaxError("A closing parenthesis has no opening parenthesis")
    return node


# Rendering


Warnings = list[str]


@dataclass(frozen=True)
class Syntax:
    key: str
    label: str
    databases: str
    term: Callable[[Term, Warnings], str]
    near: Callable[[Near, Warnings], str]
    not_operator: str = "NOT"
    ignores_operators: bool = False


def _quoted(term: Term, quote: str = '"') -> str:
    return f"{quote}{term.text}{quote}" if term.phrase or " " in term.text else term.text


def _pubmed_term(term: Term, warnings: Warnings) -> str:
    tags = {
        "all": "",
        "title": "[ti]",
        "abstract": "[ab]",
        "title_abstract": "[tiab]",
        "text_word": "[tw]",
        "heading": "[mh]",
        "heading_noexp": "[mh:noexp]",
        "major_heading": "[majr]",
        "publication_type": "[pt]",
    }
    return _quoted(term) + tags[term.field]


def _pubmed_near(near: Near, warnings: Warnings) -> str:
    tag = {"title": "ti", "abstract": "ab"}.get(near.field, "tiab")
    return f'"{" ".join(near.words)}"[{tag}:~{near.distance}]'


def _heading_warning(warnings: Warnings, term: Term, vocabulary: str) -> None:
    warnings.append(
        f'MeSH heading "{term.text}" needs checking against {vocabulary}; headings differ between vocabularies.'
    )


def _ovid_term(embase: bool) -> Callable[[Term, Warnings], str]:
    def render(term: Term, warnings: Warnings) -> str:
        if term.field in HEADING_FIELDS:
            if embase:
                _heading_warning(warnings, term, "Emtree")
            if term.field == "heading_noexp":
                return f"{term.text}/"
            return f"exp *{term.text}/" if term.field == "major_heading" else f"exp {term.text}/"
        if term.field == "publication_type" and embase:
            warnings.append(
                f'Publication type "{term.text}" uses MEDLINE types; Embase uses its own publication types.'
            )
        suffix = {
            "all": ".mp.",
            "title": ".ti.",
            "abstract": ".ab.",
            "title_abstract": ".ti,ab.",
            "text_word": ".tw.",
            "publication_type": ".pt.",
        }[term.field]
        return _quoted(term) + suffix

    return render


def _ovid_near(near: Near, warnings: Warnings) -> str:
    suffix = {"title": ".ti.", "abstract": ".ab."}.get(near.field, ".ti,ab.")
    return f"({f' adj{near.distance} '.join(near.words)}){suffix}"


def _embase_term(term: Term, warnings: Warnings) -> str:
    if term.field in HEADING_FIELDS:
        _heading_warning(warnings, term, "Emtree")
        return f"'{term.text}'/" + {"heading": "exp", "heading_noexp": "de", "major_heading": "mj"}[term.field]
    if term.field == "publication_type":
        warnings.append(
            f'Publication type "{term.text}" was searched as a keyword; use Embase publication type limits.'
        )
        return _quoted(term, "'")
    suffix = {"all": "", "title": ":ti", "abstract": ":ab", "title_abstract": ":ti,ab", "text_word": ":ti,ab,kw"}
    return _quoted(term, "'") + suffix[term.field]


def _embase_near(near: Near, warnings: Warnings) -> str:
    return f"({f' NEAR/{near.distance} '.join(near.words)}):ti,ab"


def _scopus_term(term: Term, warnings: Warnings) -> str:
    if term.field in HEADING_FIELDS:
        warnings.append(f'"{term.text}" was a MeSH heading; Scopus has no heading explosion, so INDEXTERMS is used.')
        return f'INDEXTERMS("{term.text}")'
    if term.field == "publication_type":
        warnings.append(f'Publication type "{term.text}" was searched as a keyword; use Scopus document type limits.')
    function = {"title": "TITLE", "abstract": "ABS"}.get(term.field, "TITLE-ABS-KEY")
    return f"{function}({_quoted(term)})"


def _scopus_near(near: Near, warnings: Warnings) -> str:
    return f"TITLE-ABS-KEY({f' W/{near.distance} '.join(near.words)})"


def _wos_term(term: Term, warnings: Warnings) -> str:
    if term.field in HEADING_FIELDS:
        warnings.append(
            f'"{term.text}" was a MeSH heading; Web of Science has no heading index, so it is a topic word.'
        )
    if term.field == "publication_type":
        warnings.append(f'Publication type "{term.text}" was searched as a topic word; use document type limits.')
    tag = {"title": "TI", "abstract": "AB"}.get(term.field, "TS")
    return f"{tag}=({_quoted(term)})"


def _wos_near(near: Near, warnings: Warnings) -> str:
    return f"TS=({f' NEAR/{near.distance} '.join(near.words)})"


def _ebsco_term(term: Term, warnings: Warnings) -> str:
    text = _quoted(term)
    if term.field in HEADING_FIELDS:
        _heading_warning(warnings, term, "CINAHL Headings or the APA Thesaurus")
        return {
            "heading": f'(MH "{term.text}+")',
            "heading_noexp": f'(MH "{term.text}")',
            "major_heading": f'(MM "{term.text}")',
        }[term.field]
    if term.field == "publication_type":
        warnings.append(f'Publication type "{term.text}" uses MEDLINE types; check the database\'s own types.')
        return f"PT {text}"
    return {"title": f"TI {text}", "abstract": f"AB {text}", "title_abstract": f"(TI {text} OR AB {text})"}.get(
        term.field, text
    )


def _ebsco_near(near: Near, warnings: Warnings) -> str:
    words = f" N{near.distance} ".join(near.words)
    return {"title": f"TI ({words})", "abstract": f"AB ({words})"}.get(near.field, f"(TI ({words}) OR AB ({words}))")


def _cochrane_term(term: Term, warnings: Warnings) -> str:
    if term.field in HEADING_FIELDS:
        if term.field == "major_heading":
            warnings.append(
                f'"{term.text}" was a major-topic heading; the Cochrane Library can\'t restrict to major topics.'
            )
        return f'[mh ^"{term.text}"]' if term.field == "heading_noexp" else f'[mh "{term.text}"]'
    if term.field == "publication_type":
        warnings.append(f'Publication type "{term.text}" was searched as a keyword.')
    suffix = {"title": ":ti", "abstract": ":ab", "title_abstract": ":ti,ab,kw", "text_word": ":ti,ab,kw"}
    return _quoted(term) + suffix.get(term.field, "")


def _cochrane_near(near: Near, warnings: Warnings) -> str:
    return f"({f' NEAR/{near.distance} '.join(near.words)}):ti,ab,kw"


def _europepmc_term(term: Term, warnings: Warnings) -> str:
    text = _quoted(term)
    if term.field in HEADING_FIELDS:
        if term.field != "heading":
            warnings.append(f'"{term.text}": Europe PMC can\'t control heading explosion or major topics.')
        return f'MESH_HEADING:"{term.text}"'
    if term.field == "publication_type":
        return f'PUB_TYPE:"{term.text}"'
    return {
        "title": f"TITLE:{text}",
        "abstract": f"ABSTRACT:{text}",
        "title_abstract": f"(TITLE:{text} OR ABSTRACT:{text})",
    }.get(term.field, text)


def _europepmc_near(near: Near, warnings: Warnings) -> str:
    warnings.append(
        f'Proximity "{" ".join(near.words)}" isn\'t translated for Europe PMC; the words are combined with AND.'
    )
    return "(" + " AND ".join(near.words) + ")"


def _keyword_term(label: str) -> Callable[[Term, Warnings], str]:
    def render(term: Term, warnings: Warnings) -> str:
        if term.field != "all":
            warnings.append(f'{label} has no field tags or subject headings; "{term.text}" is searched as a keyword.')
        return _quoted(term)

    return render


def _keyword_near(label: str) -> Callable[[Near, Warnings], str]:
    def render(near: Near, warnings: Warnings) -> str:
        warnings.append(f'{label} has no proximity searching; "{" ".join(near.words)}" is searched as a phrase.')
        return f'"{" ".join(near.words)}"'

    return render


SYNTAXES: dict[str, Syntax] = {
    syntax.key: syntax
    for syntax in (
        Syntax("pubmed", "PubMed", "PubMed / MEDLINE", _pubmed_term, _pubmed_near),
        Syntax("ovid_medline", "Ovid MEDLINE", "MEDLINE via Ovid", _ovid_term(embase=False), _ovid_near),
        Syntax("ovid_embase", "Ovid Embase", "Embase via Ovid", _ovid_term(embase=True), _ovid_near),
        Syntax("embase_com", "Embase.com", "Embase via Embase.com", _embase_term, _embase_near),
        Syntax("scopus", "Scopus", "Scopus", _scopus_term, _scopus_near, not_operator="AND NOT"),
        Syntax("wos", "Web of Science", "Web of Science Core Collection", _wos_term, _wos_near),
        Syntax("ebsco", "EBSCOhost", "CINAHL, APA PsycInfo via EBSCOhost", _ebsco_term, _ebsco_near),
        Syntax("cochrane", "Cochrane Library", "Cochrane CENTRAL", _cochrane_term, _cochrane_near),
        Syntax("europepmc", "Europe PMC", "Europe PMC", _europepmc_term, _europepmc_near),
        Syntax("openalex", "OpenAlex", "OpenAlex", _keyword_term("OpenAlex"), _keyword_near("OpenAlex")),
        Syntax(
            "crossref",
            "Crossref",
            "Crossref",
            _keyword_term("Crossref"),
            _keyword_near("Crossref"),
            ignores_operators=True,
        ),
        Syntax(
            "semantic_scholar",
            "Semantic Scholar",
            "Semantic Scholar",
            _keyword_term("Semantic Scholar"),
            _keyword_near("Semantic Scholar"),
            ignores_operators=True,
        ),
    )
}

DATABASE_SYNTAXES = {
    "pubmed": "pubmed",
    "medline": "pubmed",
    "medline pubmed": "pubmed",
    "medline ovid": "ovid_medline",
    "ovid medline": "ovid_medline",
    "embase": "embase_com",
    "embase com": "embase_com",
    "ovid embase": "ovid_embase",
    "embase ovid": "ovid_embase",
    "scopus": "scopus",
    "web of science": "wos",
    "wos": "wos",
    "cinahl": "ebsco",
    "psycinfo": "ebsco",
    "apa psycinfo": "ebsco",
    "cochrane": "cochrane",
    "cochrane central": "cochrane",
    "cochrane library": "cochrane",
    "central": "cochrane",
    "europe pmc": "europepmc",
    "europepmc": "europepmc",
    "openalex": "openalex",
    "crossref": "crossref",
    "semantic scholar": "semantic_scholar",
}


def syntax_for_database(database: str) -> str | None:
    return DATABASE_SYNTAXES.get(re.sub(r"[^a-z0-9]+", " ", database.lower()).strip())


def render(node: Node, syntax_key: str) -> tuple[str, list[str]]:
    syntax = SYNTAXES[syntax_key]
    warnings: Warnings = []

    def walk(item: Node, top: bool) -> str:
        if isinstance(item, Term):
            return syntax.term(item, warnings)
        if isinstance(item, Near):
            return syntax.near(item, warnings)
        joiner = f" {syntax.not_operator if item.operator == 'NOT' else item.operator} "
        text = joiner.join(walk(child, False) for child in item.children)
        return text if top else f"({text})"

    query = walk(node, True)
    if syntax.ignores_operators:
        warnings.append(f"{syntax.label} ranks results by relevance and ignores Boolean operators.")
    return query, list(dict.fromkeys(warnings))


def translate_pubmed(query: str, target: str) -> tuple[str, list[str]]:
    if target not in SYNTAXES:
        raise QuerySyntaxError(f"Unknown search syntax: {target}")
    return render(parse_pubmed(query), target)


def count_terms(node: Node) -> int:
    if isinstance(node, Group):
        return sum(count_terms(child) for child in node.children)
    return 1


def _structural_errors(query: str, quotes: str) -> list[str]:
    errors = []
    depth, in_quote = 0, ""
    for character in query:
        if in_quote:
            if character == in_quote:
                in_quote = ""
            continue
        if character in quotes:
            in_quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                errors.append("A closing parenthesis has no opening parenthesis")
                depth = 0
    if in_quote:
        errors.append("A quotation mark isn't closed")
    if depth > 0:
        errors.append("A parenthesis isn't closed")
    stripped = re.sub(r"\"[^\"]*\"|'[^']*'", " ", query)
    if re.search(r"\(\s*\)", stripped):
        errors.append("Empty parentheses")
    if re.match(r"^\s*(AND|OR|NOT)\b", stripped) or re.search(r"\b(AND|OR|NOT)\s*$", stripped):
        errors.append("The search string starts or ends with an operator")
    if re.search(r"\b(AND|OR|NOT)\s+(AND|OR|NOT)\b", stripped):
        errors.append("Two operators appear in a row")
    return errors


def validate(query: str, syntax_key: str) -> dict:
    """Syntax errors that would stop the search from running as written, and warnings worth a second look."""
    errors: list[str] = []
    warnings: list[str] = []
    term_count = None
    if not query.strip():
        errors.append("The search string is empty")
    elif syntax_key == "pubmed":
        try:
            term_count = count_terms(parse_pubmed(query))
        except QuerySyntaxError as exc:
            errors.append(str(exc))
        unquoted = re.sub(r'"[^"]*"', " ", query)
        for word in sorted(set(re.findall(r"\b(and|or|not)\b", unquoted))):
            warnings.append(f'"{word}" is searched as a word; PubMed operators must be in capitals.')
        for stem in re.findall(r'(?<![\w"])(\w{1,3})\*', unquoted):
            warnings.append(f'"{stem}*" is too short to truncate; PubMed needs at least four characters before *.')
    else:
        errors = _structural_errors(query, "\"'" if syntax_key in ("embase_com", "ovid_embase") else '"')
    return {"syntax": syntax_key, "valid": not errors, "errors": errors, "warnings": warnings, "term_count": term_count}


def catalog() -> list[dict]:
    return [{"key": s.key, "label": s.label, "databases": s.databases} for s in SYNTAXES.values()]
