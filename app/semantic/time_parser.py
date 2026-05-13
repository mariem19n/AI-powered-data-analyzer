"""
app/semantic/time_parser.py
Parseur temporel général — résout n'importe quelle expression
temporelle utilisateur en intervalle SQL-ready.

Remplace les TimePeriod canoniques du KG comme mécanisme principal.
Le KG reste optionnel pour les alias métier spéciaux.

Normalisation unique : intervalle [start_date, end_date_exclusive)
  - start_date inclus
  - end_date_exclusive exclu
  - filter_clause prêt pour SQL : date >= DATE '...' AND date < DATE '...'

Catégories supportées :
  1. Dates absolues : "mars 2025", "janvier 2024", "2024", "12 mars 2025"
  2. Plages : "du 1er mars au 15 avril 2025", "entre janvier et mars 2024"
  3. Trimestres : "Q1 2024", "1er trimestre 2024", "T2 2025"
  4. Relatives : "ce mois", "cette année", "30 derniers jours", "hier"
  5. Saisonnières : "été 2025", "hiver 2024", "printemps 2023"
  6. Ouvertes : "depuis janvier 2024", "avant 2024", "après juin 2025"

Usage :
    from app.semantic.time_parser import TimeParser, ParsedTimePeriod

    parser = TimeParser()
    result = parser.parse("mars 2025")
    print(result.filter_clause)
    # → "date >= DATE '2025-03-01' AND date < DATE '2025-04-01'"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)


# ─── Résultat normalisé ───────────────────────────────────────


@dataclass
class ParsedTimePeriod:
    """Période temporelle normalisée en intervalle [start, end)."""

    raw_text: str
    granularity: str  # "day", "week", "month", "quarter", "year", "season", "custom"
    start_date: date
    end_date_exclusive: date
    is_past: bool
    is_future: bool
    is_relative: bool  # True si dépend de la date courante
    is_open_ended: bool = False  # "depuis...", "avant..."
    normalized_label: str = ""
    filter_clause: str = ""

    def __post_init__(self):
        if not self.normalized_label:
            self.normalized_label = f"{self.start_date.isoformat()}:{self.end_date_exclusive.isoformat()}"
        if not self.filter_clause:
            if self.is_open_ended and self.end_date_exclusive == date(2099, 12, 31):
                # "depuis..." → pas de borne supérieure
                self.filter_clause = f"date >= DATE '{self.start_date.isoformat()}'"
            elif self.is_open_ended and self.start_date == date(1970, 1, 1):
                # "avant..." → pas de borne inférieure
                self.filter_clause = f"date < DATE '{self.end_date_exclusive.isoformat()}'"
            else:
                self.filter_clause = (
                    f"date >= DATE '{self.start_date.isoformat()}' "
                    f"AND date < DATE '{self.end_date_exclusive.isoformat()}'"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_text": self.raw_text,
            "granularity": self.granularity,
            "start_date": self.start_date.isoformat(),
            "end_date_exclusive": self.end_date_exclusive.isoformat(),
            "is_past": self.is_past,
            "is_future": self.is_future,
            "is_relative": self.is_relative,
            "is_open_ended": self.is_open_ended,
            "normalized_label": self.normalized_label,
            "filter_clause": self.filter_clause,
        }


# ─── Constantes ───────────────────────────────────────────────

MONTHS_FR = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3,
    "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
    "août": 8, "aout": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "décembre": 12, "decembre": 12,
}

MONTHS_EN = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

ALL_MONTHS = {**MONTHS_FR, **MONTHS_EN}

SEASONS = {
    "printemps": (3, 6),   # mars → mai (exclu juin)
    "spring": (3, 6),
    "été": (6, 9),         # juin → août (exclu septembre)
    "ete": (6, 9),
    "summer": (6, 9),
    "automne": (9, 12),    # septembre → novembre (exclu décembre)
    "autumn": (9, 12),
    "fall": (9, 12),
    "hiver": (12, 3),      # décembre → février (exclu mars)
    "winter": (12, 3),
}

QUARTER_PATTERNS = {
    "q1": (1, 4), "t1": (1, 4), "1er trimestre": (1, 4), "premier trimestre": (1, 4),
    "q2": (4, 7), "t2": (4, 7), "2ème trimestre": (4, 7), "deuxième trimestre": (4, 7),
    "q3": (7, 10), "t3": (7, 10), "3ème trimestre": (7, 10), "troisième trimestre": (7, 10),
    "q4": (10, 1), "t4": (10, 1), "4ème trimestre": (10, 1), "quatrième trimestre": (10, 1),
    "dernier trimestre": None,  # dynamique
}


# ─── Parseur ──────────────────────────────────────────────────


class TimeParser:
    """
    Parseur temporel général.

    Essaie les patterns dans l'ordre de spécificité décroissante :
      1. Date exacte (12 mars 2025)
      2. Plage explicite (du X au Y)
      3. Trimestre (Q1 2024)
      4. Mois + année (mars 2025)
      5. Année seule (2024)
      6. Saison (été 2025)
      7. Relatif (ce mois, 30 derniers jours)
      8. Ouvert (depuis janvier 2024, avant 2024)

    Si aucun pattern ne matche → retourne None (pas d'erreur).
    """

    def __init__(self, reference_date: date | None = None):
        self._ref = reference_date or date.today()

    def set_reference_date(self, ref: date) -> None:
        """Change la date de référence (utile pour les tests)."""
        self._ref = ref

    def parse(self, text: str) -> ParsedTimePeriod | None:
        """
        Parse une expression temporelle.

        Args:
            text: expression brute (ex: "mars 2025", "Q1 2024", "ce mois")

        Returns:
            ParsedTimePeriod normalisé, ou None si non reconnu
        """
        if not text or not text.strip():
            return None

        clean = text.strip().lower()

        # Essayer chaque pattern dans l'ordre
        parsers = [
            self._parse_exact_date,
            self._parse_range,
            self._parse_quarter,
            self._parse_month_year,
            self._parse_year_only,
            self._parse_season,
            self._parse_relative,
            self._parse_open_ended,
        ]

        for parser_fn in parsers:
            result = parser_fn(clean, text)
            if result is not None:
                logger.info(
                    "Time parsed : '%s' → [%s, %s) granularity=%s past=%s",
                    text,
                    result.start_date,
                    result.end_date_exclusive,
                    result.granularity,
                    result.is_past,
                )
                return result

        logger.warning("Time expression non reconnue : '%s'", text)
        return None

    # ─── 1. Date exacte ───────────────────────────────────────

    def _parse_exact_date(self, clean: str, raw: str) -> ParsedTimePeriod | None:
        """Parse "12 mars 2025", "le 5 janvier 2024", "2025-03-12"."""
        # Format ISO
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", clean)
        if m:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return self._make_result(raw, "day", d, d + timedelta(days=1))

        # Format "le 12 mars 2025" ou "12 mars 2025"
        for month_name, month_num in ALL_MONTHS.items():
            pattern = rf"(?:le\s+)?(\d{{1,2}})\s*(?:er\s+)?{month_name}\s+(\d{{4}})"
            m = re.match(pattern, clean)
            if m:
                day = int(m.group(1))
                year = int(m.group(2))
                try:
                    d = date(year, month_num, day)
                    return self._make_result(raw, "day", d, d + timedelta(days=1))
                except ValueError:
                    continue

        return None

    # ─── 2. Plage explicite ───────────────────────────────────

    def _parse_range(self, clean: str, raw: str) -> ParsedTimePeriod | None:
        """Parse "du 1er mars au 15 avril 2025", "entre janvier et mars 2024"."""
        # "du X au Y"
        m = re.match(
            r"du\s+(\d{1,2})(?:er)?\s*(\w+)\s+au\s+(\d{1,2})(?:er)?\s*(\w+)\s+(\d{4})",
            clean,
        )
        if m:
            d1, m1_name, d2, m2_name, year = (
                int(m.group(1)),
                m.group(2),
                int(m.group(3)),
                m.group(4),
                int(m.group(5)),
            )
            m1 = ALL_MONTHS.get(m1_name)
            m2 = ALL_MONTHS.get(m2_name)
            if m1 and m2:
                try:
                    start = date(year, m1, d1)
                    end = date(year, m2, d2) + timedelta(days=1)
                    return self._make_result(raw, "custom", start, end)
                except ValueError:
                    pass

        # "entre janvier et mars 2024"
        m = re.match(
            r"entre\s+(\w+)\s+et\s+(\w+)\s+(\d{4})",
            clean,
        )
        if m:
            m1 = ALL_MONTHS.get(m.group(1))
            m2 = ALL_MONTHS.get(m.group(2))
            year = int(m.group(3))
            if m1 and m2:
                start = date(year, m1, 1)
                end = _next_month(year, m2)
                return self._make_result(raw, "custom", start, end)

        return None

    # ─── 3. Trimestre ─────────────────────────────────────────

    def _parse_quarter(self, clean: str, raw: str) -> ParsedTimePeriod | None:
        """Parse "Q1 2024", "T2 2025", "1er trimestre 2024", "dernier trimestre"."""
        # "Q1 2024", "T2 2025"
        m = re.match(r"^[qt](\d)\s+(\d{4})$", clean)
        if m:
            q = int(m.group(1))
            year = int(m.group(2))
            if 1 <= q <= 4:
                start_month = (q - 1) * 3 + 1
                start = date(year, start_month, 1)
                end = _add_months(start, 3)
                return self._make_result(raw, "quarter", start, end,
                                         label=f"{year}-Q{q}")

        # "1er trimestre 2024", "premier trimestre 2024"
        for pattern_name, months in QUARTER_PATTERNS.items():
            if months is None:
                continue  # "dernier trimestre" handled below
            if pattern_name in clean:
                m = re.search(r"(\d{4})", clean)
                if m:
                    year = int(m.group(1))
                    start = date(year, months[0], 1)
                    if months[1] == 1:  # Q4 → janvier année suivante
                        end = date(year + 1, 1, 1)
                    else:
                        end = date(year, months[1], 1)
                    q_num = {(1, 4): 1, (4, 7): 2, (7, 10): 3, (10, 1): 4}[months]
                    return self._make_result(raw, "quarter", start, end,
                                             label=f"{year}-Q{q_num}")

        # "dernier trimestre" / "last quarter"
        if "dernier trimestre" in clean or "last quarter" in clean:
            current_q = (self._ref.month - 1) // 3
            if current_q == 0:
                start = date(self._ref.year - 1, 10, 1)
                end = date(self._ref.year, 1, 1)
            else:
                start_month = (current_q - 1) * 3 + 1
                start = date(self._ref.year, start_month, 1)
                end = date(self._ref.year, current_q * 3 + 1, 1)
            return self._make_result(raw, "quarter", start, end, is_relative=True)

        # "ce trimestre" / "this quarter"
        if "ce trimestre" in clean or "this quarter" in clean:
            current_q = (self._ref.month - 1) // 3
            start_month = current_q * 3 + 1
            start = date(self._ref.year, start_month, 1)
            end = _add_months(start, 3)
            return self._make_result(raw, "quarter", start, end, is_relative=True)

        return None

    # ─── 4. Mois + année ──────────────────────────────────────

    def _parse_month_year(self, clean: str, raw: str) -> ParsedTimePeriod | None:
        """Parse "mars 2025", "january 2024", "en mars 2025"."""
        # Nettoyer les prépositions
        cleaned = re.sub(r"^(en|in|de|du|au)\s+", "", clean)

        for month_name, month_num in ALL_MONTHS.items():
            # "mars 2025" ou "mars de 2025"
            pattern = rf"^{month_name}\s+(?:de\s+)?(\d{{4}})$"
            m = re.match(pattern, cleaned)
            if m:
                year = int(m.group(1))
                start = date(year, month_num, 1)
                end = _next_month(year, month_num)
                return self._make_result(
                    raw, "month", start, end,
                    label=f"{year}-{month_num:02d}",
                )

        return None

    # ─── 5. Année seule ───────────────────────────────────────

    def _parse_year_only(self, clean: str, raw: str) -> ParsedTimePeriod | None:
        """Parse "2024", "en 2024", "année 2024"."""
        cleaned = re.sub(r"^(en|année|year|l'année)\s+", "", clean)
        m = re.match(r"^(\d{4})$", cleaned)
        if m:
            year = int(m.group(1))
            start = date(year, 1, 1)
            end = date(year + 1, 1, 1)
            return self._make_result(raw, "year", start, end, label=str(year))
        return None

    # ─── 6. Saison ────────────────────────────────────────────

    def _parse_season(self, clean: str, raw: str) -> ParsedTimePeriod | None:
        """Parse "été 2025", "hiver 2024", "spring 2023"."""
        for season_name, (start_m, end_m) in SEASONS.items():
            if season_name in clean:
                m = re.search(r"(\d{4})", clean)
                if m:
                    year = int(m.group(1))
                    if start_m > end_m:
                        # Hiver : décembre année N → mars année N+1
                        start = date(year, start_m, 1)
                        end = date(year + 1, end_m, 1)
                    else:
                        start = date(year, start_m, 1)
                        end = date(year, end_m, 1)
                    return self._make_result(
                        raw, "season", start, end,
                        label=f"{season_name}-{year}",
                    )
        return None

    # ─── 7. Relatif ───────────────────────────────────────────

    def _parse_relative(self, clean: str, raw: str) -> ParsedTimePeriod | None:
        """Parse "ce mois", "cette année", "30 derniers jours", "hier", etc."""
        ref = self._ref

        # "aujourd'hui" / "today"
        if clean in ("aujourd'hui", "today"):
            return self._make_result(
                raw, "day", ref, ref + timedelta(days=1), is_relative=True
            )

        # "hier" / "yesterday"
        if clean in ("hier", "yesterday"):
            d = ref - timedelta(days=1)
            return self._make_result(
                raw, "day", d, d + timedelta(days=1), is_relative=True
            )

        # "cette semaine" / "this week"
        if clean in ("cette semaine", "this week"):
            start = ref - timedelta(days=ref.weekday())
            end = start + timedelta(days=7)
            return self._make_result(raw, "week", start, end, is_relative=True)

        # "la semaine dernière" / "last week"
        if "semaine dernière" in clean or "last week" in clean:
            start = ref - timedelta(days=ref.weekday() + 7)
            end = start + timedelta(days=7)
            return self._make_result(raw, "week", start, end, is_relative=True)

        # "ce mois" / "ce mois-ci" / "this month"
        if clean in ("ce mois", "ce mois-ci", "this month"):
            start = date(ref.year, ref.month, 1)
            end = _next_month(ref.year, ref.month)
            return self._make_result(raw, "month", start, end, is_relative=True)

        # "le mois dernier" / "mois dernier" / "last month"
        if "mois dernier" in clean or "last month" in clean:
            first_this_month = date(ref.year, ref.month, 1)
            end = first_this_month
            start = _prev_month(ref.year, ref.month)
            return self._make_result(raw, "month", start, end, is_relative=True)

        # "cette année" / "this year"
        if clean in ("cette année", "this year"):
            start = date(ref.year, 1, 1)
            end = date(ref.year + 1, 1, 1)
            return self._make_result(raw, "year", start, end, is_relative=True)

        # "l'année dernière" / "année dernière" / "last year"
        if "année dernière" in clean or "last year" in clean:
            start = date(ref.year - 1, 1, 1)
            end = date(ref.year, 1, 1)
            return self._make_result(raw, "year", start, end, is_relative=True)

        # "N derniers jours" / "last N days"
        m = re.match(r"(?:les?\s+)?(\d+)\s+derniers?\s+jours?", clean)
        if not m:
            m = re.match(r"last\s+(\d+)\s+days?", clean)
        if m:
            n = int(m.group(1))
            start = ref - timedelta(days=n)
            end = ref + timedelta(days=1)  # inclure aujourd'hui
            return self._make_result(raw, "custom", start, end, is_relative=True)

        # "N dernières semaines" / "last N weeks"
        m = re.match(r"(?:les?\s+)?(\d+)\s+dernières?\s+semaines?", clean)
        if not m:
            m = re.match(r"last\s+(\d+)\s+weeks?", clean)
        if m:
            n = int(m.group(1))
            start = ref - timedelta(weeks=n)
            end = ref + timedelta(days=1)
            return self._make_result(raw, "custom", start, end, is_relative=True)

        # "N derniers mois" / "last N months"
        m = re.match(r"(?:les?\s+)?(\d+)\s+derniers?\s+mois", clean)
        if not m:
            m = re.match(r"last\s+(\d+)\s+months?", clean)
        if m:
            n = int(m.group(1))
            start = _add_months(date(ref.year, ref.month, 1), -n)
            end = ref + timedelta(days=1)
            return self._make_result(raw, "custom", start, end, is_relative=True)

        return None

    # ─── 8. Ouvert ────────────────────────────────────────────

    def _parse_open_ended(self, clean: str, raw: str) -> ParsedTimePeriod | None:
        """Parse "depuis janvier 2024", "avant 2024", "après juin 2025"."""
        # "depuis mars 2024" / "since march 2024"
        for prefix in ("depuis", "since", "à partir de", "from"):
            if clean.startswith(prefix):
                rest = clean[len(prefix):].strip()
                inner = self.parse(rest)
                if inner:
                    return ParsedTimePeriod(
                        raw_text=raw,
                        granularity=inner.granularity,
                        start_date=inner.start_date,
                        end_date_exclusive=date(2099, 12, 31),
                        is_past=inner.start_date < self._ref,
                        is_future=inner.start_date > self._ref,
                        is_relative=False,
                        is_open_ended=True,
                        normalized_label=f"since-{inner.start_date.isoformat()}",
                    )

        # "avant 2024" / "before 2024"
        for prefix in ("avant", "before"):
            if clean.startswith(prefix):
                rest = clean[len(prefix):].strip()
                inner = self.parse(rest)
                if inner:
                    return ParsedTimePeriod(
                        raw_text=raw,
                        granularity=inner.granularity,
                        start_date=date(1970, 1, 1),
                        end_date_exclusive=inner.start_date,
                        is_past=True,
                        is_future=False,
                        is_relative=False,
                        is_open_ended=True,
                        normalized_label=f"before-{inner.start_date.isoformat()}",
                    )

        # "après juin 2025" / "after june 2025"
        for prefix in ("après", "apres", "after"):
            if clean.startswith(prefix):
                rest = clean[len(prefix):].strip()
                inner = self.parse(rest)
                if inner:
                    return ParsedTimePeriod(
                        raw_text=raw,
                        granularity=inner.granularity,
                        start_date=inner.end_date_exclusive,
                        end_date_exclusive=date(2099, 12, 31),
                        is_past=inner.end_date_exclusive < self._ref,
                        is_future=inner.end_date_exclusive > self._ref,
                        is_relative=False,
                        is_open_ended=True,
                        normalized_label=f"after-{inner.end_date_exclusive.isoformat()}",
                    )

        # "jusqu'à mars 2025" / "until march 2025"
        for prefix in ("jusqu'à", "jusqu'a", "jusqua", "until"):
            if clean.startswith(prefix):
                rest = clean[len(prefix):].strip()
                inner = self.parse(rest)
                if inner:
                    return ParsedTimePeriod(
                        raw_text=raw,
                        granularity=inner.granularity,
                        start_date=date(1970, 1, 1),
                        end_date_exclusive=inner.end_date_exclusive,
                        is_past=True,
                        is_future=False,
                        is_relative=False,
                        is_open_ended=True,
                        normalized_label=f"until-{inner.end_date_exclusive.isoformat()}",
                    )

        return None

    # ─── Helper ───────────────────────────────────────────────

    def _make_result(
        self,
        raw: str,
        granularity: str,
        start: date,
        end: date,
        is_relative: bool = False,
        label: str = "",
    ) -> ParsedTimePeriod:
        return ParsedTimePeriod(
            raw_text=raw,
            granularity=granularity,
            start_date=start,
            end_date_exclusive=end,
            is_past=end <= self._ref,
            is_future=start > self._ref,
            is_relative=is_relative,
            normalized_label=label,
        )


# ─── Utilitaires de date ──────────────────────────────────────


def _next_month(year: int, month: int) -> date:
    """Premier jour du mois suivant."""
    if month == 12:
        return date(year + 1, 1, 1)
    return date(year, month + 1, 1)


def _prev_month(year: int, month: int) -> date:
    """Premier jour du mois précédent."""
    if month == 1:
        return date(year - 1, 12, 1)
    return date(year, month - 1, 1)


def _add_months(d: date, months: int) -> date:
    """Ajoute N mois à une date (premier jour du mois résultant)."""
    month = d.month + months
    year = d.year
    while month > 12:
        month -= 12
        year += 1
    while month < 1:
        month += 12
        year -= 1
    return date(year, month, 1)
