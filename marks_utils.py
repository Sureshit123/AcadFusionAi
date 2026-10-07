import math


_MISSING_MARK_TOKENS = {"", "-", "–", "—"}


def is_unselected_subject(internal_marks):
    """Return whether the internal-mark field says this subject was not selected."""
    if internal_marks is None:
        return True
    if isinstance(internal_marks, float) and math.isnan(internal_marks):
        return True
    return str(internal_marks).strip() in _MISSING_MARK_TOKENS


def is_missing_external_mark(external_marks):
    """Return whether the external mark is absent or represented by a dash."""
    if external_marks is None:
        return True
    if isinstance(external_marks, float) and math.isnan(external_marks):
        return True
    return str(external_marks).strip() in _MISSING_MARK_TOKENS


def parse_numeric_mark(mark, default=0):
    """Parse a numeric mark while treating missing and non-numeric values as default."""
    if mark is None:
        return default
    try:
        value = float(str(mark).strip())
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return int(value) if value.is_integer() else value


def calculate_subject_total(internal_marks, external_marks, reported_total=None):
    """Return a subject total without allowing a missing external mark to erase IA."""
    if is_unselected_subject(internal_marks):
        return 0

    internal_value = parse_numeric_mark(internal_marks)
    if is_missing_external_mark(external_marks):
        return internal_value

    parsed_total = parse_numeric_mark(reported_total, default=None)
    if parsed_total is not None:
        return parsed_total
    return internal_value + parse_numeric_mark(external_marks)


def calculate_student_mark_totals(subjects):
    """Return (marks secured, maximum marks) for only this student's selected subjects."""
    applicable = [
        subject for subject in subjects.values()
        if not is_unselected_subject(subject.get("internal"))
    ]
    total_marks = sum(
        calculate_subject_total(
            subject.get("internal"),
            subject.get("external"),
            subject.get("total"),
        )
        for subject in applicable
    )
    return total_marks, len(applicable) * 100
