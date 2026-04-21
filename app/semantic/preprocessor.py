from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher

from app.semantic.prompts import KGVocabulary
from app.semantic.schemas import (
    AppliedCorrection,
    CorrectionType,
    PreprocessResult,
)

logger = logging.getLogger(__name__)

MIN_WORD_LENGTH_FOR_SPELLING = 4
SAFE_SIMILARITY_THRESHOLD = 0.90


class Preprocessor:
    """
    Pré-traitement minimal 

    Règles :
      - normalisation légère des espaces
      - remplacement des synonymes exacts du KG
      - correction orthographique ultra-safe uniquement
      - aucune correction sémantique agressive
    """

    def __init__(self, vocab: KGVocabulary):
        self._vocab = vocab
        self._search_index: dict[str, tuple[str, str]] = {}
        self._single_word_candidates: dict[str, tuple[str, str]] = {}
        self._build_search_index()

        logger.info(
            "Preprocessor initialisé — %d entrées index, %d candidats single-word",
            len(self._search_index),
            len(self._single_word_candidates),
        )

    def _build_search_index(self) -> None:
        for bt in self._vocab.business_terms:
            norm = self._normalize(bt)
            self._search_index[norm] = (bt, "BusinessTerm")
            if " " not in norm:
                self._single_word_candidates[norm] = (bt, "BusinessTerm")

        for ent in self._vocab.entities:
            norm = self._normalize(ent)
            self._search_index[norm] = (ent, "Entity")
            if " " not in norm:
                self._single_word_candidates[norm] = (ent, "Entity")

        for tp in self._vocab.time_periods:
            norm = self._normalize(tp)
            self._search_index[norm] = (tp, "TimePeriod")
            if " " not in norm:
                self._single_word_candidates[norm] = (tp, "TimePeriod")

        for m in self._vocab.metrics:
            norm = self._normalize(m)
            self._search_index[norm] = (m, "Metric")
            if " " not in norm:
                self._single_word_candidates[norm] = (m, "Metric")

        for syn, canonical in self._vocab.synonyms.items():
            norm_syn = self._normalize(syn)
            category = self._find_category(canonical)
            self._search_index[norm_syn] = (canonical, category)
            if " " not in norm_syn:
                self._single_word_candidates[norm_syn] = (canonical, category)

    def _find_category(self, canonical_term: str) -> str:
        norm = self._normalize(canonical_term)

        for bt in self._vocab.business_terms:
            if self._normalize(bt) == norm:
                return "BusinessTerm"
        for ent in self._vocab.entities:
            if self._normalize(ent) == norm:
                return "Entity"
        for tp in self._vocab.time_periods:
            if self._normalize(tp) == norm:
                return "TimePeriod"
        for m in self._vocab.metrics:
            if self._normalize(m) == norm:
                return "Metric"

        return "Unknown"

    def preprocess(self, question: str) -> PreprocessResult:
        if not question or not question.strip():
            return PreprocessResult(
                original_question=question or "",
                corrected_question=question or "",
            )

        original = question.strip()
        working = self._normalize_spaces(original)
        corrections: list[AppliedCorrection] = []

        working, syn_corr = self._replace_exact_synonyms(working)
        corrections.extend(syn_corr)

        working, typo_corr = self._safe_spelling_pass(working, corrections)
        corrections.extend(typo_corr)

        return PreprocessResult(
            original_question=original,
            corrected_question=working,
            corrections=corrections,
            is_corrected=bool(corrections),
        )

    def _replace_exact_synonyms(
        self,
        text: str,
    ) -> tuple[str, list[AppliedCorrection]]:
        corrections: list[AppliedCorrection] = []
        updated = text

        synonym_items = sorted(
            self._vocab.synonyms.items(),
            key=lambda x: len(x[0]),
            reverse=True,
        )

        for syn, canonical in synonym_items:
            pattern = re.compile(rf"\b{re.escape(syn)}\b", flags=re.IGNORECASE)
            matches = list(pattern.finditer(updated))
            if not matches:
                continue

            updated = pattern.sub(canonical, updated)
            for match in matches:
                corrections.append(
                    AppliedCorrection(
                        original=match.group(0),
                        corrected=canonical,
                        correction_type=CorrectionType.SYNONYM,
                        confidence=1.0,
                        matched_term=canonical,
                    )
                )

        return self._normalize_spaces(updated), corrections

    def _safe_spelling_pass(
        self,
        text: str,
        existing_corrections: list[AppliedCorrection],
    ) -> tuple[str, list[AppliedCorrection]]:
        corrections: list[AppliedCorrection] = []
        already_corrected = {self._normalize(c.original) for c in existing_corrections}

        words = text.split()
        new_words: list[str] = []

        for word in words:
            norm_word = self._normalize(word)

            if (
                len(norm_word) < MIN_WORD_LENGTH_FOR_SPELLING
                or norm_word in already_corrected
                or norm_word in self._search_index
                or self._looks_like_number_or_date(norm_word)
            ):
                new_words.append(word)
                continue

            best = self._find_safe_spelling_candidate(norm_word)
            if best is None:
                new_words.append(word)
                continue

            corrected_display, canonical, score = best
            new_words.append(corrected_display)
            corrections.append(
                AppliedCorrection(
                    original=word,
                    corrected=corrected_display,
                    correction_type=CorrectionType.SPELLING,
                    confidence=round(score, 3),
                    matched_term=canonical,
                )
            )

            logger.info(
                "Correction orthographique sûre : '%s' → '%s' (%.1f%%)",
                word,
                corrected_display,
                score * 100,
            )

        return " ".join(new_words), corrections

    def _find_safe_spelling_candidate(
        self,
        norm_word: str,
    ) -> tuple[str, str, float] | None:
        best_score = 0.0
        best_candidate: tuple[str, str, float] | None = None

        for candidate_norm, (canonical, _category) in self._single_word_candidates.items():
            if not self._is_safe_spelling_candidate(norm_word, candidate_norm):
                continue

            score = SequenceMatcher(None, norm_word, candidate_norm).ratio()
            if score >= SAFE_SIMILARITY_THRESHOLD and score > best_score:
                display = self._get_display_form(candidate_norm)
                best_score = score
                best_candidate = (display, canonical, score)

        return best_candidate

    def _is_safe_spelling_candidate(self, source: str, target: str) -> bool:
        """
        Barrière anti-correction sémantique.
        On n'autorise que les corrections très proches lexicalement.
        """
        if source == target:
            return False

        if abs(len(source) - len(target)) > 1:
            return False

        if source[0] != target[0]:
            return False

        if self._edit_distance_over_1(source, target):
            return False

        return True

    @staticmethod
    def _edit_distance_over_1(a: str, b: str) -> bool:
        """
        Retourne True si la distance d'édition est > 1.
        Version légère suffisante pour filtrer les corrections agressives.
        """
        if a == b:
            return False

        la, lb = len(a), len(b)
        if abs(la - lb) > 1:
            return True

        i = j = edits = 0
        while i < la and j < lb:
            if a[i] == b[j]:
                i += 1
                j += 1
                continue

            edits += 1
            if edits > 1:
                return True

            if la > lb:
                i += 1
            elif lb > la:
                j += 1
            else:
                i += 1
                j += 1

        if i < la or j < lb:
            edits += 1

        return edits > 1

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.lower().strip()
        text = text.replace("_", " ")
        nfkd = unicodedata.normalize("NFKD", text)
        text = "".join(c for c in nfkd if not unicodedata.combining(c))
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _normalize_spaces(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip())

    @staticmethod
    def _looks_like_number_or_date(text: str) -> bool:
        return bool(re.fullmatch(r"[\d\-/:.]+", text))

    def _get_display_form(self, norm_form: str) -> str:
        for bt in self._vocab.business_terms:
            if self._normalize(bt) == norm_form:
                return bt
        for ent in self._vocab.entities:
            if self._normalize(ent) == norm_form:
                return ent
        for tp in self._vocab.time_periods:
            if self._normalize(tp) == norm_form:
                return tp
        for m in self._vocab.metrics:
            if self._normalize(m) == norm_form:
                return m
        for syn in self._vocab.synonyms:
            if self._normalize(syn) == norm_form:
                return syn
        return norm_form