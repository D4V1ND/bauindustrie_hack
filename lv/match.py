"""Step 1: the group label that links exact matches."""
import hashlib
import re

from lv.model import LineItem

# The dataset's anonymiser spells one hidden word in several ways: [PERSON_115], [PERSON_0000115], PERSON_115 and,
# squeezed into the fixed-width GAEB 90 lines, PER000115. Kind and number say which word it is; the rest is spelling.
# ponytail: shorter squeezed spellings (P125, F0101, ###) could be real words or have lost their number, so they
# are left alone and such items do not link across file formats.
_HIDDEN = re.compile(r"\[?\b(?:(person|ort|firma|adresse|telefon|projekt|nummer|email|url|kennzeichen)_|per)0*(\d+)\]?")


def clean(text: str) -> str:
    """Removes the differences without meaning: letter case, spacing, line-break hyphens, anonymiser spelling."""
    text = re.sub(r"(\w)- (\w)", r"\1\2", text)  # "We- ge" -> "Wege"
    text = " ".join(text.lower().split())
    return _HIDDEN.sub(lambda hidden: f"[{hidden[1] or 'person'}_{hidden[2]}]", text)


def group_label(item: LineItem) -> str:
    """Same short text, long text and unit give the same label."""
    key = "\x1f".join(clean(text) for text in (item.short_text, item.long_text, item.unit))
    return hashlib.sha1(key.encode()).hexdigest()[:12]
