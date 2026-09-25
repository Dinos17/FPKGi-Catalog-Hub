import re


CATEGORY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_category_name(category):
    if not isinstance(category, str) or not CATEGORY_RE.fullmatch(category):
        raise ValueError(
            "Catalog category names must contain only ASCII letters, digits, "
            "hyphens, and underscores"
        )
    return category
