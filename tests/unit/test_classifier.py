"""Tests for customer message classifier."""

from src.customer.classifier import MessageClassifier


def _make_classifier():
    """Create a classifier without initializing the Anthropic client."""
    return MessageClassifier.__new__(MessageClassifier)


def test_regex_prefilter_tracking():
    """Obvious tracking questions should be pre-filtered without LLM."""
    classifier = _make_classifier()
    # classify is async but regex path is sync — we test via the sync internals
    from src.customer.classifier import OBVIOUS_PATTERNS
    msg = "Where is my order?"
    for msg_type, patterns in OBVIOUS_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(msg):
                assert msg_type == "order_status"
                break


def test_regex_prefilter_shipped():
    """Shipped/tracking questions should match order_status pattern."""
    from src.customer.classifier import OBVIOUS_PATTERNS
    for msg in ["Has my package shipped yet?", "Can I get tracking information?"]:
        matched = False
        for msg_type, patterns in OBVIOUS_PATTERNS.items():
            for pattern in patterns:
                if pattern.search(msg):
                    assert msg_type == "order_status"
                    matched = True
                    break
        assert matched, f"Message not matched: {msg}"


def test_urgency_complaints():
    """Complaints should always be flagged urgent."""
    classifier = _make_classifier()
    assert classifier.is_urgent("complaint", "I'm unhappy with the product")
    assert classifier.is_urgent("complaint", "Everything is fine actually")


def test_urgency_keywords():
    """Messages with damage/refund keywords should be flagged urgent."""
    classifier = _make_classifier()
    assert classifier.is_urgent("general_inquiry", "The bracelet arrived damaged")
    assert classifier.is_urgent("general_inquiry", "I want a refund")
    assert not classifier.is_urgent("general_inquiry", "What sizes do you have?")


def test_urgency_all_keywords():
    """All urgent keywords should trigger the flag."""
    classifier = _make_classifier()
    for keyword in ["damaged", "broken", "wrong", "missing", "refund", "dispute", "angry"]:
        assert classifier.is_urgent("general_inquiry", f"This item is {keyword}"), f"Keyword not caught: {keyword}"


def test_product_schema_loads():
    """Product JSON should load and validate correctly."""
    import json
    from src.product.schema import Product

    with open("data/products/diamond-tennis-bracelet-lab.json") as f:
        product = Product(**json.load(f))

    assert product.sku == "DTB-LAB-7-14KYG"
    assert product.materials.total_carat == 3.0
    assert product.certification is not None
    assert product.certification.grading_lab == "IGI"
    assert len(product.tags) == 13
    assert product.pricing["lab-grown-7inch"].retail == 2850
    assert product.pricing["lab-grown-7inch"].cost == 450
