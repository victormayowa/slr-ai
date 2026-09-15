"""Prioritized screening: rank unscreened records by how likely a reviewer is to include them.

The model is TF-IDF features with multinomial naive Bayes and balanced class priors, the default model of ASReview
(van de Schoot et al. 2021, Nature Machine Intelligence 3:125). It is retrained from reviewers' decisions; a ranking
only orders the queue and never decides anything.
"""

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

ALGORITHM = "tfidf-naive-bayes-v1"
# Smoothing used by ASReview's naive Bayes model.
SMOOTHING = 3.822
MIN_DOCUMENT_FREQUENCY = 2

_WORD = re.compile(r"[a-z][a-z0-9]+(?:-[a-z0-9]+)*")
_STOPWORDS = frozenset(
    """a about above after again against all also am an and any are as at be because been before being below between
    both but by can could did do does doing down during each few for from further had has have having he her here hers
    herself him himself his how i if in into is it its itself just me more most my myself no nor not now of off on once
    only or other our ours ourselves out over own same she should so some such than that the their theirs them
    themselves then there these they this those through to too under until up very was we were what when where which
    while who whom why will with would you your yours yourself yourselves""".split()
)


def tokens(text: str) -> list[str]:
    words = [word for word in _WORD.findall(text.lower()) if word not in _STOPWORDS]
    return words + [f"{first} {second}" for first, second in zip(words, words[1:], strict=False)]


@dataclass
class TrainedModel:
    vocabulary: dict[str, int]
    idf: list[float]
    # log P(feature | class) for class 0 (exclude) and class 1 (include)
    log_likelihood: tuple[list[float], list[float]]
    includes: int
    excludes: int

    def score(self, text: str) -> float:
        """Probability of inclusion under the model, between 0 and 1."""
        counts = Counter(term for term in tokens(text) if term in self.vocabulary)
        if not counts:
            return 0.5
        weights = _tfidf(counts, self.vocabulary, self.idf)
        log_exclude = sum(weight * self.log_likelihood[0][index] for index, weight in weights.items())
        log_include = sum(weight * self.log_likelihood[1][index] for index, weight in weights.items())
        difference = log_exclude - log_include
        if difference > 700:
            return 0.0
        return 1 / (1 + math.exp(difference))

    def top_terms(self, limit: int = 15) -> list[str]:
        """Terms that most favour inclusion, to show reviewers what the ranking is based on."""
        by_index = {index: term for term, index in self.vocabulary.items()}
        ratios = sorted(
            range(len(self.idf)),
            key=lambda index: self.log_likelihood[1][index] - self.log_likelihood[0][index],
            reverse=True,
        )
        return [by_index[index] for index in ratios[:limit]]


def _tfidf(counts: Counter[str], vocabulary: dict[str, int], idf: list[float]) -> dict[int, float]:
    weights = {vocabulary[term]: (1 + math.log(count)) * idf[vocabulary[term]] for term, count in counts.items()}
    norm = math.sqrt(sum(weight * weight for weight in weights.values())) or 1.0
    return {index: weight / norm for index, weight in weights.items()}


class NotEnoughDecisions(Exception):
    """The model needs at least one included and one excluded record. The message is safe to show users."""


def train(texts: Sequence[str], labels: Sequence[bool], corpus: Sequence[str]) -> TrainedModel:
    """Train on decided texts; `corpus` (every record, decided or not) sets the vocabulary and IDF weights."""
    includes = sum(labels)
    excludes = len(labels) - includes
    if includes == 0 or excludes == 0:
        raise NotEnoughDecisions("Prioritizing needs at least one included and one excluded record")

    document_frequency: Counter[str] = Counter()
    for text in corpus:
        document_frequency.update(set(tokens(text)))
    minimum = MIN_DOCUMENT_FREQUENCY if len(corpus) >= 20 else 1
    terms = sorted(term for term, frequency in document_frequency.items() if frequency >= minimum)
    vocabulary = {term: index for index, term in enumerate(terms)}
    documents = len(corpus)
    idf = [math.log((1 + documents) / (1 + document_frequency[term])) + 1 for term in terms]

    totals = ([0.0] * len(terms), [0.0] * len(terms))
    for text, label in zip(texts, labels, strict=True):
        counts = Counter(term for term in tokens(text) if term in vocabulary)
        for index, weight in _tfidf(counts, vocabulary, idf).items():
            totals[int(label)][index] += weight
    log_likelihood: list[list[float]] = []
    for class_totals in totals:
        denominator = sum(class_totals) + SMOOTHING * len(terms)
        log_likelihood.append([math.log((value + SMOOTHING) / denominator) for value in class_totals])
    return TrainedModel(vocabulary, idf, (log_likelihood[0], log_likelihood[1]), includes, excludes)
